from __future__ import annotations

import argparse

from opencode_online_search import (
    DEFAULT_MODEL,
    DEFAULT_PAPER_LIMIT,
    DEFAULT_TIMEOUT_SECONDS,
    ONLINE_PAPERS_CSV,
    SCHOLAR_PAPERS_CSV,
    SUPERVISORS_INPUT_CSV,
    batch_collect_missing_papers,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("supervisor_name_or_id")
    parser.add_argument("--force", action="store_true")
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
    )

    args = parser.parse_args()

    batch_collect_missing_papers(
        input_csv=SUPERVISORS_INPUT_CSV,
        scholar_papers_csv=SCHOLAR_PAPERS_CSV,
        output_csv=ONLINE_PAPERS_CSV,
        model=args.model,
        paper_limit=max(1, args.paper_limit),
        timeout_seconds=args.timeout,
        only=args.supervisor_name_or_id,
        force=args.force,
    )


if __name__ == "__main__":
    main()
