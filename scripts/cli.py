from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from model_client import RemoteChatModel
from profile_loader import load_stage1_cards, load_student_profile, save_student_profile
from stage1_agent import continue_stage1, opening_message as stage1_opening
from stage2_agent import continue_stage2, opening_message as stage2_opening
from stage3_agent import draft_email
from text_utils import extract_keywords, read_file_to_text

ROOT = Path(__file__).resolve().parents[1]
ASSETS_RUNTIME_DIR = ROOT / "assets" / "runtime"
ASSETS_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
SESSION_JSON = ASSETS_RUNTIME_DIR / "session_state.json"


def ask_nonempty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value


def maybe(prompt: str) -> str:
    return input(prompt).strip()


def save_session(state: Dict[str, Any]) -> None:
    SESSION_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def init_student_profile(cv_path: str | None = None) -> Dict[str, Any]:
    profile = load_student_profile() or {}
    if not profile.get("cv_text"):
        if not cv_path:
            cv_path = ask_nonempty("Enter the path to your CV file: ")
        text = read_file_to_text(cv_path)
        profile["cv_path"] = cv_path
        profile["cv_text"] = text
        profile["cv_keywords"] = extract_keywords(text, top_k=12)
        interest = maybe("Optional: add a short sentence about your research interests (or press Enter to skip): ")
        prefs = maybe("Optional: add preferences/constraints (or press Enter to skip): ")
        if interest:
            profile["interest_text"] = interest
        if prefs:
            profile["preference_text"] = prefs
        save_student_profile(profile)
    return profile


def print_recommendations(data: Dict[str, Any]) -> None:
    print("\n=== Stage 1 Recommendations ===")
    print(data.get("student_summary", ""))
    recs = data.get("recommendations", []) or []
    for idx, rec in enumerate(recs, start=1):
        print(f"{idx}. {rec.get('name','')} — {rec.get('reason','')}")
    print(data.get("next_action", ""))


def choose_supervisor(stage1_result: Dict[str, Any]) -> str:
    recs = stage1_result.get("recommendations", []) or []
    if not recs:
        cards = load_stage1_cards()
        names = [x.get("name", "") for x in cards if x.get("name")]
        print("No structured recommendations were returned, so you can choose from the available supervisors:")
        for idx, name in enumerate(names, start=1):
            print(f"{idx}. {name}")
        raw = ask_nonempty("Enter the supervisor name you want to explore: ")
        return raw

    while True:
        raw = ask_nonempty("Type the name of the supervisor you want to explore: ")
        for rec in recs:
            if raw.strip().lower() == rec.get("name", "").strip().lower():
                return rec.get("name", raw) 
        for rec in recs:
            rec_name = rec.get("name", "").strip()
            if raw.strip().lower() in rec_name.lower() or rec_name.lower() in raw.strip().lower():
                print(f"(Matched to: {rec_name})")
                return rec_name
        print("Please type one of the recommended supervisor names exactly as shown above.")

def run(cv_path: str | None = None) -> None:
    model = RemoteChatModel()
    student_profile = init_student_profile(cv_path)

    state: Dict[str, Any] = {
        "student_profile": student_profile,
        "stage1_history": [],
        "stage2_history": [],
        "stage1_result": {},
        "selected_supervisor": None,
        "selected_supervisor_detail": None,
    }

    print("\n=== Supervisor Matching CLI ===")
    print("I have loaded your CV and will now begin Stage 1.\n")
    opening = stage1_opening(model, student_profile)
    print(f"Agent: {opening}")

    turns = 0
    while True:
        user = ask_nonempty("You: ")
        turns += 1
        recommend_now = turns >= 4 or user.lower() in {"recommend", "recommend now", "show recommendations"}
        result = continue_stage1(model, student_profile, state["stage1_history"], user, recommend_now=recommend_now)
        if recommend_now:
            state["stage1_result"] = result if isinstance(result, dict) else {}
            print_recommendations(state["stage1_result"])
            break
        else:
            msg = result.get("message", "") if isinstance(result, dict) else str(result)
            if msg:
                state["stage1_history"].append({"user": user, "assistant": msg})
                print(f"Agent: {msg}")

    stage1_summary = state["stage1_result"].get("student_summary", "")
    selected = choose_supervisor(state["stage1_result"])
    state["selected_supervisor"] = selected

    print("\n=== Stage 2 Supervisor Exploration ===")
    opening2 = stage2_opening(
        model,
        selected,
        student_profile,
        stage1_summary,
        state["stage1_history"],
    )
    state["selected_supervisor_detail"] = opening2["detail"]
    print(f"Agent: {opening2['message']}")

    stage2_turns = 0
    while True:
        user = ask_nonempty("You: ")
        cmd = user.strip().lower()
        if cmd in {"draft email", "email", "generate email"}:
            break
        if cmd in {"switch", "change supervisor", "back"}:
            print("\n=== Return to Stage 1 Recommendations ===")
            print_recommendations(state["stage1_result"])

            selected = choose_supervisor(state["stage1_result"])
            state["selected_supervisor"] = selected
            state["stage2_history"] = []
            stage2_turns = 0

            print("\n=== Stage 2 Supervisor Exploration ===")
            opening2 = stage2_opening(
                model,
                selected,
                student_profile,
                stage1_summary,
                state["stage1_history"],
            )
            state["selected_supervisor_detail"] = opening2["detail"]
            print(f"Agent: {opening2['message']}")
            continue
        stage2_turns += 1
        result2 = continue_stage2(
            model,
            state["selected_supervisor_detail"],
            student_profile,
            stage1_summary,
            state["stage1_history"],
            state["stage2_history"],
            user,
        )
        msg2 = result2["message"]
        state["stage2_history"].append({"user": user, "assistant": msg2})
        print(f"Agent: {msg2}")
        if stage2_turns >= 3:
            print("\nTip: if you feel ready, type 'generate email' to move to Stage 3.")

    print("\n=== Stage 3 Email Draft ===")
    email = draft_email(
        model,
        state["selected_supervisor_detail"],
        student_profile,
        stage1_summary,
        state["stage2_history"],
    )
    print(email)
    save_session(state)
    print(f"\nSession state saved to: {SESSION_JSON}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the CLI-first supervisor matching system")
    parser.add_argument("--cv", type=str, default=None, help="Optional path to the student's CV file")
    args = parser.parse_args()
    run(args.cv)
