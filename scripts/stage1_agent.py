from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_client import RemoteChatModel, extract_json
from profile_loader import load_stage1_cards

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "stage1-profile-match" / "SKILL.md"

STAGE1_CONVERSATION_NUM_CTX = 8192
STAGE1_FINAL_MATCH_NUM_CTX = 16384
STAGE1_EXCLUSION_NUM_CTX = 8192

STAGE1_MIN_STUDENT_TURNS = 4
STAGE1_MAX_STUDENT_TURNS = 7
FINAL_MATCH_MAX_ATTEMPTS = 3
EXCLUSION_BATCH_SIZE = 7
EXCLUSION_MAX_ATTEMPTS = 2

STAGE1_QUESTION_STYLE_GUIDANCE = (
    "Question-style policy:\n"
    "- Use a layered question: first ask an open question, then add optional "
    "examples only when they help the student understand what kind of answer is useful.\n"
    "- The main question should normally begin with What, Which, How, Describe, "
    "or Tell me about.\n"
    "- Do not default to the pattern 'Are you more interested in A or B?'.\n"
    "- Examples must be non-exhaustive. Prefer three or more brief examples followed "
    "by 'or another direction' rather than presenting only two opposing choices.\n"
    "- A choice-assisted question is allowed when the student gives a very brief, "
    "uncertain, or 'I do not know' answer. Even then, invite the student to explain "
    "what appeals to them or to describe a different direction.\n"
    "- Do not use choice-assisted questions in consecutive turns.\n"
    "- After a short answer, ask for one concrete motivation, desired outcome, "
    "problem, experience, or example rather than immediately giving another choice.\n"
    "- Keep the question easy to answer. The student may respond with either a short "
    "phrase or a longer explanation.\n"
)


def load_skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def clean_stage1_output(text: str) -> str:
    """Remove unwanted role or speaker labels from model output."""
    text = (text or "").strip()
    text = re.sub(
        r"(?im)^\s*(?:"
        r"Dr\.?\s+[A-Z][A-Za-z.\-]*(?:\s+[A-Z][A-Za-z.\-]*){0,3}"
        r"|Prof\.?\s+[A-Z][A-Za-z.\-]*(?:\s+[A-Z][A-Za-z.\-]*){0,3}"
        r"|Professor\s+[A-Z][A-Za-z.\-]*(?:\s+[A-Z][A-Za-z.\-]*){0,3}"
        r"|Advisor|Assistant|AI|Agent"
        r")\s*:\s*",
        "",
        text,
    )
    text = re.sub(r"(?im)^\s*#{1,6}\s*(advisor|assistant|agent)\s*:\s*", "", text)
    return text.strip()


