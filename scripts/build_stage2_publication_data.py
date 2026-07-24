from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "references" / "data"
BUILD_DIR = ROOT / "references" / "build"

SUPERVISORS_INPUT_CSV = DATA_DIR / "supervisors_input_expanded.csv"
SCHOLAR_PAPERS_CSV = DATA_DIR / "supervisor_papers.csv"
ONLINE_BIB_DIR = DATA_DIR / "online_bibliographies"
STAGE2_PAPERS_CSV = BUILD_DIR / "stage2_supervisor_papers.csv"
SUPERVISOR_DETAIL_JSON = BUILD_DIR / "supervisor_detail.json"
PREPROCESS_SCRIPT = ROOT / "scripts" / "preprocess.py"

OUTPUT_FIELDS = [
    "supervisor_id",
    "supervisor_name",
    "paper_id",
    "title",
    "abstract",
    "year",
]

EMPTY_VALUES = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "not available",
    "not applicable",
    "nan",
    "abstract not available",
    "abstract unavailable",
    "no abstract available",
    "not provided",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _clean_abstract(value: object) -> str:
    text = _text(value)
    if text.lower().rstrip(".") in EMPTY_VALUES:
        return ""
    return text


def _clean_year(value: object) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", _text(value))
    return match.group(0) if match else ""


def _normalise_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _normalise_title(value: object) -> str:
    text = _text(value).lower()
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    text = text.replace("{", " ").replace("}", " ").replace("$", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _paper_id(source: str, supervisor_id: str, title: str, year: str) -> str:
    raw = f"{supervisor_id}|{_normalise_title(title)}|{year}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{supervisor_id}:{digest}"


def load_supervisors(path: Path) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing supervisor input CSV: {path}")

    by_id: Dict[str, Dict[str, str]] = {}
    by_filename_key: Dict[str, Dict[str, str]] = {}

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=1):
            supervisor_id = _text(row.get("supervisor_id") or f"s{index}")
            name = _text(
                row.get("name")
                or row.get("supervisor_name")
                or row.get("supervisor_1_name")
            )
            if not supervisor_id or not name:
                continue

            item = {
                "supervisor_id": supervisor_id,
                "supervisor_name": name,
            }
            by_id[supervisor_id] = item

            key = _normalise_name(name)
            if key in by_filename_key:
                raise ValueError(
                    "Two supervisors collapse to the same filename key: "
                    f"{by_filename_key[key]['supervisor_name']} and {name}"
                )
            by_filename_key[key] = item

    return by_id, by_filename_key


def _read_scholarly_rows(
    path: Path,
    supervisors_by_id: Dict[str, Dict[str, str]],
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not path.exists() or path.stat().st_size == 0:
        print(f"[warn] missing or empty Scholarly CSV: {path}")
        return rows

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            supervisor_id = _text(raw.get("supervisor_id"))
            title = _text(raw.get("title"))
            if not supervisor_id or not title:
                continue

            supervisor = supervisors_by_id.get(supervisor_id)
            if not supervisor:
                print(
                    f"[warn] skip Scholarly row with unknown supervisor_id: "
                    f"{supervisor_id} | {title}"
                )
                continue

            abstract = _clean_abstract(raw.get("abstract"))
            year = _clean_year(raw.get("year"))
            raw_paper_id = _text(raw.get("paper_id"))
            paper_id = (
                raw_paper_id
                if raw_paper_id.lower().rstrip(".") not in EMPTY_VALUES
                else _paper_id("scholarly", supervisor_id, title, year)
            )

            rows.append(
                {
                    "supervisor_id": supervisor_id,
                    "supervisor_name": supervisor["supervisor_name"],
                    "paper_id": paper_id,
                    "title": title,
                    "abstract": abstract,
                    "year": year,
                    "_source": "scholarly",
                }
            )

    return rows


def _iter_bibtex_entries(text: str) -> Iterator[str]:
    """Yield complete BibTeX entries while respecting nested braces and quotes."""
    index = 0
    length = len(text)

    while index < length:
        at = text.find("@", index)
        if at < 0:
            return

        open_pos = at + 1
        while open_pos < length and text[open_pos] not in "{(":
            open_pos += 1
        if open_pos >= length:
            return

        opener = text[open_pos]
        closer = "}" if opener == "{" else ")"
        depth = 1
        quoted = False
        escaped = False
        cursor = open_pos + 1

        while cursor < length and depth > 0:
            char = text[cursor]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif opener == "(" and char == '"':
                quoted = not quoted
            elif not quoted:
                if char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
            cursor += 1

        if depth == 0:
            yield text[at:cursor]
            index = cursor
        else:
            print(f"[warn] incomplete BibTeX entry starting near character {at}")
            return


def _read_balanced_value(text: str, start: int) -> Tuple[str, int]:
    opener = text[start]
    closer = "}" if opener == "{" else '"'
    cursor = start + 1
    depth = 1 if opener == "{" else 0
    escaped = False

    while cursor < len(text):
        char = text[cursor]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif opener == "{" and char == "{":
            depth += 1
        elif opener == "{" and char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : cursor], cursor + 1
        elif opener == '"' and char == closer:
            return text[start + 1 : cursor], cursor + 1
        cursor += 1

    return text[start + 1 :], len(text)


