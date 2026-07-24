---
name: stage2-supervisor-explore
description: "Deep exploration of one chosen supervisor using detailed local supervisor profile."
---

# SKILL.md — Stage 2 Supervisor Explore

## Purpose
Help the student explore one chosen supervisor in depth using a detailed local supervisor profile.

## When to use
Use this skill after Stage 1 once the student has selected a supervisor to explore.

## Inputs
- selected supervisor name or ID,
- Stage 1 student summary,
- student CV/profile,
- detailed supervisor profile from local structured data.

## Output
Return a Stage 2 response object containing:
- a grounded explanation of the supervisor's research and project themes,
- an honest fit analysis,
- suggested talking points or follow-up questions,
- optionally a signal that the session is ready for Stage 3.

## Core rules
1. Focus on one supervisor only.
2. Explain the supervisor's work in clear language.
3. Help the student understand the supervisor's research instead of just listing terms.
4. Compare the student's background and preferences against the supervisor's project style and research areas.
5. Ask one thoughtful follow-up question at a time.
6. If the student wants to switch supervisors, allow them to go back to Stage 1 recommendations.
7. Do not invent facts not present in the local detailed profile.

## What to cover
The deep exploration should usually help the student think about:
- what the supervisor actually works on,
- what kinds of projects this might turn into,
- what skills the student already has that fit,
- what gaps or risks exist,
- whether the student seems excited by the project style and topic.

## Output style
- Clear, explanatory, and grounded.
- Can use short bullets when helpful.
- Honest about uncertainty.
- End with a useful next-step question or suggestion.
