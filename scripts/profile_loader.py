from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
REFERENCES_DIR = ROOT / "references"
DATA_DIR = REFERENCES_DIR / "data"
BUILD_DIR = REFERENCES_DIR / "build"
ASSETS_RUNTIME_DIR = ROOT / "assets" / "runtime"
ASSETS_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

STUDENT_PROFILE_JSON = ASSETS_RUNTIME_DIR / "student_profile.json"
STAGE1_SUPERVISOR_CARDS_JSON = BUILD_DIR / "supervisor_project_summaries.json"
SUPERVISORS_INPUT_CSV = DATA_DIR / "supervisors_input_expanded.csv"
SUPERVISOR_SUMMARIES_JSON = BUILD_DIR / "supervisor_summaries.json"
META_JSON = BUILD_DIR / "meta.json"
SUPERVISOR_DETAIL_JSON = BUILD_DIR / "supervisor_detail.json"


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def save_student_profile(profile: Dict[str, Any]) -> None:
    STUDENT_PROFILE_JSON.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def load_student_profile() -> Dict[str, Any]:
    if STUDENT_PROFILE_JSON.exists():
        return json.loads(STUDENT_PROFILE_JSON.read_text(encoding="utf-8"))
    return {}

def load_stage1_cards() -> List[Dict[str, Any]]:
    if STAGE1_SUPERVISOR_CARDS_JSON.exists():
        data = json.loads(STAGE1_SUPERVISOR_CARDS_JSON.read_text(encoding="utf-8"))
        print(f"[debug] Stage 1 loaded {len(data)} supervisors from {STAGE1_SUPERVISOR_CARDS_JSON}")
        print("[debug] First 5 supervisors:", [x.get("name") for x in data[:5]])
        print("[debug] Last 5 supervisors:", [x.get("name") for x in data[-5:]])
        return data

    raise FileNotFoundError(
        f"Missing Stage 1 supervisor summaries: {STAGE1_SUPERVISOR_CARDS_JSON}. "
        "Please run scripts/summarize_project_texts.py first."
    )


def load_supervisor_detail_by_name(name: str, max_papers: int | None = None) -> Dict[str, Any]:
    target = _norm(name)

    if SUPERVISOR_DETAIL_JSON.exists():
        payload = json.loads(SUPERVISOR_DETAIL_JSON.read_text(encoding="utf-8"))
        supervisors = payload.get("supervisors", []) if isinstance(payload, dict) else []
        for item in supervisors:
            if _norm(item.get("name", "")) == target:
                item = dict(item)
                papers = item.get("papers") or []
                if max_papers is not None:
                    papers = papers[:max_papers]
                item["papers"] = papers
                return item

    if not SUPERVISORS_INPUT_CSV.exists():
        raise FileNotFoundError(f"Missing {SUPERVISORS_INPUT_CSV}")

    base_row: Optional[Dict[str, Any]] = None
    with SUPERVISORS_INPUT_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            row_name = (r.get("name") or r.get("supervisor_1_name") or "").strip()
            if _norm(row_name) == target:
                base_row = {
                    "supervisor_id": (r.get("supervisor_id") or "").strip(),
                    "name": row_name,
                    "title": (r.get("title") or "").strip(),
                    "email": (r.get("email") or r.get("supervisor_1_email") or "").strip(),
                    "homepage": (r.get("homepage") or "").strip(),
                    "project_text": (r.get("project_text") or "").strip(),
                    "work_summary": (r.get("work_summary") or "").strip(),
                    "scholar_url": (r.get("scholar_url") or "").strip(),
                }
                break
    if base_row is None:
        raise ValueError(f"Supervisor not found: {name}")

    papers: List[Dict[str, Any]] = []
    if META_JSON.exists():
        payload = json.loads(META_JSON.read_text(encoding="utf-8"))
        for row in payload.get("paper_rows", []):
            sid = str(row.get("supervisor_id") or "").strip()
            nm = str(row.get("supervisor_name") or "").strip()
            if (base_row.get("supervisor_id") and sid == base_row["supervisor_id"]) or _norm(nm) == target:
                papers.append(
                    {
                        "title": str(row.get("title") or "").strip(),
                        "abstract": str(row.get("abstract") or "").strip(),
                        "year": str(row.get("year") or "").strip(),
                    }
                )
    papers = [p for p in papers if p.get("title")]
    papers = sorted(papers, key=lambda x: str(x.get("year") or ""), reverse=True)
    if max_papers is not None:
        papers = papers[:max_papers]
    base_row["papers"] = papers
    return base_row


def build_profile_text(detail: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"Supervisor: {detail.get('name','')}")
    if detail.get("title"):
        lines.append(f"Title/Role: {detail['title']}")
    if detail.get("homepage"):
        lines.append(f"Homepage: {detail['homepage']}")
    if detail.get("email"):
        lines.append(f"Email: {detail['email']}")
    if detail.get("project_text"):
        lines.append("")
        lines.append("Project description:")
        lines.append(detail["project_text"])
    if detail.get("work_summary"):
        lines.append("")
        lines.append("Work summary:")
        lines.append(detail["work_summary"])
    papers = detail.get("papers", []) or []
    if papers:
        lines.append("")
        lines.append("Selected papers:")
        for p in papers:
            year = p.get("year") or "n/a"
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            if abstract:
                lines.append(f"- ({year}) {title}: {abstract}")
            else:
                lines.append(f"- ({year}) {title}")
    return "\n".join(lines)
