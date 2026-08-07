"""
Webhook entry point.

GitHub webhook setup (repo Settings -> Webhooks -> Add webhook):
  Payload URL: https://your-host/webhook/github
  Content type: application/json
  Secret: same value as GITHUB_WEBHOOK_SECRET in .env
  Events: "Pull requests" only (or "Let me select" -> Pull requests)
"""
import logging

from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from app.config import settings
from app.security import verify_signature
from app.review_engine import process_pull_request
from app.store import store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(title="AI PR Review Bot")

# PR actions worth (re-)reviewing on. "synchronize" = new commits pushed.
REVIEWABLE_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}
# Actions where we should drop stored incremental-review state.
CLEANUP_ACTIONS = {"closed"}


@app.get("/health")
async def health():
    return {"status": "ok", "model": settings.ollama_model}


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
):
    raw_body = await request.body()

    if not verify_signature(raw_body, settings.webhook_secret, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event == "ping":
        return JSONResponse({"msg": "pong"})

    if x_github_event != "pull_request":
        return JSONResponse({"msg": f"ignored event: {x_github_event}"})

    # GitHub redelivers webhooks on timeout/retry — without this check a
    # redelivery of an already-handled event would trigger a duplicate
    # review. First-seen wins; later deliveries of the same ID are no-ops.
    if not await store.seen_delivery(x_github_delivery):
        logger.info("Ignoring redelivered webhook: %s", x_github_delivery)
        return JSONResponse({"msg": "duplicate delivery, ignored"})

    payload = await request.json()
    action = payload.get("action")
    pr = payload["pull_request"]
    owner = payload["repository"]["owner"]["login"]
    repo = payload["repository"]["name"]
    pr_number = pr["number"]

    if action in CLEANUP_ACTIONS:
        await store.clear_pr(owner, repo, pr_number)
        return JSONResponse({"msg": f"PR closed, cleared incremental state"})

    if action not in REVIEWABLE_ACTIONS:
        return JSONResponse({"msg": f"ignored action: {action}"})

    if pr.get("draft"):
        return JSONResponse({"msg": "ignored: draft PR"})

    logger.info("Queued review for %s/%s#%s (action=%s)", owner, repo, pr_number, action)

    # Return 202 immediately — GitHub times out webhooks at 10s, and a
    # review can easily take longer than that with a local LLM.
    background_tasks.add_task(process_pull_request, owner, repo, pr_number)
    return JSONResponse({"msg": "review queued"}, status_code=202)
