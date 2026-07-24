# AGENTS.md

## Project
This repository contains a CLI-first, three-stage, agent-based supervisor matching system for students.

The system helps a student:
1. upload a CV and optionally provide interests/preferences,
2. go through a guided Stage 1 coarse matching conversation,
3. pick one supervisor for Stage 2 deep exploration,
4. generate a first-contact email in Stage 3.

This repository is a rewrite of the earlier FastAPI/GPT4All prototype into a cleaner agent/skills structure. The current backend model is a remote chat model exposed through an HTTP API (the supervisor-provided service), not GPT4All.

## High-level principles
- The assistant must proactively guide the student through every stage.
- The assistant must never hallucinate supervisor or student facts.
- Recommendations must always be grounded in available student data and supervisor data.
- The assistant should use clear, student-friendly language instead of jargon-heavy keyword dumps.
- The assistant should be willing to filter and rank supervisors more decisively.
- The system must always recommend at least one supervisor.
- The number of Stage 1 recommendations can vary depending on match quality and the number of supervisors available.

## Current operating mode
- **CLI first**. Do not assume a web UI.
- The user runs the system from a terminal.
- The system should ask for the CV file path if it is not already provided.
- The system should store derived student state locally in `assets/runtime/`.

## Skills in this repository
- `skills/stage1-profile-match/`:
  Build a student profile from the CV and conversation, then recommend suitable supervisors using only short supervisor snapshots.

- `skills/stage2-supervisor-explore/`:
  Deep exploration of one chosen supervisor using detailed local supervisor data.

- `skills/stage3-email-draft/`:
  Generate a formal first-contact email grounded in the student profile, Stage 1/2 conversation, and the selected supervisor profile.

## What each stage should read
- **Stage 1** reads:
  - student CV text and optional student preference text,
  - short supervisor snapshots only.

- **Stage 2** reads:
  - selected supervisor detailed profile,
  - Stage 1 summary,
  - student CV/profile.

- **Stage 3** reads:
  - selected supervisor detailed profile,
  - student profile,
  - Stage 1 summary,
  - Stage 2 conversation summary.

## Data expectations
The system expects the following local data files if you want to reuse the existing project data:
- `references/data/supervisors_input.csv`
- `references/data/supervisor_papers.csv`
- `references/data/student_profile.json` (runtime-generated optional cache)
- `references/build/supervisor_summaries.json`
- `references/build/meta.json`
- `references/build/supervisor_detail.json` (optional prebuilt aggregate)

## Existing-code migration guidance
The earlier codebase contained useful logic in:
- CV parsing and keyword extraction,
- loading short supervisor summaries from CSV/JSON,
- loading one supervisor’s full profile from CSV + `meta.json`,
- Stage 1 summary generation,
- Stage 3 email drafting constraints.

These ideas should be reused where sensible, but the new repository should not depend on GPT4All.

## Model backend
The runtime model is accessed through an HTTP chat endpoint.
Default assumptions in the new scripts:
- base URL: `http://128.16.12.12:11434`
- chat endpoint: `/api/chat`
- model name: `gemma3:4b`

These must be configurable by environment variables.

## Output style requirements
- Natural, easy-to-understand English.
- Friendly but academic tone.
- Formal and polite in Stage 3 email drafting.
- Never output fake role tags, template markers, or UI text.

## Future extensions
This repository is structured so that more supervisors can be added easily by updating local structured files.
A future extension may add supervisor self-submission or automated ingestion from structured forms.
