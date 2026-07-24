from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_loader import load_stage1_cards, load_supervisor_detail_by_name
from stage2_agent import build_stage2_system


def main():
    cards = load_stage1_cards()
    names = [(c.get("name") or "").strip() for c in cards if c.get("name")]

    target_name = input("Enter supervisor name to inspect: ").strip()
    detail = load_supervisor_detail_by_name(target_name, max_papers=None)
    system = build_stage2_system(detail)

    print("\n=== Selected supervisor ===")
    print(detail.get("name"))
    print("\n=== Prompt length ===")
    print(len(system), "characters")

    print("\n=== Other supervisor names accidentally present? ===")
    found_others = []
    for name in names:
        if name and name != detail.get("name") and name in system:
            found_others.append(name)

    if found_others:
        print("[warn] Other supervisor names found in Stage 2 system prompt:")
        for name in found_others:
            print("-", name)
    else:
        print("[ok] No other supervisor names found in Stage 2 system prompt.")

    print("\n=== First 2000 chars of Stage 2 system prompt ===")
    print(system[:2000])


if __name__ == "__main__":
    main()