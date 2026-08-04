from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse
import csv
import json
import re
import time
from typing import Any, Dict, List

from model_client import RemoteChatModel, extract_json


ROOT = Path(__file__).resolve().parents[1]
REFERENCES_DIR = ROOT / "references"
DATA_DIR = REFERENCES_DIR / "data"
BUILD_DIR = REFERENCES_DIR / "build"

SUPERVISORS_INPUT_CSV = DATA_DIR / "supervisors_input_expanded.csv"
OUT_JSON = BUILD_DIR / "supervisor_project_summaries.json"


def norm_text(text: str) -> str:
    """
    Clean small formatting problems from Moodle/PDF copied text.
    Do not rewrite the project content semantically.
    """
    text = text or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove common HTML fragments that appeared in copied Moodle text.
    text = re.sub(r"</?div[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)

    # Fix some common mojibake / PDF extraction artifacts.
    text = text.replace("‚Äì", "–")
    text = text.replace("\u00a0", " ")

    # Keep paragraph breaks, but remove excessive spaces and blank lines.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _norm_key(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def load_existing_summaries(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def save_summaries(path: Path, summaries: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_supervisor_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input CSV: {path}")

    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            supervisor_id = (row.get("supervisor_id") or "").strip()
            name = (
                row.get("name")
                or row.get("supervisor_1_name")
                or ""
            ).strip()
            project_text = norm_text(row.get("project_text") or "")

            if not supervisor_id and not name:
                continue
            if not name:
                print(
                    f"[warn] skip row with missing name: "
                    f"supervisor_id={supervisor_id}"
                )
                continue

            rows.append(
                {
                    "supervisor_id": supervisor_id,
                    "name": name,
                    "project_text": project_text,
                }
            )

    return rows


def split_project_text(
    project_text: str,
    max_chars: int = 5500,
) -> List[str]:
    """
    Most supervisors can be summarised in one call.
    If one supervisor has many projects and the text is too long, split it
    while trying to preserve project boundaries marked by a separator.
    """
    project_text = norm_text(project_text)
    if len(project_text) <= max_chars:
        return [project_text] if project_text else []

    parts = re.split(r"\n\s*-{3,}\s*\n", project_text)
    parts = [part.strip() for part in parts if part.strip()]

    chunks: List[str] = []
    current = ""
    for part in parts:
        if not current:
            current = part
            continue

        if len(current) + len(part) + 8 <= max_chars:
            current = current + "\n\n-----\n" + part
        else:
            chunks.append(current)
            current = part

    if current:
        chunks.append(current)

    # If one single project block is still too long, split by paragraphs.
    final_chunks: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
            continue

        paragraphs = [
            paragraph.strip()
            for paragraph in chunk.split("\n\n")
            if paragraph.strip()
        ]
        current = ""
        for paragraph in paragraphs:
            if not current:
                current = paragraph
            elif len(current) + len(paragraph) + 2 <= max_chars:
                current = current + "\n\n" + paragraph
            else:
                final_chunks.append(current)
                current = paragraph

        if current:
            final_chunks.append(current)

    return final_chunks


def summarize_one_chunk(
    model: RemoteChatModel,
    supervisor_id: str,
    name: str,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """Create one project-grounded summary for one source-text chunk."""
    system = (
        "You prepare comprehensive but compact supervisor project summaries "
        "for Stage 1 of a student-supervisor matching system.\n"
        "Use only the supplied project text. Do not use external knowledge.\n"
        "Do not invent projects, methods, prerequisites, student tasks, "
        "application domains, or suitability claims.\n"
        "Return valid JSON only."
    )

    prompt = (
        f"Supervisor ID: {supervisor_id}\n"
        f"Supervisor name: {name}\n"
        f"Source chunk: {chunk_index + 1} of {total_chunks}\n\n"
        "Complete project text for this source chunk:\n"
        f"{chunk_text}\n\n"
        "Write a single coherent project-grounded summary for Stage 1 matching. "
        "The summary must preserve the distinctions that a student needs in "
        "order to judge fit.\n\n"
        "Cover, when the source supports them:\n"
        "- the main research problem or objective;\n"
        "- the application domain or scientific context;\n"
        "- the main methods, tools, models, data, or experimental approaches;\n"
        "- what the student would actually build, analyse, implement, test, "
        "or investigate;\n"
        "- the likely project style, such as theoretical, coding-heavy, "
        "data-driven, experimental, software-oriented, or interdisciplinary;\n"
        "- prerequisites or expected background, but only when explicitly "
        "stated or unambiguously required by the described work;\n"
        "- the kinds of student interests that would fit the project; and\n"
        "- the kinds of interests that are clearly less suitable because they "
        "fall outside the described topic, domain, or working style.\n\n"
        "If several distinct projects appear, represent every important option "
        "rather than describing only the first one. Ignore contact details, "
        "administrative wording, and reference-list material unless it is "
        "necessary to understand the actual project.\n\n"
        "Length and style:\n"
        "- one paragraph;\n"
        "- approximately 160-190 words;\n"
        "- normally 7-10 complete sentences;\n"
        "- clear natural English, not a keyword list;\n"
        "- do not mention that this is a summary or a source chunk.\n\n"
        "Return JSON only with exactly this schema:\n"
        "{\n"
        '  "summary": "one complete paragraph"\n'
        "}"
    )

    raw = model.chat(
        [{"role": "user", "content": prompt}],
        system=system,
        temperature=0.1,
        num_ctx=8192,
    )
    data = extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("Model output was not a JSON object")

    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ValueError("Model returned an empty summary")
    return summary


def merge_chunk_summaries(
    model: RemoteChatModel,
    supervisor_id: str,
    name: str,
    partial_summaries: List[str],
) -> str:
    """Merge several chunk summaries without dropping distinct projects."""
    system = (
        "You merge project-grounded summaries for Stage 1 of a "
        "student-supervisor matching system.\n"
        "Use only the supplied partial summaries. Do not invent facts.\n"
        "Return valid JSON only."
    )

    prompt = (
        f"Supervisor ID: {supervisor_id}\n"
        f"Supervisor name: {name}\n\n"
        "Partial project summaries:\n"
        f"{json.dumps(partial_summaries, ensure_ascii=False, indent=2)}\n\n"
        "Merge them into one coherent supervisor-level summary. Preserve every "
        "distinct project option and all important differences in research "
        "problem, application domain, methods, student tasks, project style, "
        "prerequisites, suitable interests, and clearly less-suitable interests. "
        "Remove repetition, but do not collapse different domains into vague "
        "phrases such as 'AI' or 'machine learning'.\n\n"
        "Length and style:\n"
        "- one paragraph;\n"
        "- approximately 160-190 words;\n"
        "- normally 7-10 complete sentences;\n"
        "- clear natural English, not a keyword list;\n"
        "- no unsupported claims.\n\n"
        "Return JSON only with exactly this schema:\n"
        "{\n"
        '  "summary": "one complete paragraph"\n'
        "}"
    )

    raw = model.chat(
        [{"role": "user", "content": prompt}],
        system=system,
        temperature=0.1,
        num_ctx=8192,
    )
    data = extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("Model output was not a JSON object")

    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ValueError("Model returned an empty merged summary")
    return summary


def summarize_supervisor(
    model: RemoteChatModel,
    row: Dict[str, str],
    max_chars_per_call: int,
    sleep_seconds: float,
) -> Dict[str, str]:
    supervisor_id = row.get("supervisor_id", "")
    name = row.get("name", "")
    project_text = row.get("project_text", "")

    if not project_text.strip():
        return {
            "name": name,
            "summary": (
                "No project description is available in the local project "
                "database, so a reliable Stage 1 project summary cannot be "
                "produced for this supervisor."
            ),
        }

    chunks = split_project_text(
        project_text,
        max_chars=max_chars_per_call,
    )
    if not chunks:
        raise ValueError(f"No usable project text found for {name}")

    partial_summaries: List[str] = []
    for index, chunk in enumerate(chunks):
        print(
            f"  summarising chunk {index + 1}/{len(chunks)} "
            f"({len(chunk)} chars)"
        )
        partial_summary = summarize_one_chunk(
            model=model,
            supervisor_id=supervisor_id,
            name=name,
            chunk_text=chunk,
            chunk_index=index,
            total_chunks=len(chunks),
        )
        partial_summaries.append(partial_summary)

        if sleep_seconds > 0 and index < len(chunks) - 1:
            time.sleep(sleep_seconds)

    if len(partial_summaries) == 1:
        final_summary = partial_summaries[0]
    else:
        final_summary = merge_chunk_summaries(
            model=model,
            supervisor_id=supervisor_id,
            name=name,
            partial_summaries=partial_summaries,
        )

    return {
        "name": name,
        "summary": final_summary,
    }


def upsert_summary(
    summaries: List[Dict[str, Any]],
    supervisor_id: str,
    name: str,
    new_summary: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Replace an old record in place when possible; otherwise append it."""
    target_name = _norm_key(name)
    for index, item in enumerate(summaries):
        item_id = str(item.get("supervisor_id") or "").strip()
        item_name = _norm_key(str(item.get("name") or ""))
        if (supervisor_id and item_id == supervisor_id) or item_name == target_name:
            summaries[index] = new_summary
            return summaries

    summaries.append(new_summary)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summarise supervisors_input_expanded.csv project_text into "
            "supervisor_project_summaries.json"
        )
    )
    parser.add_argument(
        "--input",
        type=str,
        default=str(SUPERVISORS_INPUT_CSV),
        help="Path to supervisors_input_expanded.csv",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUT_JSON),
        help="Path to output supervisor_project_summaries.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate summaries even if they already exist in the output JSON",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help=(
            "Only process one supervisor by exact supervisor_id or name, "
            "for example s12 or Edina Rosta"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N supervisors after filtering. 0 means no limit.",
    )
    parser.add_argument(
        "--max-chars-per-call",
        type=int,
        default=5500,
        help="Maximum project_text characters per LLM call before chunking.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Sleep seconds between LLM calls.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load rows and show what would be processed without calling the model.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    rows = load_supervisor_rows(input_path)

    if args.only:
        target = _norm_key(args.only)
        rows = [
            row
            for row in rows
            if _norm_key(row.get("supervisor_id", "")) == target
            or _norm_key(row.get("name", "")) == target
        ]

    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    existing = load_existing_summaries(output_path)
    existing_by_id = {
        str(item.get("supervisor_id") or "").strip(): item
        for item in existing
        if str(item.get("supervisor_id") or "").strip()
    }
    existing_by_name = {
        _norm_key(str(item.get("name") or "")): item
        for item in existing
        if _norm_key(str(item.get("name") or ""))
    }

    print(f"[info] input: {input_path}")
    print(f"[info] output: {output_path}")
    print(f"[info] supervisors to consider: {len(rows)}")
    print(f"[info] existing summaries: {len(existing)}")

    if args.dry_run:
        for row in rows:
            supervisor_id = row.get("supervisor_id", "")
            name = row.get("name", "")
            text_length = len(row.get("project_text", ""))
            already_exists = (
                supervisor_id in existing_by_id
                or _norm_key(name) in existing_by_name
            )
            print(
                f"- {supervisor_id} | {name} | "
                f"project_text chars={text_length} | "
                f"existing={already_exists}"
            )
        return

    model = RemoteChatModel()
    summaries: List[Dict[str, Any]] = list(existing)

    for index, row in enumerate(rows, start=1):
        supervisor_id = row.get("supervisor_id", "")
        name = row.get("name", "")
        name_key = _norm_key(name)
        already_exists = (
            supervisor_id in existing_by_id
            or name_key in existing_by_name
        )

        if already_exists and not args.force:
            print(
                f"[skip] {index}/{len(rows)} "
                f"{supervisor_id} {name} already summarised"
            )
            continue

        print(f"[run] {index}/{len(rows)} {supervisor_id} {name}")
        try:
            summary = summarize_supervisor(
                model=model,
                row=row,
                max_chars_per_call=args.max_chars_per_call,
                sleep_seconds=args.sleep,
            )
            summaries = upsert_summary(
                summaries=summaries,
                supervisor_id=supervisor_id,
                name=name,
                new_summary=summary,
            )

            # Save after every supervisor so the script can resume safely.
            save_summaries(output_path, summaries)
            existing_by_id[supervisor_id] = summary
            existing_by_name[name_key] = summary
            print(f"[done] saved summary for {supervisor_id} {name}")
        except Exception as exc:
            print(
                f"[error] failed to summarise "
                f"{supervisor_id} {name}: {exc}"
            )
            print("[info] keeping the previous record and continuing")

        if args.sleep > 0:
            time.sleep(args.sleep)

    save_summaries(output_path, summaries)
    print(f"[done] wrote {output_path}")
    print(f"[done] total summaries: {len(summaries)}")


if __name__ == "__main__":
    main()
