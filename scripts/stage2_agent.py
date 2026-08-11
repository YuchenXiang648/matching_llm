from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import json
from pathlib import Path
from typing import Any, Dict, List

from model_client import RemoteChatModel
from profile_loader import build_profile_text, load_supervisor_detail_by_name

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "stage2-supervisor-explore" / "SKILL.md"


def load_skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def compress_student_profile(student_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Stage 2 student profile: only carry what the student explicitly stated.
    Do NOT include cv_keywords, because they come from the student's old CV
    and can incorrectly bias the model's description of the supervisor.
    """
    return {
        "interest_text": student_profile.get("interest_text", ""),
        "preference_text": student_profile.get("preference_text", ""),
    }


def build_stage1_memory_text(stage1_history: List[Dict[str, str]]) -> str:
    """
    Preserve ONLY what the student said during Stage 1.
    Do NOT carry over Stage 1 assistant messages, because wrong assistant
    assumptions will poison Stage 2.
    """
    if not stage1_history:
        return "(no stage1 history available)"

    lines: List[str] = []
    for turn in stage1_history:
        user = (turn.get("user") or "").strip()
        if user:
            lines.append(f"Student: {user}")
    return "\n".join(lines)


def build_stage2_system(supervisor_detail: Dict[str, Any]) -> str:
    skill = load_skill_text()
    profile_text = build_profile_text(supervisor_detail)

    return (
        "You are the Stage 2 supervisor exploration agent.\n\n"
        f"Follow this skill definition strictly:\n{skill}\n\n"
        "=== CRITICAL SOURCE RULES ===\n"
        "The ONLY source of truth about the supervisor is the 'Detailed supervisor profile' section below.\n"
        "The student profile is ONLY for assessing fit. It must NEVER be used to describe the supervisor.\n"
        "Do NOT mention any organisation, company, lab, team, or research area unless it appears explicitly in the supervisor profile below.\n"
        "Do NOT use placeholders such as [Insert ...] or [Company Name].\n"
        "If something is not stated in the supervisor profile, say 'I don't have that information' instead of guessing.\n"
        "Do NOT invent facts.\n\n"
        "=== ROLE RULES ===\n"
        "You are a supervisor-matching assistant, not the supervisor.\n"
        "You must never speak as if you are the selected supervisor.\n"
        "You must never write role labels such as 'Student:', 'Supervisor:', 'Agent:', or similar dialogue script markers.\n"
        "You must never repeat or quote the student's latest message unless the student explicitly asks you to restate it.\n"
        "Write directly to the student in natural assistant voice.\n\n"
        "=== WHAT STAGE 2 SHOULD DO ===\n"
        "1. Explain what this supervisor actually works on — based only on the profile below.\n"
        "2. Explain what project directions seem possible under this supervisor.\n"
        "3. Give an honest fit assessment comparing the student's stated interests against the supervisor's profile.\n"
        "4. Ask one thoughtful follow-up question to help the student think more deeply.\n"
        "5. Answer any questions the student asks, using only the supervisor profile as the factual source.\n"
        "6. After a few turns, offer to help draft a first-contact email if the student feels ready.\n\n"
        f"=== DETAILED SUPERVISOR PROFILE (primary source of truth) ===\n{profile_text}\n"
    )



import re
from collections import Counter

def _remove_project_metadata_lines(text: str) -> str:
    """
    Remove Moodle/project metadata lines that should not become research keywords.
    """
    lines = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        low = s.lower()

        if not s:
            lines.append("")
            continue

        # Remove contact/admin metadata.
        if "email address" in low:
            continue
        if low.startswith("supervisor 1"):
            continue
        if low.startswith("supervisor 2"):
            continue
        if low.startswith("url"):
            continue
        if low.startswith("(url"):
            continue
        if low.startswith("figure "):
            continue

        lines.append(s)

    return "\n".join(lines).strip()


def _extract_project_title(project_text: str) -> str:
    m = re.search(r"(?im)^\s*Title:\s*(.+?)\s*$", project_text or "")
    return _clean_text(m.group(1)) if m else ""


def _extract_section(project_text: str, section_name: str) -> str:
    """
    Extract a section such as Work Summary / Scientific aims / Overview
    until the next heading-like line.
    """
    text = project_text or ""
    pattern = rf"(?is){re.escape(section_name)}\s*:\s*(.*?)(?=\n\s*(?:Title|Prerequisites|Work Summary|Scientific aims|Overview|Figure|Supervisor)\s*:|\Z)"
    m = re.search(pattern, text)
    if not m:
        return ""

    section = m.group(1)
    section = _remove_project_metadata_lines(section)
    return _clean_text(section)


def _short_text(text: str, max_chars: int = 420) -> str:
    text = _clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _first_sentence_or_short(text: str, max_chars: int = 360) -> str:
    sentences = _sent_split(text)
    if sentences:
        return _short_text(sentences[0], max_chars=max_chars)
    return _short_text(text, max_chars=max_chars)

EN_STOP = {
    "the","a","an","and","or","of","to","for","in","on","at","by","with","from",
    "is","are","was","were","be","been","being","this","that","these","those",
    "as","it","its","if","then","than","but","so","such","via","we","our","you",
    "they","their","he","she","his","her","into","out","over","under","between",
    "within","without","per","can","could","should","would","may","might","must",
    "will","shall","not","no","yes","more","most","less","least","very","much",
    "many","any","some","each","every","both","either","neither","one","two",
    "three","using","used","use","also","based","paper","study","approach",
    "method","methods","results","show","shows","work","works","new","learning",
    "model","models","system","systems"
}


def _clean_text(s: str) -> str:
    return " ".join((s or "").strip().split())


def _sent_split(text: str) -> List[str]:
    text = _clean_text(text)
    if not text:
        return []
    return [x.strip() for x in re.split(r'(?<=[.!?])\s+', text) if x.strip()]


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())


def _extract_key_phrases(detail: Dict[str, Any], top_k: int = 8) -> List[str]:
    """
    Generic phrase extraction from the selected supervisor only.
    Works for any supervisor detail object.
    """
    texts: List[str] = []

    project_text = _clean_text(detail.get("project_text", ""))
    if project_text:
        texts.append(project_text)

    for p in detail.get("papers", []) or []:
        title = _clean_text(p.get("title", ""))
        abstract = _clean_text(p.get("abstract", ""))
        if title:
            texts.append(title)
        if abstract:
            texts.append(abstract)

    full_text = " ".join(texts)
    if not full_text:
        return []

    # collect bigrams/trigrams from titles + project text first
    words = _tokenize(full_text)
    terms = [w for w in words if w not in EN_STOP and len(w) > 2]

    unigram_counts = Counter(terms)

    # bigrams
    bigrams = []
    for i in range(len(terms) - 1):
        bg = f"{terms[i]} {terms[i+1]}"
        if terms[i] not in EN_STOP and terms[i+1] not in EN_STOP:
            bigrams.append(bg)
    bigram_counts = Counter(bigrams)

    # prioritize meaningful bigrams first, then strong unigrams
    phrases: List[str] = []

    for phrase, count in bigram_counts.most_common(20):
        if count >= 2:
            phrases.append(phrase)

    for term, count in unigram_counts.most_common(30):
        if count >= 2:
            phrases.append(term)

    # keep order, remove duplicates
    seen = set()
    final = []
    for x in phrases:
        if x not in seen:
            seen.add(x)
            final.append(x)

    return final[:top_k]


def _representative_titles(detail: Dict[str, Any], max_titles: int = 3) -> List[str]:
    papers = detail.get("papers", []) or []
    titles = []
    for p in papers[:max_titles]:
        title = _clean_text(p.get("title", ""))
        year = _clean_text(str(p.get("year", "")))
        if title:
            if year:
                titles.append(f"{title} ({year})")
            else:
                titles.append(title)
    return titles


def build_supervisor_intro_from_detail(detail: Dict[str, Any]) -> str:
    """
    Deterministic supervisor/project introduction.

    Important:
    - Do not infer the supervisor's field from the student's interests.
    - Do not turn metadata such as email addresses into research keywords.
    - If publication data is unavailable, introduce the available project description honestly.
    """
    name = _clean_text(detail.get("name", "")) or "This supervisor"
    project_text_raw = detail.get("project_text", "") or ""
    project_text = _remove_project_metadata_lines(project_text_raw)

    papers = detail.get("papers", []) or []
    title = _extract_project_title(project_text_raw)
    work_summary = _extract_section(project_text_raw, "Work Summary")
    scientific_aims = _extract_section(project_text_raw, "Scientific aims")
    overview = _extract_section(project_text_raw, "Overview")

    intro_parts: List[str] = []

    if papers:
        phrases = _extract_key_phrases(
            {
                **detail,
                "project_text": project_text,
            },
            top_k=6,
        )
        if phrases:
            intro_parts.append(
                f"{name}'s available profile suggests work related to {', '.join(phrases[:5])}."
            )
        else:
            intro_parts.append(
                f"{name}'s available profile includes project information and publication data."
            )
    else:
        intro_parts.append(
            f"{name}'s available local profile is mainly based on the listed project description rather than publication data."
        )

    if title:
        intro_parts.append(f"The listed project is **{title}**.")

    if work_summary:
        intro_parts.append(
            "In simple terms, the project asks the student to "
            + _first_sentence_or_short(work_summary, max_chars=360)
        )
    elif overview:
        intro_parts.append(
            "In simple terms, the project is about "
            + _first_sentence_or_short(overview, max_chars=360)
        )
    elif scientific_aims:
        intro_parts.append(
            "The scientific aim is to "
            + _first_sentence_or_short(scientific_aims, max_chars=360)
        )
    elif project_text:
        intro_parts.append(
            "The main available description says: "
            + _first_sentence_or_short(project_text, max_chars=360)
        )
    else:
        intro_parts.append(
            "I only have limited local information for this supervisor, so I should avoid guessing unsupported research details."
        )

    return " ".join(intro_parts).strip()

def clean_stage2_reply(text: str) -> str:
    """
    Remove bad Stage 2 output patterns:
    - role-play labels like Student:/Supervisor:/Agent:
    - meta prefixes like 'Okay, let's continue the Stage 2 conversation.'
    - full restatement of the student's latest message
    """
    text = (text or "").strip()

    # Remove common meta opener
    bad_prefixes = [
    "Okay, let's continue the Stage 2 conversation.",
    "Let's continue the Stage 2 conversation.",
    "Here is the continuation of the Stage 2 conversation.",
    "Okay, based on the student’s statements and assuming the supervisor profile in the system message provides the relevant information,",
    "Okay, based on the student's statements and assuming the supervisor profile in the system message provides the relevant information,",
    ]
    for p in bad_prefixes:
        if text.startswith(p):
            text = text[len(p):].strip()

    lines = [ln.strip() for ln in text.splitlines()]

    cleaned: List[str] = []
    for ln in lines:
        lower = ln.lower()

        # Drop script-like role labels entirely
        if lower.startswith("student:"):
            continue
        if lower.startswith("supervisor:"):
            continue
        if lower.startswith("agent:"):
            continue

        cleaned.append(ln)

    text = "\n".join(x for x in cleaned if x).strip()

    # Remove accidental leading dialogue markers if still present inline
    text = re.sub(r"\b(Student|Supervisor|Agent)\s*:\s*", "", text)

    # Collapse too many blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


def generate_fit_and_question(
    model: RemoteChatModel,
    supervisor_detail: Dict[str, Any],
    student_profile: Dict[str, Any],
    stage1_summary: str,
    stage1_history: List[Dict[str, str]],
) -> str:
    """
    Generate ONLY the student-fit analysis and one guiding question.

    The model must not use the student's interests to invent supervisor facts.
    """
    system = build_stage2_system(supervisor_detail)
    compact_student = compress_student_profile(student_profile)
    stage1_memory = build_stage1_memory_text(stage1_history)

    prompt = (
        "You are writing the Match reflection section for the selected "
        "supervisor.\n\n"

        "IMPORTANT SEPARATION RULE:\n"
        "- Supervisor and project facts come ONLY from the Detailed "
        "Supervisor Profile in the system message.\n"
        "- Student information is used only to assess direct alignment, "
        "adjacent relevance, transferable strengths, gaps, and constraints.\n"
        "- Do NOT use the student's interests to claim that the supervisor "
        "currently works on an area that is absent from the profile.\n"
        "- Do NOT invent a project connection merely to make the result "
        "sound positive.\n\n"

        "FIT INTERPRETATION RULES:\n"
        "- Assess fit on a spectrum rather than as a binary match or mismatch.\n"
        "- Distinguish direct alignment, adjacent or transferable alignment, "
        "exploratory alignment, and material conflict.\n"
        "- A different previous degree, application area, or exact project "
        "topic is not automatically a mismatch.\n"
        "- Consider the actual research topic, application domain, project "
        "style, transferable skills, willingness to learn, and explicit "
        "student constraints.\n"
        "- Reserve categorical language such as 'strong mismatch', "
        "'significant divergence', or 'not suitable' for cases where the "
        "project's core purpose or required work materially conflicts with "
        "an explicit student dislike or non-negotiable constraint.\n"
        "- When alignment is adjacent rather than direct, explain honestly: "
        "what aligns, what is less direct, what knowledge or skills the "
        "student may need to develop, and why the option may still be worth "
        "exploring.\n"
        "- A possible connection between the project and the student's "
        "interests may be described as an exploratory possibility to discuss "
        "with the supervisor. Do not present that possibility as an existing "
        "supervisor project unless the profile states it.\n"
        "- Do not reject a recommendation only because it is not an exact "
        "topic match. Help the student decide whether the adjacent direction "
        "is attractive to them.\n\n"

        "=== STUDENT INFORMATION FOR FIT ASSESSMENT ONLY ===\n"
        f"Student stated interests: "
        f"{compact_student.get('interest_text', '(not provided)')}\n"
        f"Student stated preferences: "
        f"{compact_student.get('preference_text', '(not provided)')}\n"
        f"Stage 1 summary of student interests: "
        f"{stage1_summary if stage1_summary else '(not available)'}\n\n"

        "=== WHAT THE STUDENT SAID DURING STAGE 1 ===\n"
        f"{stage1_memory}\n\n"

        "Now write:\n"
        "1. A concise and balanced Fit Analysis. Begin with the strongest "
        "supported connection, then explain any less-direct aspects or "
        "learning gaps. If appropriate, identify a plausible exploratory "
        "bridge, clearly labelled as something to discuss rather than an "
        "existing confirmed project direction.\n"
        "2. One Guiding Question that helps the student decide whether they "
        "would enjoy learning the less-familiar parts of the project or "
        "exploring the possible connection.\n\n"

        "Strict rules:\n"
        "- Do not claim that the supervisor works on the student's preferred "
        "area unless it appears in the profile.\n"
        "- Do not describe an adjacent fit as a perfect fit.\n"
        "- Do not describe a non-identical topic as a categorical mismatch "
        "unless there is a material conflict.\n"
        "- Be honest about both connections and limitations.\n"
        "- Keep the response concise and student-friendly.\n"
        "- Do not start with meta text such as 'Okay, based on...'.\n"
    )

    text = model.chat([{"role": "user", "content": prompt}], system=system).strip()
    return clean_stage2_reply(text)


def opening_message(
    model: RemoteChatModel,
    supervisor_name: str,
    student_profile: Dict[str, Any],
    stage1_summary: str,
    stage1_history: List[Dict[str, str]],
) -> Dict[str, Any]:
    # Load ALL papers for Stage 2 opening
    detail = load_supervisor_detail_by_name(supervisor_name, max_papers=None)

    supervisor_intro = build_supervisor_intro_from_detail(detail)
    fit_and_question = generate_fit_and_question(
        model,
        detail,
        student_profile,
        stage1_summary,
        stage1_history,
    )

    project_text = (detail.get("project_text") or "").strip()
    if not project_text:
        project_text = "(No project description available.)"

    final_message = (
        f"## About {supervisor_name}\n"
        f"{supervisor_intro}\n\n"
        "## Current project description\n"
        f"{project_text}\n\n"
        "## Match reflection\n"
        f"{fit_and_question}"
    )

    return {"message": final_message, "detail": detail}


def continue_stage2(
    model: RemoteChatModel,
    supervisor_detail: Dict[str, Any],
    student_profile: Dict[str, Any],
    stage1_summary: str,
    stage1_history: List[Dict[str, str]],
    history: List[Dict[str, str]],
    latest_user_message: str,
) -> Dict[str, Any]:
    system = build_stage2_system(supervisor_detail)
    compact_student = compress_student_profile(student_profile)
    stage1_memory = build_stage1_memory_text(stage1_history)

    convo_lines = []
    for turn in history:
        convo_lines.append(f"Student: {turn['user']}")
        convo_lines.append(f"Agent: {turn['assistant']}")
    convo_lines.append(f"Student: {latest_user_message}")
    convo_text = "\n".join(convo_lines)

    prompt = (
        "You are continuing Stage 2 as a supervisor-matching assistant.\n\n"

        "CRITICAL GROUNDING RULE:\n"
        "The selected supervisor/project facts are ONLY the facts in the Detailed Supervisor Profile from the system message.\n"
        "The student information is only for fit reflection. It must not be used to invent or reinterpret the supervisor's work.\n\n"

        "Student information for fit reflection only:\n"
        f"- stated interests: {compact_student.get('interest_text', '(not provided)')}\n"
        f"- stated preferences: {compact_student.get('preference_text', '(not provided)')}\n"
        f"- stage 1 summary: {stage1_summary if stage1_summary else '(not available)'}\n\n"

        "What the student said during Stage 1, for fit reflection only:\n"
        f"{stage1_memory}\n\n"

        "Stage 2 conversation so far:\n"
        f"{convo_text}\n\n"

        f"The student's latest message is:\n{latest_user_message}\n\n"

        "Your job now:\n"
        "1. If the student asks what the supervisor's project does, explain the actual selected project using ONLY the supervisor profile.\n"
        "2. If the student asks about fit, compare the student's interests with the actual selected project honestly.\n"
        "3. If the student's interests are not directly related to the selected project, say that clearly and explain what kind of student the project may suit instead.\n"
        "4. If the student mainly shares thoughts, respond briefly and ask exactly one useful guiding question.\n\n"

        "Strict output rules:\n"
        "- Do NOT repeat or quote the student's latest message.\n"
        "- Do NOT write role labels such as Student:, Supervisor:, or Agent:.\n"
        "- Do NOT speak as if you are the supervisor.\n"
        "- Do NOT describe the supervisor as working on any area that appears only in the student profile.\n"
        "- Do NOT force a connection between the student's interests and the supervisor project.\n"
        "- Do NOT mention games, NPCs, VR, finance, medicine, or other student-interest areas unless they are explicitly in the supervisor profile.\n"
        "- If the selected supervisor profile does not contain publication data, say that the explanation is based on the project description.\n"
        "- Keep the reply natural, direct, and concise.\n"
    )

    text = model.chat([{"role": "user", "content": prompt}], system=system)
    text = clean_stage2_reply(text)
    return {"message": text}