def build_stage1_snapshots(cards: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Pass every supervisor's complete Stage 1 summary without shortening it."""
    snapshots: List[Dict[str, str]] = []
    for card in cards:
        name = str(card.get("name") or "").strip()
        summary = str(card.get("summary") or "").strip()
        if name and summary:
            snapshots.append({"name": name, "summary": summary})
    return snapshots


def compress_student_profile(student_profile: Dict[str, Any]) -> Dict[str, Any]:
    """Build Stage 1 input using the complete extracted CV."""
    return {
        "cv_keywords": student_profile.get("cv_keywords", []),
        "interest_text": str(student_profile.get("interest_text") or "").strip(),
        "preference_text": str(student_profile.get("preference_text") or "").strip(),
        "cv_text": str(student_profile.get("cv_text") or "").strip(),
    }


def build_full_student_evidence(
    student_profile: Dict[str, Any],
    conversation_text: str,
) -> Dict[str, str]:
    """Build the original evidence used by final matching and exclusion analysis."""
    return {
        "full_cv_text": str(student_profile.get("cv_text") or "").strip(),
        "original_interest_text": str(student_profile.get("interest_text") or "").strip(),
        "original_preference_text": str(student_profile.get("preference_text") or "").strip(),
        "full_stage1_conversation": conversation_text,
    }


def build_conversation_text(
    history: List[Dict[str, str]],
    latest_user_message: str,
) -> str:
    """Preserve the complete chronological Stage 1 conversation."""
    lines: List[str] = []
    for turn in history:
        user_text = str(turn.get("user") or "").strip()
        assistant_text = str(turn.get("assistant") or "").strip()
        if user_text:
            lines.append(f"Student: {user_text}")
        if assistant_text:
            lines.append(f"Agent: {assistant_text}")

    latest = str(latest_user_message or "").strip()
    if latest:
        lines.append(f"Student: {latest}")
    return "\n".join(lines)


def build_stage1_system() -> str:
    """Build the system prompt for the guided Stage 1 conversation."""
    skill = load_skill_text()
    return (
        "You are the Stage 1 supervisor matching agent for a student-supervisor matching system.\n\n"
        f"Follow this skill definition strictly:\n{skill}\n\n"
        f"{STAGE1_QUESTION_STYLE_GUIDANCE}\n"
        "Conversation rules:\n"
        "1. Ask exactly one open-ended, matching-oriented question at a time.\n"
        "2. Invite the student to explain their interests in their own words, including motivations, examples, or kinds of problems they would enjoy.\n"
        "3. Do not frame the question as a forced choice between two options. Examples may be offered only as non-exhaustive illustrations, and the student must be free to describe another direction.\n"
        "4. Across the conversation, clarify research topics or problems, desired application domains, project style, methods or tools, strengths, dislikes, and constraints.\n"
        "5. Do not repeatedly ask about a dimension that the student has already answered clearly.\n"
        "6. A student may legitimately have no fixed method preference; do not keep pressing for one after they say so.\n"
        "7. Do not mention supervisor names or internal workflow terms before recommendations are ready.\n"
        "8. Treat the student's own statements as preference evidence. Options merely suggested by the agent are not student preferences.\n"
        "9. Current explicit preferences override interests merely inferred from the CV.\n"
        "10. Application-domain and research-topic alignment are more important than shared methods alone.\n"
    )


def opening_message(model: RemoteChatModel, student_profile: Dict[str, Any]) -> str:
    student_input = compress_student_profile(student_profile)
    system = build_stage1_system()
    prompt = (
        "The student has just started using the system.\n"
        f"Student evidence currently available:\n{json.dumps(student_input, ensure_ascii=False, indent=2)}\n\n"
        "Greet the student briefly and confirm that you have read the CV if one is present. "
        "Then ask exactly one easy-to-answer open question about the research "
        "problems, application areas, users, or outcomes they would most like "
        "to explore now. Follow the question-style policy in the system message. "
        "You may give several brief, non-exhaustive examples, but do not turn "
        "them into two opposing choices. Do not mention supervisors."
    )
    text = model.chat(
        [{"role": "user", "content": prompt}],
        system=system,
        num_ctx=STAGE1_CONVERSATION_NUM_CTX,
    )
    return clean_stage1_output(text)


def _readiness_prompt(
    student_profile: Dict[str, Any],
    conversation_text: str,
    turn_count: int,
    validation_feedback: str = "",
) -> str:
    student_input = compress_student_profile(student_profile)
    return (
        "Assess whether the conversation now contains enough current preference evidence to compare supervisors reliably.\n\n"
        f"Student evidence:\n{json.dumps(student_input, ensure_ascii=False, indent=2)}\n\n"
        f"Complete conversation:\n{conversation_text}\n\n"
        f"Number of student answers so far: {turn_count}\n\n"
        "The evidence is sufficient when the student's desired topic, problem, "
        "application direction, or intended outcome is reasonably clear, and "
        "their preferred project style is sufficiently understood. "
        "The conversation must also contain either an explicit dislike or "
        "constraint, or an explicit statement that the student has no important "
        "areas they wish to avoid. Do not infer that the student has no "
        "constraints merely because none have been volunteered. "
        "A fixed method preference is not required if the student has explicitly said they are open to methods. Do not demand unnecessary detail.\n\n"
        "If the evidence is sufficient, set ready_to_recommend to true and use an empty question. "
        "If it is not sufficient, set ready_to_recommend to false and ask exactly one open-ended question about the single most important missing dimension. "
        "The question must follow the question-style policy below. It should "
        "invite explanation in the student's own words while remaining easy "
        "to answer.\n\n"
        f"{STAGE1_QUESTION_STYLE_GUIDANCE}\n"
        "Return valid JSON only:\n"
        '{"ready_to_recommend": true, "question": ""}\n'
        "or\n"
        '{"ready_to_recommend": false, "question": "One open-ended question"}\n'
        f"{validation_feedback}"
    )


def assess_readiness_and_next_question(
    model: RemoteChatModel,
    student_profile: Dict[str, Any],
    conversation_text: str,
    turn_count: int,
) -> Dict[str, Any]:
    """Use the LLM to decide whether to recommend or ask one more open question."""
    system = (
        "You assess readiness for supervisor matching. "
        "Avoid over-interviewing the student and avoid forced-choice questions. Return JSON only."
    )
    feedback = ""
    for _ in range(2):
        raw = model.chat(
            [{"role": "user", "content": _readiness_prompt(student_profile, conversation_text, turn_count, feedback)}],
            system=system,
            temperature=0.1,
            num_ctx=STAGE1_CONVERSATION_NUM_CTX,
        )
        try:
            data = extract_json(raw)
        except Exception:
            feedback = "\nThe previous response was not valid JSON. Return only the required JSON object.\n"
            continue

        if not isinstance(data, dict):
            feedback = "\nThe previous response was not a JSON object. Return only the required JSON object.\n"
            continue

        ready = data.get("ready_to_recommend")
        question = clean_stage1_output(str(data.get("question") or ""))
        if isinstance(ready, bool) and (ready or question):
            return {"ready_to_recommend": ready, "question": question}
        feedback = "\nIf ready_to_recommend is false, question must be non-empty.\n"

    raw_question = model.chat(
        [{
            "role": "user",
            "content": (
                f"Complete conversation:\n{conversation_text}\n\n"
                "Ask one easy-to-answer question about the most important preference "
                "or constraint that is still unclear for supervisor matching. "
                "Follow the question-style policy in the system message. "
                "Return only the question."
            ),
        }],
        system=build_stage1_system(),
        num_ctx=STAGE1_CONVERSATION_NUM_CTX,
    )
    return {"ready_to_recommend": False, "question": clean_stage1_output(raw_question)}


def _normalise_result_item(item: Any, field_name: str, errors: List[str]) -> Dict[str, str] | None:
    if not isinstance(item, dict):
        errors.append(f"Every item in {field_name} must be an object.")
        return None
    name = str(item.get("name") or "").strip()
    reason = str(item.get("reason") or "").strip()
    if not name:
        errors.append(f"An item in {field_name} is missing a name.")
    if not reason:
        errors.append(f"{name or 'An item'} in {field_name} is missing a reason.")
    if not name or not reason:
        return None
    return {"name": name, "reason": reason}


def validate_shortlist_result(
    data: Any,
    allowed_names: List[str],
) -> Tuple[Dict[str, Any] | None, List[str]]:
    """Validate shortlist structure without scoring, replacing, or reordering matches."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return None, ["The top-level output must be a JSON object."]

    student_summary = str(data.get("student_summary") or "").strip()
    next_action = str(data.get("next_action") or "").strip()
    raw_recommendations = data.get("recommendations")
    raw_alternatives = data.get("closest_alternatives")

    if not student_summary:
        errors.append("student_summary must be non-empty.")
    if not next_action:
        errors.append("next_action must be non-empty.")
    if not isinstance(raw_recommendations, list):
        errors.append("recommendations must be a JSON list.")
        raw_recommendations = []
    if not isinstance(raw_alternatives, list):
        errors.append("closest_alternatives must be a JSON list.")
        raw_alternatives = []

    recommendations: List[Dict[str, str]] = []
    alternatives: List[Dict[str, str]] = []
    for item in raw_recommendations:
        clean = _normalise_result_item(item, "recommendations", errors)
        if clean:
            recommendations.append(clean)
    for item in raw_alternatives:
        clean = _normalise_result_item(item, "closest_alternatives", errors)
        if clean:
            alternatives.append(clean)

    if not 1 <= len(recommendations) <= 3:
        errors.append("recommendations must contain between one and three supervisors.")
    if len(alternatives) > 3:
        errors.append("closest_alternatives may contain at most three supervisors.")

    allowed_set = set(allowed_names)
    output_names = [x["name"] for x in recommendations + alternatives]
    invalid_names = [name for name in output_names if name not in allowed_set]
    if invalid_names:
        errors.append("Names must exactly match the allowed list: " + ", ".join(invalid_names))
    duplicates = sorted({name for name in output_names if output_names.count(name) > 1})
    if duplicates:
        errors.append("Names must not be repeated: " + ", ".join(duplicates))

    if errors:
        return None, errors
    return {
        "student_summary": student_summary,
        "recommendations": recommendations,
        "closest_alternatives": alternatives,
        "next_action": next_action,
    }, []


def build_shortlist_prompt(
    student_evidence: Dict[str, str],
    snapshots: List[Dict[str, str]],
    validation_feedback: List[str] | None = None,
) -> str:
    allowed_names = [snapshot["name"] for snapshot in snapshots]
    feedback = ""
    if validation_feedback:
        feedback = "\nCorrect these structural problems from the previous attempt:\n- " + "\n- ".join(validation_feedback) + "\n"

    return (
        "Produce the final supervisor shortlist using all original student evidence and all supervisor summaries.\n\n"
        f"FULL STUDENT EVIDENCE\n{json.dumps(student_evidence, ensure_ascii=False, indent=2)}\n\n"
        f"ALL SUPERVISOR PROJECT SUMMARIES\n{json.dumps(snapshots, ensure_ascii=False, indent=2)}\n\n"
        "Decision rules:\n"
        "1. Compare every supervisor before deciding.\n"
        "2. The student's explicit current statements override preferences merely inferred from the CV.\n"
        "3. Treat explicit dislikes and excluded domains as hard constraints.\n"
        "4. Identify each project's actual research problem and primary application domain before considering methods.\n"
        "5. Shared methods such as LLMs, RAG, machine learning, transformers, optimisation, or data analysis are not enough when the actual topic or application domain conflicts with the student's stated preferences.\n"
        "6. Never recommend a supervisor with a material hard-constraint conflict.\n"
        "7. Return one to three best available non-conflicting supervisors, ranked strongest first. If no perfect match exists, still choose the best available non-conflicting match and clearly state its limitations.\n"
        "8. Also return up to three closest alternatives that were considered but not shortlisted, ordered from closest to less suitable.\n"
        "9. Recommendation and alternative reasons must use both student evidence and the supervisor's actual project focus.\n"
        "10. Do not invent facts.\n\n"
        "Return valid JSON only with exactly this structure:\n"
        "{\n"
        '  "student_summary": "Grounded summary preserving current interests, dislikes, and constraints",\n'
        '  "recommendations": [{"name": "Exact allowed name", "reason": "Grounded reason"}],\n'
        '  "closest_alternatives": [{"name": "Exact allowed name", "reason": "Why it was not shortlisted"}],\n'
        '  "next_action": "Student-facing next step"\n'
        "}\n\n"
        f"Exact allowed names: {json.dumps(allowed_names, ensure_ascii=False)}\n"
        "Use exact names. Do not add fields. Keep each reason to one or two concise sentences.\n"
        f"{feedback}"
    )


def generate_shortlist(
    model: RemoteChatModel,
    student_evidence: Dict[str, str],
    snapshots: List[Dict[str, str]],
) -> Dict[str, Any] | None:
    allowed_names = [snapshot["name"] for snapshot in snapshots]
    system = (
        "You are the final decision-maker for supervisor matching. "
        "Application-domain and research-topic alignment take priority over shared methods. "
        "Explicit exclusions are hard constraints. Return JSON only."
    )
    validation_feedback: List[str] | None = None

    for attempt in range(1, FINAL_MATCH_MAX_ATTEMPTS + 1):
        raw = model.chat(
            [{"role": "user", "content": build_shortlist_prompt(student_evidence, snapshots, validation_feedback)}],
            system=system,
            temperature=0.1,
            num_ctx=STAGE1_FINAL_MATCH_NUM_CTX,
        )
        try:
            parsed = extract_json(raw)
        except Exception as exc:
            validation_feedback = [f"The output was not parseable JSON: {exc}"]
            print(f"[stage1] shortlist attempt {attempt} invalid: {validation_feedback}")
            continue

        validated, errors = validate_shortlist_result(parsed, allowed_names)
        if validated is not None:
            return validated
        validation_feedback = errors
        print(f"[stage1] shortlist attempt {attempt} invalid: {errors}")
    return None


def validate_exclusion_batch(
    data: Any,
    expected_names: List[str],
) -> Tuple[List[Dict[str, str]] | None, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("excluded_supervisors"), list):
        return None, ["Output must be an object containing excluded_supervisors as a list."]

    items: List[Dict[str, str]] = []
    for raw_item in data["excluded_supervisors"]:
        clean = _normalise_result_item(raw_item, "excluded_supervisors", errors)
        if clean:
            items.append(clean)

    names = [item["name"] for item in items]
    if names != expected_names:
        errors.append(
            "Names and order must exactly match the supplied batch. Expected: "
            + json.dumps(expected_names, ensure_ascii=False)
        )
    if errors:
        return None, errors
    return items, []


def generate_exclusion_batch(
    model: RemoteChatModel,
    student_evidence: Dict[str, str],
    batch: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    expected_names = [item["name"] for item in batch]
    feedback: List[str] | None = None
    system = (
        "You explain why already non-shortlisted supervisors were not recommended. "
        "Use the student's explicit preferences and each supplied project summary. Return JSON only."
    )

    for attempt in range(1, EXCLUSION_MAX_ATTEMPTS + 1):
        feedback_text = ""
        if feedback:
            feedback_text = "\nCorrect these structural errors:\n- " + "\n- ".join(feedback) + "\n"
        prompt = (
            f"FULL STUDENT EVIDENCE\n{json.dumps(student_evidence, ensure_ascii=False, indent=2)}\n\n"
            f"NON-SHORTLISTED SUPERVISORS\n{json.dumps(batch, ensure_ascii=False, indent=2)}\n\n"
            "These supervisors have already been excluded from the shortlist. For each one, write one concise reason grounded in the student's evidence and the supervisor's actual project focus. "
            "Where methods overlap but the topic or application domain does not, state that clearly. "
            "Return every supplied name exactly once and in the same order.\n\n"
            "Return JSON only:\n"
            '{"excluded_supervisors": [{"name": "Exact supplied name", "reason": "One concise reason"}]}\n'
            f"{feedback_text}"
        )
        raw = model.chat(
            [{"role": "user", "content": prompt}],
            system=system,
            temperature=0.1,
            num_ctx=STAGE1_EXCLUSION_NUM_CTX,
        )
        try:
            parsed = extract_json(raw)
        except Exception as exc:
            feedback = [f"Output was not parseable JSON: {exc}"]
            print(f"[stage1] exclusion batch attempt {attempt} invalid: {feedback}")
            continue

        validated, errors = validate_exclusion_batch(parsed, expected_names)
        if validated is not None:
            return validated
        feedback = errors
        print(f"[stage1] exclusion batch attempt {attempt} invalid: {errors}")

    # If a multi-item batch fails, split it so the model has a simpler task.
    if len(batch) > 1:
        midpoint = len(batch) // 2
        return (
            generate_exclusion_batch(model, student_evidence, batch[:midpoint])
            + generate_exclusion_batch(model, student_evidence, batch[midpoint:])
        )

    # A final single-supervisor call remains LLM-generated rather than using a Python-written reason.
    item = batch[0]
    raw_reason = model.chat(
        [{
            "role": "user",
            "content": (
                f"Student evidence:\n{json.dumps(student_evidence, ensure_ascii=False, indent=2)}\n\n"
                f"Supervisor:\n{json.dumps(item, ensure_ascii=False, indent=2)}\n\n"
                "Write one concise sentence explaining why this supervisor was not shortlisted. Return only the sentence."
            ),
        }],
        system="Use only supplied evidence. Do not invent facts.",
        temperature=0.1,
        num_ctx=STAGE1_EXCLUSION_NUM_CTX,
    )
    return [{"name": item["name"], "reason": clean_stage1_output(raw_reason)}]


def generate_all_exclusions(
    model: RemoteChatModel,
    student_evidence: Dict[str, str],
    snapshots: List[Dict[str, str]],
    recommendations: List[Dict[str, str]],
    closest_alternatives: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    recommended_names = {item["name"] for item in recommendations}
    closest_names = {item["name"] for item in closest_alternatives}
    remaining = [
        snapshot for snapshot in snapshots
        if snapshot["name"] not in recommended_names and snapshot["name"] not in closest_names
    ]

    exclusions = list(closest_alternatives)
    for start in range(0, len(remaining), EXCLUSION_BATCH_SIZE):
        exclusions.extend(
            generate_exclusion_batch(
                model,
                student_evidence,
                remaining[start:start + EXCLUSION_BATCH_SIZE],
            )
        )
    return exclusions


def generate_final_stage1_result(
    model: RemoteChatModel,
    student_profile: Dict[str, Any],
    conversation_text: str,
    snapshots: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Generate shortlist first, then produce exclusion explanations in smaller batches."""
    student_evidence = build_full_student_evidence(student_profile, conversation_text)
    shortlist = generate_shortlist(model, student_evidence, snapshots)
    if shortlist is None:
        return {
            "stage1_done": False,
            "student_summary": "",
            "recommendations": [],
            "excluded_supervisors": [],
            "considered_count": len(snapshots),
            "next_action": "Please retry the matching step.",
            "matching_error": (
                "I could not produce a valid recommendation result after several model attempts. "
                "Please ask me to generate the recommendations again."
            ),
        }

    recommendations = shortlist["recommendations"]
    closest_alternatives = shortlist["closest_alternatives"]
    excluded = generate_all_exclusions(
        model,
        student_evidence,
        snapshots,
        recommendations,
        closest_alternatives,
    )

    return {
        "stage1_done": True,
        "student_summary": shortlist["student_summary"],
        "recommendations": recommendations,
        "excluded_supervisors": excluded,
        "considered_count": len(snapshots),
        "next_action": shortlist["next_action"],
    }


def continue_stage1(
    model: RemoteChatModel,
    student_profile: Dict[str, Any],
    history: List[Dict[str, str]],
    latest_user_message: str,
    turn_count: int,
    force_recommend: bool = False,
) -> Dict[str, Any]:
    """Continue open-ended interviewing or move to recommendations when ready."""
    conversation_text = build_conversation_text(history, latest_user_message)

    should_recommend = force_recommend or turn_count >= STAGE1_MAX_STUDENT_TURNS
    if not should_recommend and turn_count >= STAGE1_MIN_STUDENT_TURNS:
        readiness = assess_readiness_and_next_question(
            model,
            student_profile,
            conversation_text,
            turn_count,
        )
        should_recommend = bool(readiness.get("ready_to_recommend"))
        if not should_recommend:
            return {
                "stage1_done": False,
                "message": str(readiness.get("question") or "").strip(),
            }

    if should_recommend:
        cards = load_stage1_cards()
        snapshots = build_stage1_snapshots(cards)
        return generate_final_stage1_result(
            model=model,
            student_profile=student_profile,
            conversation_text=conversation_text,
            snapshots=snapshots,
        )

    student_input = compress_student_profile(student_profile)
    system = build_stage1_system()
    prompt = (
        "Continue the guided matching conversation.\n"
        f"Complete student evidence currently available:\n{json.dumps(student_input, ensure_ascii=False, indent=2)}\n\n"
        f"Complete conversation so far:\n{conversation_text}\n\n"
        "Ask exactly one follow-up question about the single most useful "
        "preference dimension that is still unclear. Follow the question-style "
        "policy below, invite the student to explain in their own words. Briefly acknowledge the student's latest answer, then ask "
        "the question. Do not repeat a dimension that has already been answered "
        "clearly, and do not mention supervisors.\n\n"
        f"{STAGE1_QUESTION_STYLE_GUIDANCE}"
    )
    text = model.chat(
        [{"role": "user", "content": prompt}],
        system=system,
        num_ctx=STAGE1_CONVERSATION_NUM_CTX,
    )
    return {"stage1_done": False, "message": clean_stage1_output(text)}
