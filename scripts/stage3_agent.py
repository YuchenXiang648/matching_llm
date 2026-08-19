from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from model_client import RemoteChatModel


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "stage3-email-draft" / "SKILL.md"


def load_skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def build_stage3_system() -> str:
    skill = load_skill_text()

    return (
        "You are the Stage 3 email drafting agent.\n\n"
        f"Follow this skill definition strictly:\n{skill}\n\n"
        "This is a FIRST-CONTACT email from the student to the selected supervisor.\n"
        "The student and supervisor have NOT met, have NOT emailed before, and have NOT had a meeting.\n"
        "The Stage 1 and Stage 2 conversations were with the matching system, NOT with the supervisor.\n"
        "So the email must NEVER thank the supervisor for previous questions, guidance, discussion, or feedback.\n"
        "Do NOT write as if there has already been contact.\n"
        "Do NOT use placeholders like [Agent Name/System].\n"
        "Use the complete Stage 2 conversation below as the factual source for the supervisor/project.\n"
        "If a factual detail is missing, simply avoid mentioning it rather than inventing it.\n"
    )


def compress_student_profile(student_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep the student facts that are useful for a first-contact email.
    """
    return {
        "interest_text": student_profile.get("interest_text", ""),
        "preference_text": student_profile.get("preference_text", ""),
        "cv_keywords": student_profile.get("cv_keywords", []),
        "cv_text": student_profile.get("cv_text", "") or "",
    }


def build_stage1_student_only_memory(
    stage1_history: List[Dict[str, str]],
) -> str:
    """
    Keep all of the student's own Stage 1 answers.
    """
    if not stage1_history:
        return "(no stage1 student messages available)"

    lines: List[str] = []

    for turn in stage1_history:
        user = (turn.get("user") or "").strip()
        if user:
            lines.append(f"Student: {user}")

    return "\n".join(lines)


def build_complete_stage2_memory(
    stage2_history: List[Dict[str, str]],
) -> str:
    """
    Keep the complete Stage 2 conversation, including the opening
    and all later student and agent messages.
    """
    if not stage2_history:
        return "(no stage2 conversation available)"

    lines: List[str] = []

    for turn in stage2_history:
        user = (turn.get("user") or "").strip()
        assistant = (turn.get("assistant") or "").strip()

        if user:
            lines.append(f"Student: {user}")

        if assistant:
            lines.append(f"Agent: {assistant}")

    return "\n".join(lines)


def clean_email_output(text: str) -> str:
    """
    Remove obvious bad template artefacts.
    """
    text = (text or "").strip()

    bad_lines_prefix = [
        "To: [Agent Name/System]",
        "Greeting: Dear [Agent Name/System],",
    ]

    lines = [ln.rstrip() for ln in text.splitlines()]
    cleaned: List[str] = []

    for ln in lines:
        stripped = ln.strip()

        if any(stripped.startswith(p) for p in bad_lines_prefix):
            continue

        cleaned.append(ln)

    text = "\n".join(cleaned).strip()

    # Remove common bad phrases implying prior contact
    replacements = [
        ("Thank you for your insightful questions and guidance. ", ""),
        ("Thank you for your questions and guidance. ", ""),
        ("Based on our conversation, ", ""),
        ("Based on our earlier conversation, ", ""),
        ("Following our discussion, ", ""),
    ]

    for old, new in replacements:
        text = text.replace(old, new)

    # collapse too many blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


def draft_email(
    model: RemoteChatModel,
    supervisor_name: str,
    student_profile: Dict[str, Any],
    stage1_summary: str,
    stage1_history: List[Dict[str, str]],
    stage2_history: List[Dict[str, str]],
) -> str:
    system = build_stage3_system()

    compact_student = compress_student_profile(student_profile)
    stage1_student_only = build_stage1_student_only_memory(stage1_history)
    stage2_complete = build_complete_stage2_memory(stage2_history)

    supervisor_name = (supervisor_name or "Professor").strip()

    prompt = (
        "Write a formal first-contact email from the STUDENT to the selected supervisor.\n\n"
        "Requirements for the email content:\n"
        "1. Start with a proper subject line.\n"
        "2. Use an appropriate greeting to the supervisor.\n"
        "3. The student should briefly introduce themselves.\n"
        "4. The student should explain their interests and possible project direction.\n"
        "5. The student should connect those interests to the selected supervisor's project or research themes.\n"
        "6. The student should politely ask whether the supervisor would be available for a meeting or further discussion.\n"
        "7. The tone must be polite, formal, and appropriate for first contact.\n"
        "8. Do NOT mention any previous conversation with the supervisor, because there has been none.\n"
        "9. Do NOT thank the supervisor for previous questions, guidance, or discussion.\n"
        "10. Do NOT address the email to an agent or system.\n"
        "11. Do NOT use bullet points.\n"
        "12. Keep it around 180 to 260 words.\n\n"
        f"Selected supervisor name: {supervisor_name}\n\n"
        "Student profile information:\n"
        f"{json.dumps(compact_student, ensure_ascii=False, indent=2)}\n\n"
        f"Stage 1 summary:\n"
        f"{stage1_summary if stage1_summary else '(not available)'}\n\n"
        "What the student said during Stage 1:\n"
        f"{stage1_student_only}\n\n"
        "Complete Stage 2 conversation:\n"
        f"{stage2_complete}\n\n"
        "Now write the final email draft.\n"
        "Output only the email itself.\n"
    )

    text = model.chat(
        [{"role": "user", "content": prompt}],
        system=system,
    )

    text = clean_email_output(text)

    return text