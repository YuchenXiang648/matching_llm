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
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "references" / "data"
BUILD_DIR = ROOT / "references" / "build"

SUPERVISORS_INPUT_CSV = DATA_DIR / "supervisors_input_expanded.csv"
SCHOLAR_PAPERS_CSV = DATA_DIR / "supervisor_papers.csv"
ONLINE_PAPERS_CSV = DATA_DIR / "online_supervisor_papers.csv"

COMBINED_PAPERS_CSV = BUILD_DIR / "combined_supervisor_papers.csv"
SUPERVISOR_DETAIL_JSON = BUILD_DIR / "supervisor_detail.json"
PREPROCESS_SCRIPT = ROOT / "scripts" / "preprocess.py"

OUTPUT_FIELDS = [
    "supervisor_id",
    "supervisor_name",
    "paper_id",
    "title",
    "abstract",
    "year",
    "scholar_url",
    "author_lookup_mode",
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
}

ABSTRACT_PLACEHOLDERS = {
    "abstract not available",
    "abstract unavailable",
    "abstract is not available",
    "no abstract available",
    "not available from search results",
    "not provided",
}


def _text(value: object) -> str:
    """Convert one CSV value into clean single-line text."""
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _is_empty_like(value: object) -> bool:
    return _text(value).lower().rstrip(".") in EMPTY_VALUES


def _clean_abstract(value: object) -> str:
    abstract = _text(value)
    lowered = abstract.lower().rstrip(".")
    if lowered in EMPTY_VALUES or lowered in ABSTRACT_PLACEHOLDERS:
        return ""
    return abstract


def _clean_year(value: object) -> str:
    """
    Keep only a four-digit publication year.

    Examples:
    "2025" -> "2025"
    "2025-03-10" -> "2025"
    "not available" -> ""
    """
    text = _text(value)
    match = re.search(r"\b(?:19|20)\d{2}\b", text)
    return match.group(0) if match else ""


def _normalise_title(value: object) -> str:
    text = _text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _generated_paper_id(
    source_name: str,
    supervisor_id: str,
    title: str,
    year: str,
) -> str:
    """
    Generate an internal stable ID.

    For online-search rows this deliberately does not retain the DOI.
    The ID is used only for local deduplication and is not included in the
    text sent to the Stage 2 LLM.
    """
    raw = f"{supervisor_id}|{_normalise_title(title)}|{year}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{source_name}:{supervisor_id}:{digest}"


def _clean_scholar_paper_id(value: object) -> str:
    """
    Keep a usable Scholarly paper ID, but reject placeholder values.

    Online-search IDs are always regenerated separately, so DOI values from
    online_supervisor_papers.csv never enter the combined Stage 2 dataset.
    """
    paper_id = _text(value)
    if _is_empty_like(paper_id):
        return ""

    lowered = paper_id.lower().strip()
    if lowered.startswith("doi:"):
        remainder = paper_id[4:].strip()
        if _is_empty_like(remainder):
            return ""

    return paper_id


def _read_source_rows(
    path: Path,
    source_name: str,
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    stats = {
        "input_rows": 0,
        "usable_rows": 0,
        "skipped_missing_id": 0,
        "skipped_missing_title": 0,
    }
    rows: List[Dict[str, str]] = []

    if not path.exists() or path.stat().st_size == 0:
        print(f"[warn] missing or empty {source_name} CSV: {path}")
        return rows, stats

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)

        for raw in reader:
            stats["input_rows"] += 1

            supervisor_id = _text(raw.get("supervisor_id"))
            supervisor_name = _text(
                raw.get("supervisor_name")
                or raw.get("name")
                or raw.get("supervisor_1_name")
            )
            title = _text(raw.get("title"))
            abstract = _clean_abstract(raw.get("abstract"))
            year = _clean_year(raw.get("year"))

            if not supervisor_id:
                stats["skipped_missing_id"] += 1
                continue
            if not title:
                stats["skipped_missing_title"] += 1
                continue

            if source_name == "scholarly":
                paper_id = _clean_scholar_paper_id(raw.get("paper_id"))
                if not paper_id:
                    paper_id = _generated_paper_id(
                        source_name, supervisor_id, title, year
                    )
                scholar_url = _text(raw.get("scholar_url"))
                lookup_mode = (
                    _text(raw.get("author_lookup_mode")) or "scholarly"
                )
            else:
                # Deliberately ignore online CSV fields such as:
                # doi, authors, venue, source_url, scholar_url and raw paper_id.
                paper_id = _generated_paper_id(
                    source_name, supervisor_id, title, year
                )
                scholar_url = ""
                lookup_mode = "online_search"

            rows.append(
                {
                    "supervisor_id": supervisor_id,
                    "supervisor_name": supervisor_name,
                    "paper_id": paper_id,
                    "title": title,
                    "abstract": abstract,
                    "year": year,
                    "scholar_url": scholar_url,
                    "author_lookup_mode": lookup_mode,
                    "_source": source_name,
                }
            )
            stats["usable_rows"] += 1

    return rows, stats