def _parse_bibtex_fields(entry: str) -> Dict[str, str]:
    open_positions = [pos for pos in (entry.find("{"), entry.find("(")) if pos >= 0]
    if not open_positions:
        return {}

    open_pos = min(open_positions)
    close_pos = len(entry) - 1
    body = entry[open_pos + 1 : close_pos]

    comma = body.find(",")
    if comma < 0:
        return {}
    cursor = comma + 1
    fields: Dict[str, str] = {}

    while cursor < len(body):
        while cursor < len(body) and (body[cursor].isspace() or body[cursor] == ","):
            cursor += 1
        if cursor >= len(body):
            break

        name_match = re.match(r"[A-Za-z][A-Za-z0-9_:-]*", body[cursor:])
        if not name_match:
            cursor += 1
            continue

        field_name = name_match.group(0).lower()
        cursor += len(name_match.group(0))
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
        if cursor >= len(body) or body[cursor] != "=":
            continue
        cursor += 1
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
        if cursor >= len(body):
            fields[field_name] = ""
            break

        if body[cursor] in {'{', '"'}:
            value, cursor = _read_balanced_value(body, cursor)
        else:
            end = cursor
            while end < len(body) and body[end] != ",":
                end += 1
            value = body[cursor:end]
            cursor = end

        fields[field_name] = _text(value.replace("~", " "))

    return fields


def _read_online_bib_rows(
    directory: Path,
    supervisors_by_filename_key: Dict[str, Dict[str, str]],
) -> Tuple[List[Dict[str, str]], List[str]]:
    if not directory.exists():
        raise FileNotFoundError(f"Missing online bibliography directory: {directory}")

    rows: List[Dict[str, str]] = []
    unmatched_files: List[str] = []

    bib_files = sorted(directory.glob("*.bib"))
    if not bib_files:
        raise FileNotFoundError(f"No .bib files found in: {directory}")

    for bib_path in bib_files:
        filename_key = _normalise_name(bib_path.stem)
        supervisor = supervisors_by_filename_key.get(filename_key)
        if not supervisor:
            unmatched_files.append(bib_path.name)
            continue

        text = bib_path.read_text(encoding="utf-8-sig", errors="replace")
        file_count = 0
        for entry in _iter_bibtex_entries(text):
            fields = _parse_bibtex_fields(entry)
            title = _text(fields.get("title"))
            if not title:
                continue

            abstract = _clean_abstract(fields.get("abstract"))
            year = _clean_year(fields.get("year") or fields.get("date"))
            supervisor_id = supervisor["supervisor_id"]

            rows.append(
                {
                    "supervisor_id": supervisor_id,
                    "supervisor_name": supervisor["supervisor_name"],
                    "paper_id": _paper_id("online_bib", supervisor_id, title, year),
                    "title": title,
                    "abstract": abstract,
                    "year": year,
                    "_source": "online_bib",
                }
            )
            file_count += 1

        print(
            f"[bib] {bib_path.name}: supervisor={supervisor['supervisor_name']} "
            f"entries={file_count}"
        )

    return rows, unmatched_files


