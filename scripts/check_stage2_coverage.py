from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_loader import load_stage1_cards, load_supervisor_detail_by_name, build_profile_text


def main():
    cards = load_stage1_cards()

    print(f"[info] Stage 1 supervisors: {len(cards)}")

    ok = 0
    weak = []
    failed = []

    for i, card in enumerate(cards, start=1):
        name = (card.get("name") or "").strip()
        if not name:
            continue

        try:
            detail = load_supervisor_detail_by_name(name, max_papers=None)

            project_text = (detail.get("project_text") or "").strip()
            work_summary = (detail.get("work_summary") or "").strip()
            papers = detail.get("papers") or []
            profile_text = build_profile_text(detail)

            has_any_detail = bool(project_text or work_summary or papers)

            if has_any_detail:
                ok += 1
                print(
                    f"[ok] {i:02d} {name} | "
                    f"project_text={len(project_text)} chars | "
                    f"work_summary={len(work_summary)} chars | "
                    f"papers={len(papers)} | "
                    f"profile_text={len(profile_text)} chars"
                )
            else:
                weak.append(name)
                print(f"[weak] {i:02d} {name} | no project_text/work_summary/papers")

        except Exception as e:
            failed.append((name, str(e)))
            print(f"[fail] {i:02d} {name} | {e}")

    print("\n=== Summary ===")
    print(f"usable supervisors: {ok}")
    print(f"weak supervisors: {len(weak)}")
    print(f"failed supervisors: {len(failed)}")

    if weak:
        print("\nWeak supervisors:")
        for name in weak:
            print(f"- {name}")

    if failed:
        print("\nFailed supervisors:")
        for name, err in failed:
            print(f"- {name}: {err}")


if __name__ == "__main__":
    main()