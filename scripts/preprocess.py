# scripts/preprocess.py
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
REFERENCES_DIR = ROOT / "references"
DATA_DIR = REFERENCES_DIR / "data"
BUILD_DIR = REFERENCES_DIR / "build"

CSV_INPUT = DATA_DIR / "supervisors_input_expanded.csv"
CSV_PAPERS = DATA_DIR / "supervisor_papers.csv"

OUT_JSON = BUILD_DIR / "supervisor_detail.json"


def _s(x) -> str:
    return "" if x is None else str(x).strip()


def _norm(s: str) -> str:
    return " ".join(_s(s).lower().split())


def _year_int(y: str) -> Optional[int]:
    """
    Try to parse a year. Return None if parsing fails.
    """
    y = _s(y)
    if not y:
        return None

    y4 = y[:4]
    if not y4.isdigit():
        return None

    try:
        return int(y4)
    except Exception:
        return None


def _dedup_papers_keep_order(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate papers by:
    - paper_id if available
    - otherwise title + year

    Keep first occurrence.
    """
    seen = set()
    out: List[Dict[str, Any]] = []

    for p in papers:
        paper_id = _s(p.get("paper_id"))
        title = _s(p.get("title"))
        year = _s(p.get("year"))

        if not title and not paper_id:
            continue

        if paper_id:
            key = ("id", paper_id)
        else:
            key = ("title_year", title.lower(), year[:4])

        if key in seen:
            continue

        seen.add(key)
        out.append(p)

    return out


def _sort_papers_by_year_desc(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sort papers by year descending.
    Unknown-year papers are kept at the end.
    """
    known: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []

    for p in papers:
        if _year_int(_s(p.get("year"))) is None:
            unknown.append(p)
        else:
            known.append(p)

    known.sort(key=lambda p: _year_int(_s(p.get("year"))) or -1, reverse=True)
    return known + unknown


def load_supervisors_input(input_csv: Path) -> Dict[str, Dict[str, str]]:
    """
    Load references/data/supervisors_input_expanded.csv.

    Return:
        supervisor_id -> supervisor info
    """
    if not input_csv.exists() or input_csv.stat().st_size == 0:
        raise FileNotFoundError(f"Missing or empty input CSV: {input_csv}")

    info_by_id: Dict[str, Dict[str, str]] = {}

    with input_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for idx, r in enumerate(reader, start=1):
            sid = _s(r.get("supervisor_id") or f"s{idx}")
            if not sid:
                continue

            name = _s(
                r.get("name")
                or r.get("supervisor_name")
                or r.get("supervisor_1_name")
            )

            homepage = _s(r.get("homepage") or r.get("supervisor_homepage"))
            email = _s(r.get("email") or r.get("supervisor_1_email"))
            title = _s(r.get("title"))
            scholar_url = _s(r.get("scholar_url"))

            project_text = _s(r.get("project_text"))
            work_summary = _s(r.get("work_summary"))
            department = _s(r.get("department"))

            info_by_id[sid] = {
                "supervisor_id": sid,
                "name": name,
                "homepage": homepage,
                "email": email,
                "title": title,
                "department": department,
                "scholar_url": scholar_url,
                "project_text": project_text,
                "work_summary": work_summary,
            }

    return info_by_id


def load_supervisor_papers(papers_csv: Path) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load references/data/supervisor_papers.csv.

    Return:
        supervisor_id -> list of paper dictionaries

    If the file does not exist yet, return an empty mapping.
    This allows preprocess.py to still build supervisor_detail.json
    from project descriptions only.
    """
    papers_by_id: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    if not papers_csv.exists() or papers_csv.stat().st_size == 0:
        print(f"[warn] paper CSV not found or empty: {papers_csv}")
        print("[warn] supervisor_detail.json will be built from project descriptions only.")
        return papers_by_id

    with papers_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for r in reader:
            sid = _s(r.get("supervisor_id"))
            if not sid:
                continue

            title = _s(r.get("title"))
            abstract = _s(r.get("abstract"))
            paper_id = _s(r.get("paper_id"))
            year = _s(r.get("year"))

            if not title and not abstract:
                continue

            papers_by_id[sid].append(
                {
                    "paper_id": paper_id,
                    "title": title,
                    "abstract": abstract,
                    "year": year,
                }
            )

    return papers_by_id


def build_supervisor_detail_json(
    input_csv: Path = CSV_INPUT,
    papers_csv: Path = CSV_PAPERS,
) -> Dict[str, Any]:
    """
    Build supervisor_detail.json for Stage 2.

    It keeps all supervisors from supervisors_input_expanded.csv,
    even if no papers are available.
    """
    supervisor_info = load_supervisors_input(input_csv)
    papers_by_id = load_supervisor_papers(papers_csv)

    supervisors: List[Dict[str, Any]] = []

    for sid, info in supervisor_info.items():
        raw_papers = papers_by_id.get(sid, [])
        deduped = _dedup_papers_keep_order(raw_papers)
        papers_sorted = _sort_papers_by_year_desc(deduped)

        supervisors.append(
            {
                "supervisor_id": sid,
                "name": info.get("name", ""),
                "homepage": info.get("homepage", ""),
                "email": info.get("email", ""),
                "title": info.get("title", ""),
                "department": info.get("department", ""),
                "scholar_url": info.get("scholar_url", ""),
                "project_text": info.get("project_text", ""),
                "work_summary": info.get("work_summary", ""),
                "papers": papers_sorted,
                "detail_source": {
                    "project_info": str(input_csv),
                    "papers": str(papers_csv) if papers_csv.exists() else "",
                },
            }
        )

    # Keep accidental extra paper rows, but mark them clearly.
    extra_ids = [sid for sid in papers_by_id.keys() if sid not in supervisor_info]

    for sid in extra_ids:
        raw_papers = papers_by_id.get(sid, [])
        deduped = _dedup_papers_keep_order(raw_papers)
        papers_sorted = _sort_papers_by_year_desc(deduped)

        supervisors.append(
            {
                "supervisor_id": sid,
                "name": "",
                "homepage": "",
                "email": "",
                "title": "",
                "department": "",
                "scholar_url": "",
                "project_text": "",
                "work_summary": "",
                "papers": papers_sorted,
                "note": "This supervisor_id exists in supervisor_papers.csv but not in supervisors_input_expanded.csv.",
                "detail_source": {
                    "project_info": "",
                    "papers": str(papers_csv),
                },
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "supervisor_source": str(input_csv),
        "paper_source": str(papers_csv),
        "supervisors": supervisors,
    }

    return payload


def main(
    input_csv: Path = CSV_INPUT,
    papers_csv: Path = CSV_PAPERS,
    output_json: Path = OUT_JSON,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)

    payload = build_supervisor_detail_json(
        input_csv=input_csv,
        papers_csv=papers_csv,
    )

    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    supervisors = payload.get("supervisors", []) or []
    n_supervisors = len(supervisors)
    n_with_papers = sum(1 for s in supervisors if s.get("papers"))
    n_papers = sum(len(s.get("papers", []) or []) for s in supervisors)

    print(f"[done] wrote {output_json}")
    print(f"[stats] supervisors={n_supervisors}")
    print(f"[stats] supervisors_with_papers={n_with_papers}")
    print(f"[stats] papers={n_papers}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate supervisor project descriptions and Scholarly papers into "
            "references/build/supervisor_detail.json for Stage 2."
        )
    )

    parser.add_argument(
        "--input",
        type=str,
        default=str(CSV_INPUT),
        help=f"Supervisor input CSV. Default: {CSV_INPUT}",
    )
    parser.add_argument(
        "--papers",
        type=str,
        default=str(CSV_PAPERS),
        help=f"Supervisor papers CSV. Default: {CSV_PAPERS}",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUT_JSON),
        help=f"Output supervisor_detail.json. Default: {OUT_JSON}",
    )

    args = parser.parse_args()

    main(
        input_csv=Path(args.input),
        papers_csv=Path(args.papers),
        output_json=Path(args.output),
    )