def _merge_rows(
    scholarly_rows: Iterable[Dict[str, str]],
    online_rows: Iterable[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    merged: List[Dict[str, str]] = []
    index_by_key: Dict[Tuple[str, str], int] = {}
    stats = {
        "duplicates_merged": 0,
        "abstracts_filled": 0,
        "years_filled": 0,
    }

    for row in [*scholarly_rows, *online_rows]:
        key = (row["supervisor_id"].lower(), _normalise_title(row["title"]))
        existing_index = index_by_key.get(key)

        if existing_index is None:
            index_by_key[key] = len(merged)
            merged.append(dict(row))
            continue

        stats["duplicates_merged"] += 1
        existing = merged[existing_index]
        if not existing.get("abstract") and row.get("abstract"):
            existing["abstract"] = row["abstract"]
            stats["abstracts_filled"] += 1
        if not existing.get("year") and row.get("year"):
            existing["year"] = row["year"]
            stats["years_filled"] += 1

    def sort_key(row: Dict[str, str]) -> Tuple[int, str, int, str]:
        sid_match = re.fullmatch(r"s(\d+)", row.get("supervisor_id", "").lower())
        sid_number = int(sid_match.group(1)) if sid_match else 10**9
        year = int(row["year"]) if row.get("year", "").isdigit() else -1
        return (
            sid_number,
            row.get("supervisor_id", ""),
            -year,
            row.get("title", "").lower(),
        )

    merged.sort(key=sort_key)
    return merged, stats


def _write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=str(path.parent),
        prefix=path.stem + "_",
        suffix=".tmp",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OUTPUT_FIELDS})
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def _run_preprocess(input_csv: Path, papers_csv: Path, output_json: Path) -> None:
    if not PREPROCESS_SCRIPT.exists():
        raise FileNotFoundError(f"Missing preprocessing script: {PREPROCESS_SCRIPT}")

    command = [
        sys.executable,
        str(PREPROCESS_SCRIPT),
        "--input",
        str(input_csv),
        "--papers",
        str(papers_csv),
        "--output",
        str(output_json),
    ]
    print("[run] " + " ".join(command))
    subprocess.run(command, check=True)


def _verify_output(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    supervisors = payload.get("supervisors", []) if isinstance(payload, dict) else []
    print(f"[verified] supervisors={len(supervisors)}")
    print(f"[verified] supervisors_with_papers={sum(1 for s in supervisors if s.get('papers'))}")
    print(f"[verified] papers={sum(len(s.get('papers') or []) for s in supervisors)}")
    print(f"[verified] Stage 2 JSON={path}")


def main(
    input_csv: Path = SUPERVISORS_INPUT_CSV,
    scholarly_csv: Path = SCHOLAR_PAPERS_CSV,
    online_bib_dir: Path = ONLINE_BIB_DIR,
    output_csv: Path = STAGE2_PAPERS_CSV,
    output_json: Path = SUPERVISOR_DETAIL_JSON,
) -> None:
    supervisors_by_id, supervisors_by_filename_key = load_supervisors(input_csv)
    scholarly_rows = _read_scholarly_rows(scholarly_csv, supervisors_by_id)
    online_rows, unmatched_files = _read_online_bib_rows(
        online_bib_dir,
        supervisors_by_filename_key,
    )

    if unmatched_files:
        joined = "\n- ".join(unmatched_files)
        raise ValueError(
            "These bibliography filenames do not exactly match any supervisor name "
            "after normalisation:\n- " + joined
        )

    combined_rows, stats = _merge_rows(scholarly_rows, online_rows)
    _write_csv(output_csv, combined_rows)

    print(f"[stats] Scholarly rows={len(scholarly_rows)}")
    print(f"[stats] online BibTeX rows={len(online_rows)}")
    print(f"[stats] duplicates merged={stats['duplicates_merged']}")
    print(f"[stats] missing abstracts filled={stats['abstracts_filled']}")
    print(f"[stats] missing years filled={stats['years_filled']}")
    print(f"[stats] final Stage 2 paper rows={len(combined_rows)}")
    print(f"[done] Stage 2 paper CSV={output_csv}")

    _run_preprocess(input_csv, output_csv, output_json)
    _verify_output(output_json)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Merge Scholarly CSV papers with manually collected OpenCode BibTeX files, "
            "then rebuild the Stage 2 supervisor_detail.json file."
        )
    )
    parser.add_argument("--input", type=Path, default=SUPERVISORS_INPUT_CSV)
    parser.add_argument("--scholarly", type=Path, default=SCHOLAR_PAPERS_CSV)
    parser.add_argument("--online-bib-dir", type=Path, default=ONLINE_BIB_DIR)
    parser.add_argument("--output-csv", type=Path, default=STAGE2_PAPERS_CSV)
    parser.add_argument("--output-json", type=Path, default=SUPERVISOR_DETAIL_JSON)
    args = parser.parse_args()

    main(
        input_csv=args.input,
        scholarly_csv=args.scholarly,
        online_bib_dir=args.online_bib_dir,
        output_csv=args.output_csv,
        output_json=args.output_json,
    )
