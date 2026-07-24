# matching_llm

CLI-first, three-stage, agent-based supervisor matching system.

## Folder overview
- `AGENTS.md`: project-wide agent instructions
- `skills/`: one `SKILL.md` per stage
- `scripts/`: runtime code
- `references/`: local data, schemas, policies, and prompt guides
- `assets/`: examples and runtime state

## System Overview
```
Student
  │
  ▼
┌─────────────────────────────────────────────────────┐
│                    CLI (cli.py)                      │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │   Stage 1    │→ │   Stage 2    │→ │  Stage 3  │  │
│  │ Profile Match│  │  Supervisor  │  │  Email    │  │
│  │              │  │  Explore     │  │  Draft    │  │
│  └──────────────┘  └──────────────┘  └───────────┘  │
│         │                 │                 │         │
│    SKILL.md          SKILL.md          SKILL.md       │
└─────────────────────────────────────────────────────┘
         │                 │
         ▼                 ▼
  stage1_supervisor   supervisor_detail
     _cards.json      _by_name (local)
  (short snapshots)   (full profile)
         │
         ▼
   RemoteChatModel (gemma3:4b via Ollama)
   http://128.16.12.12:11434/api/chat
```

## What each stage does

| Stage | Input | Output |
|-------|-------|--------|
| Stage 1 | CV + short conversation + supervisor snapshots | Ranked supervisor recommendations |
| Stage 2 | One supervisor's full profile + Stage 1 summary | Deep fit analysis + talking points |
| Stage 3 | Student profile + Stage 1/2 summaries + supervisor profile | Draft first-contact email |

## Folder structure
```
matching_llm/
├── AGENTS.md                     # Agent-level instructions
├── README.md
├── skills/
│   ├── stage1_profile_match/SKILL.md
│   ├── stage2_supervisor_explore/SKILL.md
│   └── stage3_email_draft/SKILL.md
├── scripts/
│   ├── cli.py                    # Main entry point
│   ├── model_client.py           # Ollama HTTP client
│   ├── profile_loader.py         # Load student/supervisor data
│   ├── stage1_agent.py           # Stage 1 logic
│   ├── stage2_agent.py           # Stage 2 logic
│   ├── stage3_agent.py           # Stage 3 logic
│   ├── text_utils.py             # CV parsing + keyword extraction
│   ├── build_stage1_cards.py     # Build Stage 1 supervisor cards
│   ├── check_model.py            # Test model connection
│   ├── fetch_scholar.py          # Fetch papers from Google Scholar
│   ├── preprocess.py             # Aggregate supervisor data
│   ├── summarize_supervisors.py  # Summarise supervisors with LLM
│   └── requirements.txt
├── references/
│   ├── data/
│   │   ├── supervisors_input.csv
│   │   ├── supervisor_papers.csv
│   │   └── stage1_supervisor_cards.json  ← built by build_stage1_cards.py
│   ├── build/
│   │   ├── supervisor_summaries.json     ← built by summarize_supervisors.py
│   │   ├── supervisor_detail.json        ← built by preprocess.py
│   │   └── meta.json
│   ├── schemas/
│   ├── prompts/
│   └── policies/
└── assets/
    ├── examples/
    └── runtime/                  ← session state saved here
```

## How to add more supervisors

1. Add a new row to `references/data/supervisors_input.csv` with the supervisor's details.
2. Re-run the data preparation scripts (see below).
3. No code changes needed.

## Setup
```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r scripts/requirements.txt

# 3. Connect to UCL VPN (required for the model endpoint)

# 4. Test model connection
python scripts/check_model.py
```

## Data you need to copy from the old project
Copy these folders/files from the old `supervisor_matching_llm` project into this repository:

- `data/supervisors_input.csv` -> `references/data/supervisors_input.csv`
- `data/supervisor_papers.csv` -> `references/data/supervisor_papers.csv`
- `build/supervisor_summaries.json` -> `references/build/supervisor_summaries.json`
- `build/meta.json` -> `references/build/meta.json`
- optionally `build/supervisor_detail.json` -> `references/build/supervisor_detail.json`

## First-time steps
1. Create a Python virtual environment.
2. Install `scripts/requirements.txt`.
3. Connect to UCL VPN.
4. Run `python scripts/check_model.py`.
5. Run `python scripts/build_stage1_cards.py`.
6. Run `python scripts/cli.py --cv /path/to/your_cv.pdf`.