def _merge_rows(
    scholarly_rows: Iterable[Dict[str, str]],
    online_rows: Iterable[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    """
    Merge both sources.

    Scholarly rows are considered first. If the same supervisor/title also
    appears in online search, the existing Scholarly row is retained, but a
    missing abstract or year may be filled from the online row.
    """
    merged: List[Dict[str, str]] = []
    index_by_key: Dict[Tuple[str, str], int] = {}

    stats = {
        "duplicates_merged": 0,
        "abstracts_filled": 0,
        "years_filled": 0,
    }

    for row in [*scholarly_rows, *online_rows]:
        key = (
            row["supervisor_id"].lower(),
            _normalise_title(row["title"]),
        )

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

        if not existing.get("supervisor_name") and row.get("supervisor_name"):
            existing["supervisor_name"] = row["supervisor_name"]

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


def _write_combined_csv(path: Path, rows: List[Dict[str, str]]) -> None:
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


def _run_existing_preprocess(
    input_csv: Path,
    combined_papers_csv: Path,
    output_json: Path,
) -> None:
    if not PREPROCESS_SCRIPT.exists():
        raise FileNotFoundError(
            f"Existing preprocessing script was not found: {PREPROCESS_SCRIPT}"
        )

    command = [
        sys.executable,
        str(PREPROCESS_SCRIPT),
        "--input",
        str(input_csv),
        "--papers",
        str(combined_papers_csv),
        "--output",
        str(output_json),
    ]

    print("\n[run] existing Scholarly preprocessing pipeline")
    print("[run] " + " ".join(command))
    subprocess.run(command, check=True)


def _print_json_stats(output_json: Path) -> None:
    if not output_json.exists():
        print(f"[warn] expected JSON was not created: {output_json}")
        return

    try:
        payload = json.loads(output_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"[warn] could not inspect generated JSON: {error}")
        return

    supervisors = (
        payload.get("supervisors", [])
        if isinstance(payload, dict)
        else []
    )
    supervisors_with_papers = sum(
        1 for supervisor in supervisors if supervisor.get("papers")
    )
    paper_count = sum(
        len(supervisor.get("papers") or [])
        for supervisor in supervisors
    )

    print("\n[verified] Stage 2 JSON")
    print(f"[verified] supervisors: {len(supervisors)}")
    print(f"[verified] supervisors with papers: {supervisors_with_papers}")
    print(f"[verified] papers: {paper_count}")
    print(f"[verified] output: {output_json}")


def main(
    input_csv: Path = SUPERVISORS_INPUT_CSV,
    scholar_papers_csv: Path = SCHOLAR_PAPERS_CSV,
    online_papers_csv: Path = ONLINE_PAPERS_CSV,
    combined_output_csv: Path = COMBINED_PAPERS_CSV,
    output_json: Path = SUPERVISOR_DETAIL_JSON,
) -> None:
    scholarly_rows, scholarly_stats = _read_source_rows(
        scholar_papers_csv,
        "scholarly",
    )
    online_rows, online_stats = _read_source_rows(
        online_papers_csv,
        "online",
    )

    combined_rows, merge_stats = _merge_rows(
        scholarly_rows,
        online_rows,
    )
    _write_combined_csv(combined_output_csv, combined_rows)

    print("[done] clean paper sources merged")
    print(
        "[stats] Scholarly: "
        f"input={scholarly_stats['input_rows']}, "
        f"usable={scholarly_stats['usable_rows']}"
    )
    print(
        "[stats] online search: "
        f"input={online_stats['input_rows']}, "
        f"usable={online_stats['usable_rows']}"
    )
    print(
        "[stats] merge: "
        f"duplicates={merge_stats['duplicates_merged']}, "
        f"abstracts_filled={merge_stats['abstracts_filled']}, "
        f"years_filled={merge_stats['years_filled']}"
    )
    print(f"[stats] combined rows={len(combined_rows)}")
    print(f"[done] combined CSV: {combined_output_csv}")

    _run_existing_preprocess(
        input_csv=input_csv,
        combined_papers_csv=combined_output_csv,
        output_json=output_json,
    )
    _print_json_stats(output_json)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Clean and merge Scholarly and OpenCode online-search paper CSVs, "
            "then reuse scripts/preprocess.py to rebuild the Stage 2 "
            "supervisor_detail.json file."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=SUPERVISORS_INPUT_CSV,
        help=f"Supervisor input CSV. Default: {SUPERVISORS_INPUT_CSV}",
    )
    parser.add_argument(
        "--scholar-papers",
        type=Path,
        default=SCHOLAR_PAPERS_CSV,
        help=f"Scholarly paper CSV. Default: {SCHOLAR_PAPERS_CSV}",
    )
    parser.add_argument(
        "--online-papers",
        type=Path,
        default=ONLINE_PAPERS_CSV,
        help=f"Online-search paper CSV. Default: {ONLINE_PAPERS_CSV}",
    )
    parser.add_argument(
        "--combined-output",
        type=Path,
        default=COMBINED_PAPERS_CSV,
        help=f"Clean combined CSV. Default: {COMBINED_PAPERS_CSV}",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=SUPERVISOR_DETAIL_JSON,
        help=f"Stage 2 JSON. Default: {SUPERVISOR_DETAIL_JSON}",
    )
    args = parser.parse_args()

    main(
        input_csv=args.input,
        scholar_papers_csv=args.scholar_papers,
        online_papers_csv=args.online_papers,
        combined_output_csv=args.combined_output,
        output_json=args.output_json,
    )
