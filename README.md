# AI PR Review Bot

Self-hosted GitHub webhook service that reviews pull requests using a local
LLM via [Ollama](https://ollama.com) — no code leaves your machine/server.

## How it works

1. GitHub sends a `pull_request` webhook (opened / synchronize / reopened) to `/webhook/github`.
2. The signature is verified (HMAC-SHA256), then the review is queued as a background task and `202` is returned immediately (GitHub kills webhooks that don't respond within 10s).
3. **First review on a PR** (`opened`/`reopened`): the bot fetches all changed files (`GET /pulls/{n}/files`).
   **Later pushes** (`synchronize`): it fetches only the delta since the last commit it reviewed (`GET /compare/{last_sha}...{new_sha}`) — so it doesn't re-flag the same unchanged code on every push. If the stored checkpoint isn't reachable anymore (force-push/rebase), it transparently falls back to a full review.
4. Skips lockfiles/binaries/oversized diffs, and sends each remaining file's diff to your local Ollama model with a strict "review for bugs/security/perf, not style" prompt.
5. The model's JSON output is parsed, hallucinated line numbers (outside the actual diff) are dropped, and everything is batched into **one** GitHub review with inline comments — not one notification per comment.
6. Duplicate webhook deliveries (GitHub retries on timeout) are recognized via `X-GitHub-Delivery` and ignored, so a slow response doesn't trigger a second review of the same push.

## Setup

### 1. Pull a code model into Ollama

```bash
ollama pull qwen2.5-coder:7b
# or, if you have the VRAM/RAM for it, a stronger option:
# ollama pull deepseek-coder-v2:16b
```

### 2. Configure

```bash
cp .env.example .env
```

Fill in:
- `GITHUB_TOKEN` — a PAT (classic, `repo` scope) or fine-grained token with **Pull requests: Read & Write** on the target repo.
- `GITHUB_WEBHOOK_SECRET` — any random string; you'll enter the same value in GitHub's webhook UI.

### 3. Run

```bash
docker compose up --build
```

This starts Ollama and the bot together. If you'd rather run Ollama on the host (e.g. for GPU access outside Docker), set `OLLAMA_HOST=http://host.docker.internal:11434` in `.env` and remove the `ollama` service from `docker-compose.yml`.

### 4. Expose the webhook and register it on GitHub

For local dev, tunnel it:
```bash
ngrok http 8000
```

Then in your repo: **Settings → Webhooks → Add webhook**
- Payload URL: `https://<your-tunnel-or-host>/webhook/github`
- Content type: `application/json`
- Secret: same as `GITHUB_WEBHOOK_SECRET`
- Events: select **Pull requests** only

Open a PR and watch the logs (`docker compose logs -f review-bot`).

## Tuning

All in `.env`:
- `REVIEW_EVENT` — start with `COMMENT`. Only switch to `REQUEST_CHANGES` once you trust the model's judgment on your codebase.
- `MAX_COMMENTS_PER_PR` / `MAX_FILES_PER_PR` / `MAX_PATCH_LINES_PER_FILE` — caps to keep review time and noise bounded on huge PRs.
- `IGNORED_PATH_PATTERNS` — glob patterns to skip (lockfiles, build output, etc.)
- `OLLAMA_TEMPERATURE` — kept low (0.1) by default since you want consistent, non-creative review output.

## Known limitations (be upfront about these if you demo this)

- Comments only anchor to the **RIGHT side** (new/added + context lines) of the diff — deletions aren't commentable in this version.
- Small local models occasionally produce comments that are technically valid but low-value; `MAX_COMMENTS_PER_PR` and the "don't nitpick style" system prompt keep this in check but won't eliminate it entirely.
- State (incremental checkpoints + webhook dedup) is a single JSON file (`data/review_state.json`), guarded by an in-process `asyncio.Lock`. Fine for one instance; if you ever scale to multiple replicas behind a load balancer, swap `app/store.py` for Redis/Postgres — the interface is small enough that callers won't need to change.
- Incremental review compares against the *last commit the bot reviewed*, not the PR's original base — so if someone manually edits a line the bot already commented on in a way that reverts it, that specific line won't get re-flagged unless it's part of a later diff hunk.

## Project structure

```
app/
  main.py           FastAPI app, webhook endpoint
  security.py       HMAC signature verification
  config.py         env-based settings
  github_client.py  GitHub REST API calls
  diff_parser.py    unified diff -> line-numbered, LLM-ready text
  llm_client.py     Ollama call + structured JSON extraction
  review_engine.py  orchestrates the above (full or incremental), posts the review
  store.py          persisted per-PR checkpoints + webhook dedup
data/
  review_state.json  created automatically at runtime (gitignored)
```
