from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from text_utils import extract_keywords

ROOT = Path(__file__).resolve().parents[1]
REFERENCES_DIR = ROOT / "references"
DATA_DIR = REFERENCES_DIR / "data"
BUILD_DIR = REFERENCES_DIR / "build"

SUPERVISORS_INPUT_CSV = DATA_DIR / "supervisors_input_expanded.csv"

# NEW:
# This file is generated from supervisors_input.csv project_text.
# It replaces the old paper-based supervisor_summaries.json for Stage 1 card building.
SUPERVISOR_PROJECT_SUMMARIES_JSON = BUILD_DIR / "supervisor_project_summaries.json"

# OLD:
# Keep this file untouched. It was generated from paper summaries in the older pipeline.
# Do not use it for the current project-text-based Stage 1 cards.
OLD_SUPERVISOR_SUMMARIES_JSON = BUILD_DIR / "supervisor_summaries.json"

OUT_JSON = DATA_DIR / "stage1_supervisor_cards.json"


def infer_keywords(*parts: str) -> List[str]:
    text = " ".join(x for x in parts if x)
    return extract_keywords(text, top_k=12)


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def load_summary_items(path: Path) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    summaries_by_id: Dict[str, Dict[str, Any]] = {}
    summaries_by_name: Dict[str, Dict[str, Any]] = {}

    if not path.exists():
        print(f"[warn] summary file not found: {path}")
        print("[warn] build_stage1_cards.py will fall back to raw project_text, which may be too long.")
        return summaries_by_id, summaries_by_name

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}")

    for item in data:
        if not isinstance(item, dict):
            continue

        sid = str(item.get("supervisor_id") or "").strip()
        name = str(item.get("name") or "").strip()

        if sid:
            summaries_by_id[sid] = item
        if name:
            summaries_by_name[_norm(name)] = item

    return summaries_by_id, summaries_by_name


def main() -> None:
    summaries_by_id, summaries_by_name = load_summary_items(SUPERVISOR_PROJECT_SUMMARIES_JSON)

    rows: List[Dict[str, Any]] = []

    if not SUPERVISORS_INPUT_CSV.exists():
        raise FileNotFoundError(f"Missing {SUPERVISORS_INPUT_CSV}")

    with SUPERVISORS_INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for r in reader:
            sid = (r.get("supervisor_id") or "").strip()
            name = (r.get("name") or r.get("supervisor_1_name") or "").strip()

            if not name:
                continue

            summary_item = summaries_by_id.get(sid) or summaries_by_name.get(_norm(name)) or {}

            project_text = (r.get("project_text") or "").strip()
            work_summary = (r.get("work_summary") or "").strip()

            # Prefer the new project-text-based summary.
            # Only fall back to raw project_text if the new summary is missing.
            summary = (summary_item.get("summary") or "").strip()
            if not summary:
                summary = " ".join(x for x in [project_text, work_summary] if x).strip()

            if not summary:
                summary = "(no summary available)"

            summary_keywords = summary_item.get("keywords") or []
            if not isinstance(summary_keywords, list):
                summary_keywords = []

            inferred_keywords = infer_keywords(project_text, work_summary, summary)

            # Put LLM-extracted keywords first, then fallback extracted keywords.
            keywords: List[str] = []
            seen = set()
            for kw in list(summary_keywords) + inferred_keywords:
                k = str(kw).strip()
                key = _norm(k)
                if not key or key in seen:
                    continue
                seen.add(key)
                keywords.append(k)

            project_style = summary_item.get("project_style") or []
            if not isinstance(project_style, list):
                project_style = []

            preferred_background = summary_item.get("preferred_background") or []
            if not isinstance(preferred_background, list):
                preferred_background = []

            project_titles = summary_item.get("project_titles") or []
            if not isinstance(project_titles, list):
                project_titles = []

            rows.append(
                {
                    "supervisor_id": sid,
                    "name": name,
                    "title": (r.get("title") or "").strip(),
                    "department": (r.get("department") or "").strip(),
                    "homepage": (r.get("homepage") or summary_item.get("homepage") or "").strip(),
                    "research_areas": keywords[:4],
                    "summary": summary,
                    "project_titles": project_titles,
                    "project_style": project_style,
                    "preferred_background": preferred_background,
                    "keywords": keywords,
                    "summary_source": "supervisor_project_summaries.json" if summary_item else "raw_project_text_fallback",
                }
            )

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[done] wrote {OUT_JSON}")
    print(f"[done] supervisor cards: {len(rows)}")


if __name__ == "__main__":
    main()