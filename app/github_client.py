"""
Thin wrapper around the GitHub REST API endpoints this bot needs:
  - list files changed in a PR (with per-file patch text)
  - create a single "review" with inline comments
  - fall back to a plain issue comment if review creation fails
    (e.g. because a computed line isn't part of the diff anymore)
"""
import httpx
from app.config import settings


class GitHubClient:
    def __init__(self):
        self.base = settings.github_api_base
        self.headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_pr(self, owner: str, repo: str, pr_number: int) -> dict:
        url = f"{self.base}/repos/{owner}/{repo}/pulls/{pr_number}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Returns list of {filename, status, patch, additions, deletions, ...}.
        Paginates since GitHub caps this endpoint at 100 files per page."""
        files: list[dict] = []
        page = 1
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                url = f"{self.base}/repos/{owner}/{repo}/pulls/{pr_number}/files"
                r = await client.get(
                    url, headers=self.headers,
                    params={"per_page": 100, "page": page},
                )
                r.raise_for_status()
                batch = r.json()
                files.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
        return files

    async def compare_commits(self, owner: str, repo: str, base_sha: str, head_sha: str) -> list[dict]:
        """Returns the file-level diff between two commits, in the same
        shape as get_pr_files (filename, patch, status, ...). Used for
        incremental review: base_sha = last commit we already reviewed,
        head_sha = new PR head after a `synchronize` event.

        Raises httpx.HTTPStatusError on failure — notably a 404, which
        happens if base_sha is no longer reachable (e.g. the branch was
        force-pushed/rebased). Callers should catch this and fall back
        to a full review.
        """
        url = f"{self.base}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self.headers)
            r.raise_for_status()
            return r.json().get("files", [])

    async def create_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        body: str,
        comments: list[dict],
        event: str = "COMMENT",
    ) -> dict:
        """comments: [{path, line, side, body}, ...]"""
        url = f"{self.base}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
            "comments": comments,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=self.headers, json=payload)
            if r.status_code >= 400:
                return {"error": True, "status": r.status_code, "detail": r.text}
            return r.json()

    async def create_issue_comment(self, owner: str, repo: str, pr_number: int, body: str) -> dict:
        """Fallback: plain PR comment, used if the structured review call fails
        (e.g. all computed line anchors were rejected by GitHub)."""
        url = f"{self.base}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=self.headers, json={"body": body})
            r.raise_for_status()
            return r.json()
