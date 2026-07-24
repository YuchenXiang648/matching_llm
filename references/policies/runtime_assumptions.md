# Runtime assumptions

- The project is CLI-first for now.
- The remote model endpoint must be reachable through UCL VPN.
- The model backend is accessed over HTTP using `/api/chat`.
- The current default model is `gemma3:4b`.
- Supervisor data is local and structured.
- Stage 2 live web search is **not enabled by default** in this first rewrite because reproducibility and source grounding matter.
- Existing local files remain the main source of truth.
