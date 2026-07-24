# scripts/fetch_scholar.py
from __future__ import annotations

import argparse
import csv
import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

import pandas as pd
from scholarly import scholarly


ROOT = Path(__file__).resolve().parents[1]
REFERENCES_DIR = ROOT / "references"
DATA_DIR = REFERENCES_DIR / "data"
BUILD_DIR = REFERENCES_DIR / "build"

IN_CSV = DATA_DIR / "supervisors_input_expanded.csv"
OUT_CSV = DATA_DIR / "supervisor_papers.csv"
CACHE_JSON = BUILD_DIR / "fetch_scholar_cache.json"

# Keep the same logic as your old working version:
# - first N publications from the Scholar profile
# - plus recent-year papers, with best-effort abstract filling
MAX_TOP_PAPERS_PER_SUP = 5
DEFAULT_RECENT_YEARS = 4
MAX_PAPERS_PER_YEAR = 5

# Request pacing. Keep this conservative to reduce blocking risk.
SLEEP_BETWEEN_FILLS = 0.8
JITTER = 0.4


def _s(x) -> str:
    return "" if x is None else str(x).strip()


def _norm(s: str) -> str:
    return " ".join(_s(s).lower().split())


def extract_author_id(scholar_url: str) -> Optional[str]:
    """
    Extract the author id from a Google Scholar URL.

    Example:
    https://scholar.google.com/citations?user=XXXX&hl=en
    -> XXXX
    """
    scholar_url = _s(scholar_url)
    if not scholar_url:
        return None

    try:
        query = parse_qs(urlparse(scholar_url).query)
        return query.get("user", [None])[0]
    except Exception:
        return None


def fetch_author_by_id(author_id: str) -> Optional[dict]:
    """
    Retrieve a Google Scholar author by author id.
    This is the safest mode because it avoids same-name ambiguity.
    """
    try:
        author = scholarly.search_author_id(author_id)
        if not author:
            return None
        return scholarly.fill(author, sections=["publications"])
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"[warn] fetch_author_by_id failed for {author_id}: {e}")
        return None


def search_author_by_name(name: str) -> Optional[dict]:
    """
    Search author by name and take the first hit.

    This is disabled by default because it can easily pick the wrong person
    when multiple scholars have the same name.
    """
    try:
        results = scholarly.search_author(name)
        author = next(results, None)
        if not author:
            return None
        return scholarly.fill(author, sections=["publications"])
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"[warn] search_author_by_name failed for {name}: {e}")
        return None


def _peek_pub_meta(pub: dict) -> Dict[str, str]:
    """
    Read publication metadata without filling the publication.
    This is faster and reduces request volume.
    """
    bib = pub.get("bib", {}) if isinstance(pub, dict) else {}

    title = _s(bib.get("title")).replace("\n", " ")
    year = _s(bib.get("pub_year") or bib.get("year") or pub.get("year"))

    paper_id = (
        _s(pub.get("author_pub_id"))
        or _s(bib.get("citation_id"))
        or (title[:80] if title else "")
    )

    abstract = _s(bib.get("abstract")).replace("\n", " ")

    return {
        "paper_id": paper_id,
        "title": title,
        "abstract": abstract,
        "year": year if year.isdigit() else "",
    }


def _parse_year(year_text: str) -> Optional[int]:
    year_text = _s(year_text)
    if not year_text:
        return None

    year4 = year_text[:4]
    if not year4.isdigit():
        return None

    try:
        return int(year4)
    except Exception:
        return None


def _merge_filled(meta: Dict[str, str], filled: dict) -> Dict[str, str]:
    """
    Merge filled publication information back into the existing metadata.
    """
    try:
        bib = filled.get("bib", {}) if isinstance(filled, dict) else {}

        title = _s(bib.get("title") or meta.get("title")).replace("\n", " ")
        year = _s(
            bib.get("pub_year")
            or bib.get("year")
            or filled.get("year")
            or meta.get("year")
        )

        abstract = _s(
            bib.get("abstract")
            or filled.get("abstract")
            or meta.get("abstract")
        ).replace("\n", " ")

        paper_id = (
            _s(filled.get("author_pub_id"))
            or _s(bib.get("citation_id"))
            or _s(meta.get("paper_id"))
            or (title[:80] if title else "")
        )

        meta.update(
            {
                "paper_id": paper_id,
                "title": title,
                "abstract": abstract,
                "year": year if year.isdigit() else _s(meta.get("year")),
            }
        )
    except Exception:
        pass

    return meta


