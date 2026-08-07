"""
Lightweight JSON-file-backed store for two things:

  1. Per-PR "last reviewed commit SHA" — enables incremental review:
     on a `synchronize` event we only send the LLM the delta between
     the last commit we reviewed and the new head, instead of
     re-reviewing the entire PR diff from scratch every push.

  2. Seen webhook delivery IDs — GitHub redelivers webhooks on retry
     (e.g. if your server was briefly down or slow to ack). Without
     this, a redelivered event triggers a duplicate review/comments.

This is intentionally simple (single JSON file + asyncio.Lock) — fine
for a single-instance self-hosted bot. If you ever run multiple
replicas, swap this for Redis/Postgres; the interface below is small
enough to reimplement against either without touching callers.
"""
import asyncio
import json
import time
from pathlib import Path

_LOCK = asyncio.Lock()
_DEFAULT_PATH = Path(__file__).parent.parent / "data" / "review_state.json"
_DELIVERY_TTL_SECONDS = 24 * 60 * 60  # prune delivery IDs older than this


class ReviewStore:
    def __init__(self, path: Path = _DEFAULT_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"prs": {}, "deliveries": {}}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupt/partial write — don't crash the service over it,
            # just start fresh (worst case: one extra full review).
            return {"prs": {}, "deliveries": {}}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data))
        tmp.replace(self.path)  # atomic on POSIX

    @staticmethod
    def _pr_key(owner: str, repo: str, pr_number: int) -> str:
        return f"{owner}/{repo}#{pr_number}"

    async def get_last_reviewed_sha(self, owner: str, repo: str, pr_number: int) -> str | None:
        async with _LOCK:
            return self._data["prs"].get(self._pr_key(owner, repo, pr_number), {}).get("last_sha")

    async def set_last_reviewed_sha(self, owner: str, repo: str, pr_number: int, sha: str) -> None:
        async with _LOCK:
            key = self._pr_key(owner, repo, pr_number)
            self._data["prs"].setdefault(key, {})["last_sha"] = sha
            self._data["prs"][key]["updated_at"] = time.time()
            self._save()

    async def clear_pr(self, owner: str, repo: str, pr_number: int) -> None:
        """Call when a PR closes — no need to keep incremental state around."""
        async with _LOCK:
            self._data["prs"].pop(self._pr_key(owner, repo, pr_number), None)
            self._save()

    async def seen_delivery(self, delivery_id: str) -> bool:
        """Returns True and records the ID if new; returns False (already
        seen) without changing anything if it's a redelivery."""
        if not delivery_id:
            return True  # no ID to dedup on — don't block processing over it

        async with _LOCK:
            self._prune_deliveries_locked()
            if delivery_id in self._data["deliveries"]:
                return False
            self._data["deliveries"][delivery_id] = time.time()
            self._save()
            return True

    def _prune_deliveries_locked(self) -> None:
        cutoff = time.time() - _DELIVERY_TTL_SECONDS
        self._data["deliveries"] = {
            k: v for k, v in self._data["deliveries"].items() if v > cutoff
        }


store = ReviewStore()
