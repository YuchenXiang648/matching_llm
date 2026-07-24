from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "references" / "data"
BUILD_DIR = ROOT / "references" / "build"

SUPERVISORS_INPUT_CSV = DATA_DIR / "supervisors_input_expanded.csv"
SCHOLAR_PAPERS_CSV = DATA_DIR / "supervisor_papers.csv"
ONLINE_PAPERS_CSV = DATA_DIR / "online_supervisor_papers.csv"

RAW_DIR = BUILD_DIR / "opencode_plain_results"
LOG_DIR = BUILD_DIR / "opencode_plain_logs"

DEFAULT_MODEL = os.getenv(
    "OPENCODE_SEARCH_MODEL",
    "opencode/nemotron-3-ultra-free",
)
DEFAULT_PAPER_LIMIT = 5
DEFAULT_TIMEOUT_SECONDS = 600

CSV_FIELDS = [
    "supervisor_id",
    "supervisor_name",
    "paper_id",
    "title",
    "abstract",
    "year",
    "scholar_url",
    "author_lookup_mode",
    "authors",
    "venue",
    "doi",
    "source_url",
    "source_title",
    "source_type",
    "selection_bucket",
    "identity_verified",
    "identity_evidence",
    "search_model",
    "searched_at",
]

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class OnlineSearchError(RuntimeError):
    pass


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def norm(value: Any) -> str:
    return " ".join(clean(value).lower().split())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def save_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore",
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {field: clean(row.get(field)) for field in CSV_FIELDS}
            )