def _fetch_pub_with_abstract(pub: dict, fallback: Dict[str, str]) -> Dict[str, str]:
    """
    Best-effort fill of one publication to get abstract.
    If fill fails, return the existing metadata.
    """
    meta = dict(fallback)

    try:
        filled = scholarly.fill(pub)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"[warn] publication fill failed: {e}")
        return meta

    return _merge_filled(meta, filled)


def _load_cache(cache_path: Path) -> Dict:
    if not cache_path.exists():
        return {"done_supervisors": {}, "updated_at": None}

    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {"done_supervisors": {}, "updated_at": None}


def _save_cache(cache_path: Path, cache_obj: Dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_obj["updated_at"] = datetime.now().isoformat(timespec="seconds")

    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache_path)


def _load_existing_rows(out_csv: Path) -> Tuple[Set[Tuple[str, str]], Set[str], int]:
    """
    Read existing output rows to avoid duplicate work.

    Returns:
    - seen paper keys: (supervisor_id, paper title)
    - supervisor_ids that already have at least one paper in the output CSV
    - number of existing rows

    Important:
    If a supervisor_id already exists in supervisor_papers.csv, we treat that
    supervisor as already crawled and skip it by default.
    """
    if not out_csv.exists():
        return set(), set(), 0

    try:
        df = pd.read_csv(out_csv).fillna("")
    except Exception:
        return set(), set(), 0

    seen: Set[Tuple[str, str]] = set()
    existing_supervisor_ids: Set[str] = set()

    if "supervisor_id" in df.columns:
        for sid in df["supervisor_id"].astype(str):
            sid = sid.strip()
            if sid:
                existing_supervisor_ids.add(sid)

    if "supervisor_id" in df.columns and "title" in df.columns:
        for sid, title in zip(df["supervisor_id"].astype(str), df["title"].astype(str)):
            sid = sid.strip()
            title = title.strip()
            if sid and title:
                seen.add((sid, title))

    return seen, existing_supervisor_ids, len(df)


def _append_rows(out_csv: Path, rows: List[Dict[str, str]]) -> int:
    if not rows:
        return 0

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    need_header = not out_csv.exists()

    fieldnames = [
        "supervisor_id",
        "supervisor_name",
        "paper_id",
        "title",
        "abstract",
        "year",
        "scholar_url",
        "author_lookup_mode",
    ]

    with out_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if need_header:
            writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def load_supervisor_rows(input_csv: Path) -> List[Dict[str, str]]:
    if not input_csv.exists():
        raise FileNotFoundError(f"Missing input CSV: {input_csv}")

    df = pd.read_csv(input_csv).fillna("")

    rows: List[Dict[str, str]] = []

    for idx, r in df.iterrows():
        sid = _s(r.get("supervisor_id") or f"s{idx + 1}")
        name = _s(r.get("name") or r.get("supervisor_name") or r.get("supervisor_1_name"))
        scholar_url = _s(r.get("scholar_url"))
        homepage = _s(r.get("homepage"))

        if not sid and not name:
            continue

        rows.append(
            {
                "supervisor_id": sid,
                "name": name,
                "scholar_url": scholar_url,
                "homepage": homepage,
            }
        )

    return rows


