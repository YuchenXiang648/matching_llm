from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse
import csv
import json
import re
import time
from typing import Any, Dict, List, Optional

from model_client import RemoteChatModel, extract_json
from text_utils import extract_keywords

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
    text = text.replace("￾", "-")
    text = text.replace("\u00a0", " ")

    # Keep paragraph breaks, but remove excessive blank lines.
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
    path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")


def load_supervisor_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input CSV: {path}")

    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = (row.get("supervisor_id") or "").strip()
            name = (row.get("name") or row.get("supervisor_1_name") or "").strip()
            project_text = norm_text(row.get("project_text") or "")
            if not sid and not name:
                continue
            if not name:
                print(f"[warn] skip row with missing name: supervisor_id={sid}")
                continue

            rows.append(
                {
                    "supervisor_id": sid,
                    "name": name,
                    "scholar_url": (row.get("scholar_url") or "").strip(),
                    "homepage": (row.get("homepage") or "").strip(),
                    "project_text": project_text,
                }
            )

    return rows


def split_project_text(project_text: str, max_chars: int = 5500) -> List[str]:
    """
    Most supervisors can be summarized in one call.
    If one supervisor has many projects and the text is too long, split it into chunks.

    The split tries to preserve project boundaries marked by:
    -----
    Title:
    """
    project_text = norm_text(project_text)
    if len(project_text) <= max_chars:
        return [project_text] if project_text else []

    # Try to split by the separator you are using between multiple projects.
    parts = re.split(r"\n\s*-{3,}\s*\n", project_text)
    parts = [p.strip() for p in parts if p.strip()]

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

    # If one single part is still too long, hard split by paragraphs.
    final_chunks: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
            continue

        paragraphs = [p.strip() for p in chunk.split("\n\n") if p.strip()]
        current = ""
        for p in paragraphs:
            if not current:
                current = p
            elif len(current) + len(p) + 2 <= max_chars:
                current = current + "\n\n" + p
            else:
                final_chunks.append(current)
                current = p
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
) -> Dict[str, Any]:
    system = (
        "You summarize UCL MSc research project descriptions for a student-supervisor matching system.\n"
        "Use only the supplied project text. Do not use external knowledge.\n"
        "Do not invent facts, prerequisites, papers, institutions, or project details.\n"
        "Return valid JSON only."
    )

    prompt = (
        f"Supervisor ID: {supervisor_id}\n"
        f"Supervisor name: {name}\n"
        f"Chunk: {chunk_index + 1} of {total_chunks}\n\n"
        "Project text:\n"
        f"{chunk_text}\n\n"
        "Task:\n"
        "Summarize this project text for Stage 1 coarse matching.\n"
        "The summary should help match students based on topic, methods, project style, prerequisites, and application area.\n\n"
        "Return JSON only with exactly this schema:\n"
        "{\n"
        '  "partial_summary": "one concise paragraph, 50-90 words",\n'
        '  "project_titles": ["title 1", "title 2"],\n'
        '  "keywords": ["keyword 1", "keyword 2", "keyword 3"],\n'
        '  "project_style": ["coding-heavy", "ML-heavy", "theory-heavy", "data-heavy", "software-engineering", "experimental", "interdisciplinary"],\n'
        '  "preferred_background": ["Python", "machine learning"]\n'
        "}\n\n"
        "Rules:\n"
        "- Keep project_titles exactly based on the text when possible.\n"
        "- If no prerequisite is stated, use an empty list for preferred_background.\n"
        "- project_style can use only short phrases.\n"
        "- keywords should be short topic/method phrases.\n"
        "- Do not mention that this is a chunk.\n"
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

    return {
        "partial_summary": str(data.get("partial_summary") or "").strip(),
        "project_titles": [str(x).strip() for x in data.get("project_titles", []) if str(x).strip()],
        "keywords": [str(x).strip() for x in data.get("keywords", []) if str(x).strip()],
        "project_style": [str(x).strip() for x in data.get("project_style", []) if str(x).strip()],
        "preferred_background": [str(x).strip() for x in data.get("preferred_background", []) if str(x).strip()],
    }


def merge_chunk_summaries(
    model: RemoteChatModel,
    supervisor_id: str,
    name: str,
    homepage: str,
    scholar_url: str,
    partials: List[Dict[str, Any]],
    fallback_keywords: List[str],
) -> Dict[str, Any]:
    """
    Merge one or more partial summaries into the final supervisor-level summary.
    If there is only one chunk, this still normalizes the output structure.
    """
    system = (
        "You create final supervisor-level summaries for a Stage 1 supervisor matching system.\n"
        "Use only the supplied partial summaries. Do not invent facts.\n"
        "Return valid JSON only."
    )

    prompt = (
        f"Supervisor ID: {supervisor_id}\n"
        f"Supervisor name: {name}\n\n"
        "Partial summaries and extracted fields:\n"
        f"{json.dumps(partials, ensure_ascii=False, indent=2)}\n\n"
        "Create one final concise summary for this supervisor.\n"
        "If the supervisor has multiple projects, combine them clearly in one paragraph.\n\n"
        "Return JSON only with exactly this schema:\n"
        "{\n"
        '  "summary": "one concise paragraph, 60-110 words",\n'
        '  "project_titles": ["title 1", "title 2"],\n'
        '  "keywords": ["keyword 1", "keyword 2", "keyword 3"],\n'
        '  "project_style": ["coding-heavy", "ML-heavy"],\n'
        '  "preferred_background": ["Python", "machine learning"]\n'
        "}\n\n"
        "Rules:\n"
        "- The summary must be useful for matching students to this supervisor's available project options.\n"
        "- Mention the main topic areas and methods.\n"
        "- Mention prerequisites only if they appear in the supplied partial summaries.\n"
        "- Do not mention anything not supported by the supplied partial summaries.\n"
    )

    try:
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
        project_titles = [str(x).strip() for x in data.get("project_titles", []) if str(x).strip()]
        keywords = [str(x).strip() for x in data.get("keywords", []) if str(x).strip()]
        project_style = [str(x).strip() for x in data.get("project_style", []) if str(x).strip()]
        preferred_background = [str(x).strip() for x in data.get("preferred_background", []) if str(x).strip()]

    except Exception:
        # Safe fallback if the merge call fails.
        joined = " ".join(p.get("partial_summary", "") for p in partials if p.get("partial_summary"))
        summary = joined[:900].strip() or "No project summary could be generated."
        project_titles = []
        keywords = []
        project_style = []
        preferred_background = []

        for p in partials:
            project_titles.extend(p.get("project_titles", []))
            keywords.extend(p.get("keywords", []))
            project_style.extend(p.get("project_style", []))
            preferred_background.extend(p.get("preferred_background", []))

    # Deduplicate while preserving order.
    def dedupe(items: List[str]) -> List[str]:
        seen = set()
        clean: List[str] = []
        for item in items:
            key = _norm_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            clean.append(item)
        return clean

    keywords = dedupe(keywords + fallback_keywords)[:12]

    return {
        "supervisor_id": supervisor_id,
        "name": name,
        "homepage": homepage,
        "scholar_url": scholar_url,
        "summary": summary,
        "project_titles": dedupe(project_titles),
        "keywords": keywords,
        "project_style": dedupe(project_style)[:8],
        "preferred_background": dedupe(preferred_background)[:8],
        "source": "supervisors_input.csv project_text",
        "summary_type": "project_text_summary",
    }


def summarize_supervisor(
    model: RemoteChatModel,
    row: Dict[str, str],
    max_chars_per_call: int,
    sleep_seconds: float,
) -> Dict[str, Any]:
    supervisor_id = row.get("supervisor_id", "")
    name = row.get("name", "")
    homepage = row.get("homepage", "")
    scholar_url = row.get("scholar_url", "")
    project_text = row.get("project_text", "")

    fallback_keywords = extract_keywords(project_text, top_k=12)

    if not project_text.strip():
        return {
            "supervisor_id": supervisor_id,
            "name": name,
            "homepage": homepage,
            "scholar_url": scholar_url,
            "summary": "No project description is available in the local project database.",
            "project_titles": [],
            "keywords": fallback_keywords,
            "project_style": [],
            "preferred_background": [],
            "source": "supervisors_input.csv project_text",
            "summary_type": "project_text_summary",
        }

    chunks = split_project_text(project_text, max_chars=max_chars_per_call)
    partials: List[Dict[str, Any]] = []

    for i, chunk in enumerate(chunks):
        print(f"    summarizing chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)")
        partial = summarize_one_chunk(
            model=model,
            supervisor_id=supervisor_id,
            name=name,
            chunk_text=chunk,
            chunk_index=i,
            total_chunks=len(chunks),
        )
        partials.append(partial)
        if sleep_seconds > 0 and i < len(chunks) - 1:
            time.sleep(sleep_seconds)

    return merge_chunk_summaries(
        model=model,
        supervisor_id=supervisor_id,
        name=name,
        homepage=homepage,
        scholar_url=scholar_url,
        partials=partials,
        fallback_keywords=fallback_keywords,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize supervisors_input.csv project_text into supervisor_project_summaries.json"
    )
    parser.add_argument(
        "--input",
        type=str,
        default=str(SUPERVISORS_INPUT_CSV),
        help="Path to supervisors_input.csv",
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
        help="Only process one supervisor by exact supervisor_id or name, e.g. s12 or Edina Rosta",
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
        help="Load rows and show what would be processed, without calling the model.",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    rows = load_supervisor_rows(input_path)
    if args.only:
        target = _norm_key(args.only)
        rows = [
            r for r in rows
            if _norm_key(r.get("supervisor_id", "")) == target
            or _norm_key(r.get("name", "")) == target
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
        str(item.get("name") or "").strip(): item
        for item in existing
        if str(item.get("name") or "").strip()
    }

    print(f"[info] input: {input_path}")
    print(f"[info] output: {output_path}")
    print(f"[info] supervisors to consider: {len(rows)}")
    print(f"[info] existing summaries: {len(existing)}")

    if args.dry_run:
        for r in rows:
            sid = r.get("supervisor_id", "")
            name = r.get("name", "")
            text_len = len(r.get("project_text", ""))
            already = sid in existing_by_id or name in existing_by_name
            print(f"- {sid} | {name} | project_text chars={text_len} | existing={already}")
        return

    model = RemoteChatModel()

    summaries: List[Dict[str, Any]] = list(existing)

    for idx, row in enumerate(rows, start=1):
        sid = row.get("supervisor_id", "")
        name = row.get("name", "")

        already_exists = sid in existing_by_id or name in existing_by_name
        if already_exists and not args.force:
            print(f"[skip] {idx}/{len(rows)} {sid} {name} already summarized")
            continue

        print(f"[run] {idx}/{len(rows)} {sid} {name}")

        try:
            summary = summarize_supervisor(
                model=model,
                row=row,
                max_chars_per_call=args.max_chars_per_call,
                sleep_seconds=args.sleep,
            )

            # Remove old version if --force is used.
            summaries = [
                item for item in summaries
                if str(item.get("supervisor_id") or "").strip() != sid
                and str(item.get("name") or "").strip() != name
            ]
            summaries.append(summary)

            # Save after every supervisor, so the script can resume safely.
            save_summaries(output_path, summaries)

            existing_by_id[sid] = summary
            existing_by_name[name] = summary

            print(f"[done] saved summary for {sid} {name}")

        except Exception as e:
            print(f"[error] failed to summarize {sid} {name}: {e}")
            print("[info] continuing with the next supervisor")

        if args.sleep > 0:
            time.sleep(args.sleep)

    save_summaries(output_path, summaries)
    print(f"[done] wrote {output_path}")
    print(f"[done] total summaries: {len(summaries)}")


if __name__ == "__main__":
    main()