def load_supervisors(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing supervisor input CSV: {path}")

    supervisors: List[Dict[str, str]] = []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for index, row in enumerate(reader, start=1):
            supervisor_id = clean(
                row.get("supervisor_id") or f"s{index}"
            )
            name = clean(
                row.get("name")
                or row.get("supervisor_name")
                or row.get("supervisor_1_name")
            )

            if not supervisor_id or not name:
                continue

            supervisors.append(
                {
                    "supervisor_id": supervisor_id,
                    "name": name,
                    "title": clean(row.get("title")),
                    "department": clean(row.get("department")),
                    "email": clean(
                        row.get("email")
                        or row.get("supervisor_1_email")
                    ),
                    "homepage": clean(
                        row.get("homepage")
                        or row.get("supervisor_homepage")
                    ),
                    "scholar_url": clean(row.get("scholar_url")),
                }
            )

    return supervisors


def supervisor_ids_with_papers(path: Path) -> set[str]:
    return {
        clean(row.get("supervisor_id"))
        for row in load_csv(path)
        if clean(row.get("supervisor_id"))
        and clean(row.get("title"))
    }


def online_count(path: Path, supervisor_id: str) -> int:
    return sum(
        1
        for row in load_csv(path)
        if clean(row.get("supervisor_id")) == supervisor_id
        and clean(row.get("title"))
    )


def build_prompt(
    supervisor: Dict[str, str],
    paper_limit: int,
) -> str:
    """
    Keep the web-search request almost identical to the prompt that worked
    in the OpenCode interface. The marker format is plain text, not JSON.
    """
    return f"""
Find the last {paper_limit} titles and abstracts of papers of
{supervisor['name']} in UCL.

Make sure this is the correct UCL person:
department: {supervisor['department'] or 'unknown'}
role: {supervisor['title'] or 'unknown'}
email: {supervisor['email'] or 'unknown'}
homepage: {supervisor['homepage'] or 'unknown'}

Return each paper using exactly this plain-text format:

PAPER_START
TITLE: complete unabridged title
YEAR: four-digit year if available
AUTHORS: authors if available
VENUE: journal or conference if available
DOI: DOI if available
SOURCE: source URL if available
ABSTRACT_START
full abstract, or NOT AVAILABLE
ABSTRACT_END
PAPER_END

Repeat that block for each paper.
Do not use JSON.
Do not shorten a title with ... or ….
Do not invent an abstract.
""".strip()


def opencode_binary() -> str:
    path = shutil.which("opencode")

    if path:
        return path

    fallback = Path.home() / ".opencode" / "bin" / "opencode"

    if fallback.exists():
        return str(fallback)

    raise OnlineSearchError(
        "OpenCode was not found on PATH or at "
        "~/.opencode/bin/opencode."
    )


def save_debug_file(
    directory: Path,
    supervisor_id: str,
    suffix: str,
    content: str,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"{stamp}_{supervisor_id}_{suffix}"
    path.write_text(content, encoding="utf-8")

    return path


def run_search(
    supervisor: Dict[str, str],
    model: str,
    paper_limit: int,
    timeout_seconds: int,
) -> str:
    """
    Run one OpenCode request and capture its normal final text response.

    There is deliberately:
    - no --format json;
    - no instruction to write result.json;
    - no dependency on the OpenCode edit/write permission.
    """
    with tempfile.TemporaryDirectory(
        prefix="opencode_paper_search_"
    ) as temp:
        workdir = Path(temp)

        command = [
            opencode_binary(),
            "run",
            "--model",
            model,
            "--agent",
            "build",
            "--dir",
            str(workdir),
            "--title",
            f"Papers for {supervisor['name']}",
            build_prompt(supervisor, paper_limit),
        ]

        timeout = (
            None
            if timeout_seconds <= 0
            else timeout_seconds
        )

        try:
            result = subprocess.run(
                command,
                cwd=str(workdir),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            stdout = (
                error.stdout.decode(
                    "utf-8",
                    errors="replace",
                )
                if isinstance(error.stdout, bytes)
                else (error.stdout or "")
            )
            stderr = (
                error.stderr.decode(
                    "utf-8",
                    errors="replace",
                )
                if isinstance(error.stderr, bytes)
                else (error.stderr or "")
            )

            log_path = save_debug_file(
                LOG_DIR,
                supervisor["supervisor_id"],
                "timeout.log",
                "=== STDOUT ===\n"
                + stdout
                + "\n\n=== STDERR ===\n"
                + stderr,
            )

            raise OnlineSearchError(
                f"Search exceeded {timeout_seconds} seconds. "
                f"Log: {log_path}"
            ) from error

        stdout = ANSI_RE.sub("", result.stdout or "").strip()
        stderr = ANSI_RE.sub("", result.stderr or "").strip()

        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path = (
            RAW_DIR
            / f"{supervisor['supervisor_id']}.txt"
        )
        raw_path.write_text(stdout, encoding="utf-8")

        if stdout:
            return stdout

        log_path = save_debug_file(
            LOG_DIR,
            supervisor["supervisor_id"],
            "empty.log",
            "=== RETURN CODE ===\n"
            + str(result.returncode)
            + "\n\n=== STDOUT ===\n"
            + stdout
            + "\n\n=== STDERR ===\n"
            + stderr,
        )

        raise OnlineSearchError(
            "OpenCode returned no final text. "
            f"Log: {log_path}"
        )


def extract_label(
    block: str,
    label: str,
) -> str:
    match = re.search(
        rf"(?im)^\s*{re.escape(label)}\s*:\s*(.*)$",
        block,
    )

    return clean(match.group(1)) if match else ""


def parse_marker_blocks(text: str) -> List[Dict[str, str]]:
    blocks = re.findall(
        r"PAPER_START(.*?)PAPER_END",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    papers: List[Dict[str, str]] = []

    for block in blocks:
        title = extract_label(block, "TITLE")
        year = extract_label(block, "YEAR")
        authors = extract_label(block, "AUTHORS")
        venue = extract_label(block, "VENUE")
        doi = extract_label(block, "DOI")
        source = extract_label(block, "SOURCE")

        abstract_match = re.search(
            r"ABSTRACT_START(.*?)ABSTRACT_END",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

        abstract = (
            clean(abstract_match.group(1))
            if abstract_match
            else ""
        )

        papers.append(
            {
                "title": title,
                "year": year,
                "authors": authors,
                "venue": venue,
                "doi": doi,
                "source_url": source,
                "abstract": abstract,
            }
        )

    return papers


def parse_numbered_fallback(
    text: str,
) -> List[Dict[str, str]]:
    """
    Fallback for the natural numbered format visible in the OpenCode UI:

    1. Paper title (2025)
    Journal: ...
    Authors: ...
    Abstract: ...
    """
    starts = list(
        re.finditer(
            r"(?m)^\s*(\d+)\.\s+",
            text,
        )
    )

    papers: List[Dict[str, str]] = []

    for index, start in enumerate(starts):
        end = (
            starts[index + 1].start()
            if index + 1 < len(starts)
            else len(text)
        )

        block = text[start.end():end].strip()

        first_label = re.search(
            r"(?im)^\s*"
            r"(?:JOURNAL|VENUE|AUTHORS|ABSTRACT|DOI|SOURCE)"
            r"\s*:",
            block,
        )

        title_part = (
            block[:first_label.start()]
            if first_label
            else block.splitlines()[0]
        )

        title = " ".join(title_part.split())

        year_match = re.search(
            r"\((?:19|20)\d{2}\)\s*$",
            title,
        )

        year = ""

        if year_match:
            year = re.search(
                r"(?:19|20)\d{2}",
                year_match.group(0),
            ).group(0)
            title = title[:year_match.start()].strip()

        venue = (
            extract_label(block, "JOURNAL")
            or extract_label(block, "VENUE")
        )
        authors = extract_label(block, "AUTHORS")
        doi = extract_label(block, "DOI")
        source = extract_label(block, "SOURCE")

        abstract_match = re.search(
            r"(?is)^\s*ABSTRACT\s*:\s*(.*?)(?="
            r"^\s*(?:DOI|SOURCE|JOURNAL|VENUE|AUTHORS)\s*:|\Z)",
            block,
            flags=re.MULTILINE,
        )

        abstract = (
            clean(abstract_match.group(1))
            if abstract_match
            else ""
        )

        papers.append(
            {
                "title": title,
                "year": year,
                "authors": authors,
                "venue": venue,
                "doi": doi,
                "source_url": source,
                "abstract": abstract,
            }
        )

    return papers


def parse_search_result(
    text: str,
) -> List[Dict[str, str]]:
    papers = parse_marker_blocks(text)

    if not papers:
        papers = parse_numbered_fallback(text)

    cleaned: List[Dict[str, str]] = []
    seen: set[str] = set()

    for paper in papers:
        title = " ".join(
            clean(paper.get("title")).split()
        )

        if not title:
            continue

        if "..." in title or "…" in title:
            continue

        title_key = norm(title)

        if title_key in seen:
            continue

        seen.add(title_key)

        year_match = re.search(
            r"(?:19|20)\d{2}",
            clean(paper.get("year")),
        )
        year = year_match.group(0) if year_match else ""

        abstract = " ".join(
            clean(paper.get("abstract")).split()
        )

        if abstract.upper() in {
            "NOT AVAILABLE",
            "N/A",
            "NONE",
        }:
            abstract = ""

        if (
            "abstract not fully available"
            in abstract.lower()
            or "abstract unavailable"
            in abstract.lower()
            or "not available from search results"
            in abstract.lower()
        ):
            abstract = ""

        cleaned.append(
            {
                "title": title,
                "year": year,
                "authors": clean(
                    paper.get("authors")
                ),
                "venue": clean(paper.get("venue")),
                "doi": clean(paper.get("doi")),
                "source_url": clean(
                    paper.get("source_url")
                ),
                "abstract": abstract,
            }
        )

    return cleaned


def to_csv_rows(
    papers: Sequence[Dict[str, str]],
    supervisor: Dict[str, str],
    model: str,
    paper_limit: int,
) -> List[Dict[str, str]]:
    searched_at = now_iso()
    rows: List[Dict[str, str]] = []

    for paper in papers[:paper_limit]:
        doi = clean(paper.get("doi"))
        source_url = clean(
            paper.get("source_url")
        )

        paper_id = (
            f"doi:{doi}"
            if doi
            else source_url
            if source_url
            else (
                f"opencode:{supervisor['supervisor_id']}:"
                + norm(paper["title"]).replace(" ", "_")[:100]
            )
        )

        rows.append(
            {
                "supervisor_id": supervisor[
                    "supervisor_id"
                ],
                "supervisor_name": supervisor[
                    "name"
                ],
                "paper_id": paper_id,
                "title": paper["title"],
                "abstract": paper["abstract"],
                "year": paper["year"],
                "scholar_url": supervisor[
                    "scholar_url"
                ],
                "author_lookup_mode": (
                    "opencode_plaintext_search"
                ),
                "authors": paper["authors"],
                "venue": paper["venue"],
                "doi": doi,
                "source_url": source_url,
                "source_title": paper["title"],
                "source_type": (
                    "online search result"
                ),
                "selection_bucket": (
                    f"latest:{paper_limit}"
                ),
                "identity_verified": "true",
                "identity_evidence": (
                    "Matched using the UCL name, "
                    "department, role, email, and "
                    "homepage supplied in the search prompt."
                ),
                "search_model": model,
                "searched_at": searched_at,
            }
        )

    return rows


def replace_supervisor_rows(
    path: Path,
    supervisor: Dict[str, str],
    new_rows: Sequence[Dict[str, str]],
) -> None:
    existing = load_csv(path)

    kept = [
        row
        for row in existing
        if clean(row.get("supervisor_id"))
        != supervisor["supervisor_id"]
    ]

    kept.extend(new_rows)
    save_csv(path, kept)


def collect_one(
    supervisor: Dict[str, str],
    output_csv: Path,
    model: str,
    paper_limit: int,
    timeout_seconds: int,
) -> int:
    response = run_search(
        supervisor,
        model,
        paper_limit,
        timeout_seconds,
    )

    papers = parse_search_result(response)

    if not papers:
        raw_path = (
            RAW_DIR
            / f"{supervisor['supervisor_id']}.txt"
        )
        raise OnlineSearchError(
            "OpenCode returned text, but no paper blocks "
            "could be parsed. "
            f"Raw response: {raw_path}"
        )

    rows = to_csv_rows(
        papers,
        supervisor,
        model,
        paper_limit,
    )

    replace_supervisor_rows(
        output_csv,
        supervisor,
        rows,
    )

    return len(rows)


def batch_collect_missing_papers(
    *,
    input_csv: Path = SUPERVISORS_INPUT_CSV,
    scholar_papers_csv: Path = SCHOLAR_PAPERS_CSV,
    output_csv: Path = ONLINE_PAPERS_CSV,
    model: str = DEFAULT_MODEL,
    paper_limit: int = DEFAULT_PAPER_LIMIT,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    only: str = "",
    limit: int = 0,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    supervisors = load_supervisors(input_csv)

    if only:
        target = norm(only)
        supervisors = [
            supervisor
            for supervisor in supervisors
            if norm(supervisor["supervisor_id"])
            == target
            or norm(supervisor["name"])
            == target
        ]

    if limit > 0:
        supervisors = supervisors[:limit]

    scholar_ids = supervisor_ids_with_papers(
        scholar_papers_csv
    )

    candidates = [
        supervisor
        for supervisor in supervisors
        if supervisor["supervisor_id"]
        not in scholar_ids
    ]

    print(
        f"[info] supervisors considered: "
        f"{len(supervisors)}"
    )
    print(
        f"[info] supervisors needing online search: "
        f"{len(candidates)}"
    )
    print(
        f"[info] latest papers requested: "
        f"{paper_limit}"
    )
    print(f"[info] model: {model}")
    print(
        "[info] timeout: "
        + (
            "disabled"
            if timeout_seconds <= 0
            else f"{timeout_seconds} seconds "
            "per supervisor"
        )
    )
    print(f"[info] output: {output_csv}")

    if dry_run:
        for supervisor in candidates:
            print(
                f"- {supervisor['supervisor_id']} | "
                f"{supervisor['name']} | "
                f"existing_online_rows="
                f"{online_count(output_csv, supervisor['supervisor_id'])}"
            )
        return

    if not output_csv.exists():
        save_csv(output_csv, [])

    for index, supervisor in enumerate(
        candidates,
        start=1,
    ):
        existing_count = online_count(
            output_csv,
            supervisor["supervisor_id"],
        )

        if (
            not force
            and existing_count >= paper_limit
        ):
            print(
                f"[skip] {index}/{len(candidates)} "
                f"{supervisor['name']} "
                f"({existing_count} rows)"
            )
            continue

        print(
            f"\n[search] {index}/{len(candidates)} "
            f"{supervisor['supervisor_id']} "
            f"{supervisor['name']}"
        )

        try:
            count = collect_one(
                supervisor,
                output_csv,
                model,
                paper_limit,
                timeout_seconds,
            )
            print(f"[saved] {count} paper(s)")
        except KeyboardInterrupt:
            print("\n[interrupt] stopping safely")
            raise
        except Exception as error:
            print(f"[error] {error}")
            print(
                "[info] continuing with the next "
                "supervisor"
            )

    print(
        f"\n[done] total online rows: "
        f"{len(load_csv(output_csv))}"
    )
    print(f"[done] {output_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Use one simple OpenCode request per "
            "supervisor and save the returned paper "
            "titles and abstracts."
        )
    )
    parser.add_argument(
        "--input",
        default=str(SUPERVISORS_INPUT_CSV),
    )
    parser.add_argument(
        "--scholar-papers",
        default=str(SCHOLAR_PAPERS_CSV),
    )
    parser.add_argument(
        "--output",
        default=str(ONLINE_PAPERS_CSV),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
    )
    parser.add_argument(
        "--paper-limit",
        type=int,
        default=DEFAULT_PAPER_LIMIT,
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Use 0 for no timeout.",
    )
    parser.add_argument("--only", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--force",
        action="store_true",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    args = parser.parse_args()

    batch_collect_missing_papers(
        input_csv=Path(args.input),
        scholar_papers_csv=Path(
            args.scholar_papers
        ),
        output_csv=Path(args.output),
        model=args.model,
        paper_limit=max(1, args.paper_limit),
        timeout_seconds=args.timeout,
        only=args.only,
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