def main(
    recent_years: Optional[int] = None,
    include_current_year: bool = False,
    years: Optional[List[int]] = None,
    cache: bool = True,
    force: bool = False,
    allow_name_search: bool = False,
    only: str = "",
    limit: int = 0,
    input_csv: Path = IN_CSV,
    output_csv: Path = OUT_CSV,
    cache_path: Path = CACHE_JSON,
) -> None:
    """
    Fetch papers for supervisors listed in supervisors_input_expanded.csv.

    Default behavior:
    - Use scholar_url if available.
    - Do NOT search by name unless --allow-name-search is passed.
    - Keep first 5 papers.
    - Also include recent-year papers, up to 5 per year.
    - Try to fetch abstracts for recent-year selected papers.
    - Write incrementally to supervisor_papers.csv.
    - Use cache to avoid repeating completed supervisors.
    """
    if recent_years is None:
        recent_years = DEFAULT_RECENT_YEARS

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_supervisor_rows(input_csv)

    if only:
        target = _norm(only)
        rows = [
            r for r in rows
            if _norm(r.get("supervisor_id")) == target
            or _norm(r.get("name")) == target
        ]

    if limit and limit > 0:
        rows = rows[:limit]

    if years is None:
        years = []

    year_set = {int(y) for y in years if str(y).isdigit()}

    if recent_years is not None and recent_years <= 0:
        recent_years = None

    current_year = datetime.now().year
    end_year = current_year if include_current_year else current_year - 1

    if recent_years:
        recent_range = set(range(end_year - recent_years + 1, end_year + 1))
    else:
        recent_range = set()

    def want_by_year(y_int: Optional[int]) -> bool:
        if y_int is None:
            return False
        if year_set and y_int in year_set:
            return True
        if recent_range and y_int in recent_range:
            return True
        return False

    cache_obj = _load_cache(cache_path) if cache else {"done_supervisors": {}, "updated_at": None}
    done_supervisors: Dict[str, Dict] = (
        cache_obj.get("done_supervisors", {})
        if isinstance(cache_obj, dict)
        else {}
    )

    seen_keys, existing_supervisor_ids, existing_n = _load_existing_rows(output_csv)
    if existing_n > 0:
        print(f"[cache] existing output found: {output_csv} ({existing_n} rows)")
        print(f"[cache] supervisors already in output CSV: {len(existing_supervisor_ids)}")

    print(f"[info] input supervisors: {len(rows)}")
    print(f"[info] input csv: {input_csv}")
    print(f"[info] output csv: {output_csv}")
    print(f"[info] allow_name_search={allow_name_search}")

    for idx, row_in in enumerate(rows, start=1):
        sid = _s(row_in.get("supervisor_id") or f"s{idx}")
        name = _s(row_in.get("name"))
        scholar_url = _s(row_in.get("scholar_url"))
        homepage = _s(row_in.get("homepage"))

        cached = done_supervisors.get(sid, {})
        cached_status = cached.get("status")

        if cache and not force and cached_status in {"done", "not_found", "no_scholar_url"}:
            print(f"[skip] {idx}/{len(rows)} {name} ({sid}) cached as {cached_status}")
            continue
        # If this supervisor already has at least one paper in supervisor_papers.csv,
        # skip the whole supervisor by default.
        # This avoids re-querying Google Scholar for supervisors already crawled
        # before the cache file existed.
        if not force and sid in existing_supervisor_ids:
            print(f"[skip] {idx}/{len(rows)} {name} ({sid}) already has papers in {output_csv}")
            if cache:
                done_supervisors[sid] = {
                    "status": "done",
                    "name": name,
                    "homepage": homepage,
                    "scholar_url": scholar_url,
                    "lookup_mode": "existing_output_csv",
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "note": "Skipped because supervisor_id already exists in supervisor_papers.csv.",
                }
                cache_obj["done_supervisors"] = done_supervisors
                _save_cache(cache_path, cache_obj)
            continue

        print(f"\n[author] {idx}/{len(rows)} {name} ({sid})")

        author = None
        lookup_mode = ""

        author_id = extract_author_id(scholar_url)

        if author_id:
            lookup_mode = "scholar_url"
            print(f"[info] using scholar_url author id: {author_id}")
            author = fetch_author_by_id(author_id)
        elif allow_name_search and name:
            lookup_mode = "name_search"
            print("[warn] no scholar_url; falling back to name search")
            author = search_author_by_name(name)
        else:
            print("[skip] no scholar_url and name search is disabled")
            if cache:
                done_supervisors[sid] = {
                    "status": "no_scholar_url",
                    "name": name,
                    "homepage": homepage,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                cache_obj["done_supervisors"] = done_supervisors
                _save_cache(cache_path, cache_obj)
            continue

        if author is None:
            print(f"[warn] Scholar author not found: {name}")
            if cache:
                done_supervisors[sid] = {
                    "status": "not_found",
                    "name": name,
                    "homepage": homepage,
                    "scholar_url": scholar_url,
                    "lookup_mode": lookup_mode,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                cache_obj["done_supervisors"] = done_supervisors
                _save_cache(cache_path, cache_obj)
            continue

        pubs = author.get("publications", []) or []

        top_count = 0
        per_year_counts: Dict[int, int] = {}
        new_rows: List[Dict[str, str]] = []

        for pub in pubs:
            meta = _peek_pub_meta(pub)
            title = meta["title"]
            if not title:
                continue

            y_int = _parse_year(meta["year"])

            keep_top = top_count < MAX_TOP_PAPERS_PER_SUP

            keep_year = want_by_year(y_int)
            keep_year_bucket = False

            if keep_year and y_int is not None:
                current_count = per_year_counts.get(y_int, 0)
                if current_count < MAX_PAPERS_PER_YEAR:
                    keep_year_bucket = True

            if not (keep_top or keep_year_bucket):
                if not year_set and not recent_range and top_count >= MAX_TOP_PAPERS_PER_SUP:
                    break
                continue

            row_meta = meta

            if keep_year_bucket:
                row_meta = _fetch_pub_with_abstract(pub, meta)
                per_year_counts[y_int] = per_year_counts.get(y_int, 0) + 1
                time.sleep(SLEEP_BETWEEN_FILLS + random.random() * JITTER)

            out_row = {
                "supervisor_id": sid,
                "supervisor_name": name,
                "paper_id": row_meta["paper_id"],
                "title": row_meta["title"],
                "abstract": row_meta["abstract"],
                "year": row_meta["year"],
                "scholar_url": scholar_url,
                "author_lookup_mode": lookup_mode,
            }

            key = (out_row["supervisor_id"], out_row["title"])
            if key in seen_keys:
                if keep_top:
                    top_count += 1
                continue

            new_rows.append(out_row)
            seen_keys.add(key)

            if keep_top:
                top_count += 1

            if top_count >= MAX_TOP_PAPERS_PER_SUP and recent_range:
                all_full = all(
                    per_year_counts.get(y, 0) >= MAX_PAPERS_PER_YEAR
                    for y in recent_range
                )
                if all_full:
                    break

        written = _append_rows(output_csv, new_rows)

        print(
            f"[ok] {name}: wrote={written}, "
            f"top_taken={min(top_count, MAX_TOP_PAPERS_PER_SUP)}, "
            f"year_counts={per_year_counts}"
        )

        if cache:
            done_supervisors[sid] = {
                "status": "done",
                "name": name,
                "homepage": homepage,
                "scholar_url": scholar_url,
                "lookup_mode": lookup_mode,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "wrote_rows": written,
                "top_limit": MAX_TOP_PAPERS_PER_SUP,
                "recent_years": recent_years,
                "include_current_year": include_current_year,
                "per_year_limit": MAX_PAPERS_PER_YEAR,
                "year_counts": per_year_counts,
            }
            cache_obj["done_supervisors"] = done_supervisors
            _save_cache(cache_path, cache_obj)

    print(f"\n[done] output at {output_csv}")
    if cache:
        print(f"[done] cache at {cache_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Fetch supervisor papers from Google Scholar for the current matching_llm codebase. "
            "Default input: references/data/supervisors_input_expanded.csv. "
            "Default output: references/data/supervisor_papers.csv."
        )
    )

    parser.add_argument(
        "--recent-years",
        type=int,
        default=DEFAULT_RECENT_YEARS,
        help=(
            "Include papers from the last N-year window. "
            "Default excludes current year. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--include-current-year",
        action="store_true",
        help="Include the current year in the recent-year window.",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="*",
        default=None,
        help="Additionally include papers from specific years, e.g. --years 2023 2024 2025.",
    )
    parser.add_argument(
        "--allow-name-search",
        action="store_true",
        help=(
            "Allow fallback Google Scholar author search by name when scholar_url is missing. "
            "Use carefully because of same-name ambiguity."
        ),
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="Only process one supervisor by exact supervisor_id or name, e.g. s1 or Kaan Aksit.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N supervisors after filtering. 0 means no limit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cache status and try again.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache skipping. Existing CSV rows are still deduplicated.",
    )
    parser.add_argument(
        "--input",
        type=str,
        default=str(IN_CSV),
        help=f"Input supervisors CSV. Default: {IN_CSV}",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUT_CSV),
        help=f"Output paper CSV. Default: {OUT_CSV}",
    )
    parser.add_argument(
        "--cache-path",
        type=str,
        default=str(CACHE_JSON),
        help=f"Cache JSON path. Default: {CACHE_JSON}",
    )

    args = parser.parse_args()

    main(
        recent_years=args.recent_years,
        include_current_year=args.include_current_year,
        years=args.years,
        cache=not args.no_cache,
        force=args.force,
        allow_name_search=args.allow_name_search,
        only=args.only,
        limit=args.limit,
        input_csv=Path(args.input),
        output_csv=Path(args.output),
        cache_path=Path(args.cache_path),
    )