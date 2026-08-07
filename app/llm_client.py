"""
Talks to a local Ollama server and turns its output into structured
review comments. Small local code models don't reliably obey "output
only JSON", so we ask for a fenced JSON block and extract it with a
regex fallback rather than assuming clean output.
"""
import json
import re
import httpx
from app.config import settings

SYSTEM_PROMPT = """You are a senior software engineer performing a code review on a GitHub pull request diff.

You will be shown one file's diff. Each line that can be commented on is prefixed with its line number in the NEW version of the file, like "  42| +    return x".

Review for: bugs, security issues, edge cases, performance problems, and clear readability/maintainability issues. Do NOT comment on pure style preferences (formatting, naming bikeshedding) unless they actively hurt readability. Do NOT invent issues — if the diff is fine, say so with zero comments.

Respond with ONLY a JSON object in this exact shape, no prose before or after, no markdown fences:

{
  "comments": [
    {"line": <int, must be one of the numbered lines shown>, "severity": "critical|warning|suggestion", "comment": "<specific, actionable, 1-3 sentences>"}
  ],
  "summary": "<one sentence overall verdict for this file>"
}

If there are no issues, return {"comments": [], "summary": "..."}. Only flag real problems — a noisy bot that comments on everything gets ignored."""

JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMReviewError(Exception):
    pass


async def review_file_diff(filename: str, annotated_patch: str) -> dict:
    """Returns {"comments": [...], "summary": "..."}"""
    user_prompt = f"File: {filename}\n\nDiff:\n{annotated_patch}"

    payload = {
        "model": settings.ollama_model,
        "system": SYSTEM_PROMPT,
        "prompt": user_prompt,
        "stream": False,
        "options": {"temperature": settings.ollama_temperature},
    }

    async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
        try:
            r = await client.post(f"{settings.ollama_host}/api/generate", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMReviewError(f"Ollama request failed for {filename}: {e}") from e

    raw_text = r.json().get("response", "")
    return _extract_json(raw_text, filename)


def _extract_json(raw_text: str, filename: str) -> dict:
    # Happy path: the whole response is valid JSON.
    try:
        parsed = json.loads(raw_text)
        return _validate_shape(parsed)
    except json.JSONDecodeError:
        pass

    # Fallback: model wrapped it in prose or markdown fences — grab the
    # largest {...} block and try again.
    match = JSON_BLOCK_RE.search(raw_text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return _validate_shape(parsed)
        except json.JSONDecodeError:
            pass

    # Last resort: no usable structure, treat as "no comments" rather
    # than crashing the whole PR review over one unparsable file.
    return {"comments": [], "summary": f"(review model returned unparsable output for {filename})"}


def _validate_shape(parsed: dict) -> dict:
    comments = parsed.get("comments", [])
    clean_comments = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        if "line" not in c or "comment" not in c:
            continue
        try:
            c["line"] = int(c["line"])
        except (TypeError, ValueError):
            continue
        c.setdefault("severity", "suggestion")
        clean_comments.append(c)
    return {
        "comments": clean_comments,
        "summary": parsed.get("summary", ""),
    }
