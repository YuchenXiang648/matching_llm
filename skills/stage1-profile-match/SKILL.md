---
name: stage1-profile-match
description: "Build a student profile from the full extracted CV and conversation, then recommend supervisors using comprehensive project-grounded summaries."
---

# SKILL.md — Stage 1 Profile Match

## Purpose
Use the student's full extracted CV plus a short guided conversation to understand their background, interests, working style, and project preferences, then recommend suitable supervisors using comprehensive project-grounded summaries.

## When to use
Use this skill at the beginning of a new student session.

## Inputs
- CV text extracted from a PDF/DOCX/TXT file.
- Optional free-text student interest statement.
- Optional free-text student preferences or constraints.
- - A list of supervisor project summaries from `references/build/supervisor_project_summaries.json`.

## Output
Return a Stage 1 result object containing:
- a concise student summary,
- recommended supervisors ranked best-first,
- a reason for each recommendation,
- suggested next action text inviting the student to choose one supervisor for Stage 2.

## Core rules
1. Actively drive the process.
2. If the CV has not been provided yet, ask for it first.
3. Ask one question at a time.
4. Keep questions specific and non-generic.
5. Do not introduce supervisor names too early unless the skill is ready to recommend.
6. Use only the provided project-grounded supervisor summaries in this stage.
7. Never invent supervisor details that are not present in the snapshot data.
8. The number of recommendations can vary depending on the available supervisors and match quality, but it must be at least one.
9. Give clear, easy-to-understand reasons.
10. End by asking the student which supervisor they want to explore next.
11. Before the recommendation step, do not mention any supervisor names.

## Recommended question targets
The guided questions should try to clarify:
- preferred research topic(s),
- preferred project style (coding-heavy, theory-heavy, data-heavy, experimental, interdisciplinary, etc.),
- preferred application area(s),
- strengths and skills,
- dislikes or constraints,
- whether the student prefers a broad exploration or a tightly scoped project.

## Required grounding behavior
- Recommendations must be grounded in both the student profile and the supervisor snapshot fields.
- If uncertain, say what is uncertain.
- Do not oversell weak matches.
- Do not use keywords alone as the explanation; explain the match in normal language.

## Output style
- Student-friendly English.
- Natural, concise, academically appropriate.
- No fake role labels.
- No markdown tables.
