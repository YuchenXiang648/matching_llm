6.1
uvicorn server:app --reload --port 8000


self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://128.16.12.12:11434").rstrip("/")
self.model_name = model_name or os.getenv("OLLAMA_MODEL") or "gemma3:4b"


self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
self.model_name = model_name or os.getenv("OLLAMA_MODEL") or "gemma4:e4b"


export OLLAMA_BASE_URL="http://localhost:11434"
export OLLAMA_MODEL="gemma4:e4b"
export OLLAMA_TIMEOUT="300"

uvicorn server:app --reload --port 8000


curl http://localhost:11434/api/tags

ollama pull gemma4:e4b

curl http://localhost:11434/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma4:e4b",
    "stream": false,
    "messages": [
      {
        "role": "user",
        "content": "Reply with one short sentence."
      }
    ]
  }'


ollama launch opencode
OPENCODE_ENABLE_EXA=1 ollama launch opencode




mkdir -p ~/.config/opencode
nano ~/.config/opencode/opencode.json

 "options": {
                        "reasoningEffort": "none",
                        "maxOutputTokens": 1024,
                        "temperature": 0
                    }

{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "webfetch": "allow",
    "websearch": "allow",
    "bash": "allow",
    "edit": "allow"
  }
}


Use the websearch tool with this exact query: "Kaan Aksit UCL Computational Light". If you cannot use websearch, say exactly: WEBSEARCH_NOT_AVAILABLE.

Use the websearch tool with this exact query: "Kaan Aksit UCL Computational Light". If you cannot use websearch, say exactly: WEBSEARCH_NOT_AVAILABLE.



6.12-6.19

## Stage 2 Evidence Selection Workflow

```text
Select a supervisor
        ↓
Are local Scholarly publication records available?
        ↓
    Yes                         No
     ↓                           ↓
Use the local              Is a homepage URL
publication records         already available?
                                 ↓
                            Yes       No
                             ↓         ↓
                     Use webfetch    Use websearch once
                             ↓         ↓
                    Extract and     Select only 1–2
                  summarise content  authoritative results
                                       ↓
                                   Use webfetch
                                       ↓
                              Cache the online results
```

## OpenCode Integration Workflow

```text
Stage 2 Python code
        ↓
opencode_online_search.py
        ↓
Execute opencode run
        ↓
gemma4:26b decides whether to call
websearch and/or webfetch
        ↓
Return structured online information
about the selected supervisor
        ↓
Save the result to the cache
and return it to Stage 2
```



1. Do not read, inspect, search, or use any local files in this repository. Use only online sources to find a complete list of publications from the last three years for Federico Galvanin at University College London. The only local action you may take is to save the final results as a BibTeX file at `references/data/online_bibliographies/federico_galvanin.bib`. 


2. Review and complete federico_galvanin.bib in place.

Search online thoroughly for all publications by Federico Galvanin at University College London from 2023 to 2026. Correct inaccurate entries, remove duplicates, and add all missing verified publications.

Save the corrected file directly, and report the final publication count. 

3. Read @references/data/online_bibliographies/federico_galvanin.bib carefully, and then update this bibliography such that abstracts are included in the bibliography. Do it to the best of your abilities.

4. Continue updating federico_galvanin.bib in place by adding verified full abstracts to entries whose abstracts are missing, incomplete, or unusually short. Use websearch and webfetch as needed, and do not create any other files. 





Review and complete michael_parkes.bib in place.

Search online thoroughly for all publications by Michael Parkes at University College London from 2023 to 2026. Correct inaccurate entries, remove duplicates, and add all missing verified publications.

Preserve existing verified abstracts, save the corrected file directly, and report the final publication count.

Continue updating michael_parkes.bib in place by adding verified full abstracts to entries whose abstracts are missing, incomplete, or unusually short. Use websearch and webfetch as needed, and do not create any other files.


s5 (Mose Giordano), s9 (Michael Parkes), s12 (Frank Smith), s13 (Anthony Lynas-Gray), s14 (Gabriel Facini), s15 (Kjell Erlandsson), s16 (Mark Buitelaar), s21 (Nora Yitong Qiu), s26 (Nikos Konstantinidis), s27 (William Woof), s28 (Tony Lynas-Gray), s30 (Federico Galvanin)


Mose Giordano             9
Michael Parkes           15
Frank Smith              13
Gabriel Facini           35
Kjell Erlandsson         38
Mark Buitelaar           27
Nora Yitong Qiu           4
Nikos Konstantinidis     12
William Woof              8
Tony Lynas-Gray            4
Federico Galvanin        47