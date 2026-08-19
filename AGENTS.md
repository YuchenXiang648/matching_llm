# AGENTS.md

## Project

This repository contains a three stage LLM based student supervisor matching system with both a browser interface and a command line interface.

The system supports:

1. CV upload and student preference exploration,
2. Stage 1 comparison across the approved supervisor set,
3. Stage 2 detailed exploration of one selected recommendation,
4. Stage 3 drafting of a first contact email.

The live workflow uses locally prepared supervisor evidence. It does not perform web search during a student session.

## Core principles

- Ground all supervisor claims in the local project and publication data supplied to the current stage.
- Never invent student achievements, supervisor facts, projects, publications, prerequisites, or prior contact.
- Treat the student's explicit current statements as the main evidence of current preference.
- Do not assume that previous CV experience automatically represents future research interest.
- Keep application domain and research problem central to matching. Shared methods alone are not sufficient.
- Preserve clear stage boundaries.
- Validate names and output structure in code, but do not replace the model's semantic ranking with a hidden numerical score.
- Keep language clear, natural, and useful to a student.
- Treat the system as decision support, not as an official allocation or suitability judgement.

## Runtime interfaces

The browser interface is served by `server.py` and `static/index.html`.

The CLI entry point is `scripts/cli.py`.

Both interfaces reuse the same core stage functions in `scripts/`.

## Model backend

The runtime model is accessed through an Ollama compatible HTTP chat endpoint.

Current default model:

```text
gemma4:26b
```

Standard runtime configuration:

- default temperature: `0.2`
- default `top_p`: `0.9`
- default `top_k`: `40`
- standard context: `8192`
- Stage 1 final comparison context: `16384`
- `think=False`
- `stream=False`
- default timeout: `180` seconds

The base URL, model name, and timeout can be overridden with:

```text
OLLAMA_BASE_URL
OLLAMA_MODEL
OLLAMA_TIMEOUT
```

## Stage 1 evidence scope

### Preference exploration

Stage 1 dialogue may read:

- complete extracted CV text,
- CV keywords,
- optional original interest statement,
- optional original preference or constraint statement,
- complete Stage 1 conversation so far.

It must **not** receive supervisor project summaries during this dialogue.

The purpose is to collect current student preferences without exposing candidate information.

Conversation behaviour:

- ask exactly one question per turn;
- use mostly open questions;
- optional examples may be provided when helpful;
- do not use repeated forced choices;
- do not repeat a preference dimension that is already clear;
- accept that a student may have no fixed method preference;
- normally collect 4 to 7 student answers;
- from the fourth answer onward, use readiness checking to avoid unnecessary questioning;
- allow the student to request recommendations earlier.

### Final Stage 1 comparison

When matching begins, supply:

- full CV,
- original interest/preference fields,
- complete Stage 1 conversation,
- all project grounded summaries from `references/build/supervisor_project_summaries.json`.

Matching rules:

- compare the student against every approved supervisor summary;
- return 1 to 3 recommendations;
- preserve the model's valid ranking order;
- allow up to 3 closest alternatives;
- validate exact allowed names, counts, reasons, and duplicates;
- retry structured generation up to 3 times if required;
- treat explicit dislikes, excluded domains, and non negotiable constraints as strong requirements;
- do not recommend a material domain conflict merely because methods overlap;
- create exclusion explanations for every non recommended supervisor after the shortlist is selected.

The application may validate structure and membership. It must not calculate a replacement semantic ranking.

## Stage 2 evidence scope

Stage 2 reads:

- one selected supervisor detailed profile only,
- optional explicit student interest and preference fields,
- Stage 1 student summary,
- only the student's own Stage 1 messages,
- current Stage 2 conversation history.

Do not add CV keywords to the compact Stage 2 student context.

Do not carry Stage 1 assistant messages into the Stage 2 student memory.

The selected detailed profile is the only source of supervisor and project facts.

The opening contains:

1. a deterministic `About` overview,
2. the complete stored current project description,
3. an LLM fit reflection,
4. one guiding question.

Use the fit spectrum:

- direct alignment,
- adjacent or transferable alignment,
- exploratory alignment,
- material conflict.

A different exact academic background or topic is not automatically a mismatch.

Do not role play as the selected supervisor.

If the student switches supervisors, reset the Stage 2 conversation state for the new selection.

## Stage 3 evidence scope

Stage 3 receives:

- selected supervisor name,
- full extracted CV,
- CV keywords,
- original interest statement,
- original preference or constraint statement,
- Stage 1 student summary,
- all student messages from Stage 1,
- complete Stage 2 conversation, including the Stage 2 opening and all later student and assistant messages.

A separate detailed supervisor profile is not passed to `draft_email`.

Supervisor and project facts in Stage 3 must be supported by the Stage 2 conversation.

Relationship rules:

- this is the student's first contact with the selected supervisor;
- Stage 1 and Stage 2 were conversations with the matching system;
- never imply a previous meeting, email exchange, guidance, feedback, or discussion with the real supervisor;
- never thank the supervisor for previous questions or advice.

The target email length is approximately 180 to 260 words.

The output is a draft for student review, not a message that should be sent without editing.

## Supervisor data preparation

### Stage 1 summaries

Primary source:

```text
references/data/supervisors_input_expanded.csv
```

Generated output:

```text
references/build/supervisor_project_summaries.json
```

Use `scripts/summarize_project_texts.py`.

The summary workflow processes one supervisor at a time and grounds each summary in that supervisor's stored project description. Longer project descriptions may be split into project aware or paragraph based chunks and merged after chunk level summarisation.

### Stage 2 detailed publication data

Primary final sources:

```text
references/data/supervisor_papers.csv
references/data/online_bibliographies/*.bib
```

Final merge/rebuild script:

```text
scripts/build_stage2_publication_data.py
```

Generated outputs include:

```text
references/build/stage2_supervisor_papers.csv
references/build/supervisor_detail.json
```

Duplicate detection is based on supervisor identity plus normalised title.

Scholar records remain primary when the same publication is also present in the reviewed online bibliography. Missing abstract or year information may be filled from the online record.

### Legacy utilities

The following may remain in the repository for development history:

```text
scripts/opencode_online_search.py
scripts/preprocess_all_papers.py
references/data/online_supervisor_papers.csv
```

Do not describe these as the final publication pipeline.

## Runtime data and privacy

Runtime state is stored under:

```text
assets/runtime/
```

Participant CVs and runtime session data must not be committed to the public repository.

Recommended ignore rules include:

```gitignore
assets/runtime/*.json
!assets/runtime/.gitkeep
__pycache__/
*.pyc
.venv/
venv/
```

## Important data paths

```text
references/data/supervisors_input_expanded.csv
references/data/supervisor_papers.csv
references/data/online_bibliographies/
references/build/supervisor_project_summaries.json
references/build/stage2_supervisor_papers.csv
references/build/supervisor_detail.json
assets/runtime/
```

## Output style

Stage 1 and Stage 2:

- clear student friendly English;
- concise but explanatory;
- no fake role labels;
- no unsupported certainty.

Stage 3:

- formal natural English;
- first contact framing;
- no bullet points in the email;
- no invented facts;
- no prior contact language.

## Scope

The system helps one student explore a fixed local set of supervisors.

It does not:

- allocate a cohort of students,
- model supervisor capacity,
- solve stable matching,
- guarantee research compatibility,
- replace official university information,
- replace the student's final judgement.
