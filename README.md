# AI PR Review Bot

Private, self-hosted service that reviews GitHub pull requests using a local Ollama LLM; focuses on bugs/security/perf (not style).

## Quick overview
- Receives GitHub `pull_request` webhooks, verifies HMAC-SHA256, and queues background reviews.
- First review fetches all changed files; subsequent `synchronize` events use commit comparisons for incremental reviews.
- Sends annotated diffs to a local Ollama model, converts model output to structured inline GitHub review comments (falls back to an issue comment if needed).

## Quickstart
1. Pull a coder model into Ollama:
   ```bash
   ollama pull qwen2.5-coder:7b
   ```
2. Copy and edit env:
   ```bash
   cp .env.example .env
   # set GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET
   ```
3. Run (Docker Compose):
   ```bash
   docker compose up --build
   ```
4. Register webhook (repo Settings → Webhooks):
   - Payload URL: `https://<host>/webhook/github`
   - Content type: `application/json`
   - Secret: same as `GITHUB_WEBHOOK_SECRET`
   - Events: Pull requests

Health check: GET /health

## Important env vars
- GITHUB_TOKEN — token with Pull requests: Read & Write
- GITHUB_WEBHOOK_SECRET — webhook HMAC secret
- OLLAMA_HOST / OLLAMA_MODEL / OLLAMA_TIMEOUT_S / OLLAMA_TEMPERATURE
- MAX_FILES_PER_PR, MAX_PATCH_LINES_PER_FILE, MAX_COMMENTS_PER_PR, REVIEW_EVENT

## Layout (key files)
```
app/
  main.py        webhook + background tasks
  security.py    HMAC verification
  config.py      env settings
  github_client.py  GitHub API calls
  diff_parser.py annotated diffs + valid comment lines
  llm_client.py  Ollama calls / parsing
  review_engine.py orchestration
  store.py       data/review_state.json persistence
.env.example
Dockerfile
docker-compose.yml
requirements.txt
```

## Limitations
- Only posts RIGHT-side (new/context) inline comments.
- Single-instance file-based state (data/review_state.json); replace with shared store for scaling.
- Small local models can be noisy — tune limits and prompt before enabling REQUEST_CHANGES.

## Contributing
Add tests for parsing/logic, document env changes in `.env.example`, and open PRs for improvements.

## License
No LICENSE file present — add one before wide reuse.
