# LocalProjectManager
Local project manager inspired by the Ralph Loop. It runs on a local PC and coordinates stronger agents (Google Jules or Cursor Cloud Agents) by maintaining compressed project context and directing iterative development.

## What it does
- Loads and compresses design docs into a persistent summary.
- Pulls the repo, compresses the codebase, and checks Jules status.
- Responds to requests, reviews PRs, merges if approved, and starts the next session.
- Streams LLM traces and context in a lightweight local UI.

## Requirements
- Python 3.10+
- A running llama.cpp OpenAI-compatible server
- Jules HTTP API endpoint

## Setup
```bash
pip install -r requirements.txt
```

## Run
```bash
python main.py
```
Open `http://127.0.0.1:8765` to view the UI.

## Configuration
Set environment variables as needed:
- `LPM_LLM_BASE_URL` (default `http://localhost:8080`)
- `LPM_LLM_MODEL` (default `qwen3-8b`)
- `LPM_LLM_TEMPERATURE` (default `0.2`)
- `LPM_LLM_MAX_TOKENS` (default `8192`)
- `LPM_LLM_TIMEOUT_SECONDS` (default `600`)
- `LPM_LLM_START_CMD` (optional command to start llama.cpp)
- `LPM_LLM_START_WAIT_SECONDS` (default `20`)
- `LPM_JULES_BASE_URL` (default `https://jules.googleapis.com/v1alpha`)
- `LPM_JULES_API_KEY` (optional; sent as `X-Goog-Api-Key` for Jules API)
- `LPM_JULES_SESSION_ID` (optional; if unset, newest session is used)
- `LPM_JULES_SOURCE` (optional; used to create a session if none exists)
- `LPM_JULES_STARTING_BRANCH` (default `main`)
- `LPM_JULES_SESSION_TITLE` (optional)
- `LPM_UI_HOST` (default `127.0.0.1`)
- `LPM_UI_PORT` (default `8765`)
- `LPM_DOCS_PATH` (default `docs`)
- `LPM_INCLUDE_README` (default `1`)
- `LPM_POLL_INTERVAL_SECONDS` (default `10`)
- `LPM_MAX_ITERATIONS` (default `0` for unlimited)
- `LPM_MAX_INPUT_TOKENS` (default `4000`)
- `LPM_TARGET_CHUNK_TOKENS` (default `1000`)
- `LPM_TARGET_TOTAL_TOKENS` (default `1000`)
- `LPM_MAX_FILE_BYTES` (default `2000000`)

## Jules API expectations
The agent expects these endpoints:
- `GET /status` -> `{ "status": "inProcess|needsInput|readyForReview" }`
- `GET /request` -> `{ "id": "...", "content": "..." }`
- `POST /feedback` -> `{ "id": "...", "feedback": "..." }`
- `GET /pr` -> `{ "id": "...", "branch": "...", "url": "...", "title": "...", "description": "..." }`
- `POST /start_session` -> `{ "context": "..." }`

## User interrupts
Use the UI to send an interrupt. Send `__STOP__` to stop the loop.
