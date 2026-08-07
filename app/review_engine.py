"""
Orchestrates a full PR review:
  fetch changed files -> filter/skip noise -> per-file LLM review ->
  validate line numbers against the actual diff -> batch into one
  GitHub review -> post.

Incremental review: on a `synchronize` event (new commits pushed to an
already-reviewed PR), we don't want to re-send the entire PR diff to
the LLM again — that's slow, wasteful, and re-flags the same old code
every push. Instead we fetch only the delta between the last commit we
reviewed and the new head (via GitHub's compare API) and review just
that. If we've never reviewed this PR before, or the stored base SHA
is no longer reachable (force-push/rebase), we fall back to a full
review of the current diff.
"""
import logging

import httpx

from app.config import settings
from app.diff_parser import parse_patch, should_skip_file
from app.github_client import GitHubClient
from app.llm_client import review_file_diff, LLMReviewError
from app.store import store

logger = logging.getLogger("review_engine")

SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "suggestion": "🔵"}


async def process_pull_request(owner: str, repo: str, pr_number: int) -> None:
    gh = GitHubClient()

    pr = await gh.get_pr(owner, repo, pr_number)
    commit_id = pr["head"]["sha"]

    files, is_incremental = await _get_files_to_review(gh, owner, repo, pr_number, commit_id)

    if files is None:
        logger.info("PR #%s: no new changes since last review (%s) — skipping", pr_number, commit_id)
        return

    logger.info("PR #%s: %d file(s) to review (%s)", pr_number, len(files),
                "incremental" if is_incremental else "full")

    if len(files) > settings.max_files_per_pr:
        files = files[: settings.max_files_per_pr]
        truncated_file_list = True
    else:
        truncated_file_list = False

    all_comments: list[dict] = []
    file_summaries: list[str] = []
    skipped: list[str] = []

    for f in files:
        filename = f["filename"]
        patch = f.get("patch")  # absent for binary files, renames w/o changes, etc.

        if should_skip_file(filename, settings.ignored_path_patterns):
            skipped.append(filename)
            continue
        if not patch:
            skipped.append(filename)
            continue

        parsed = parse_patch(patch, max_lines=settings.max_patch_lines_per_file)
        if parsed is None or not parsed.valid_lines:
            skipped.append(filename)
            continue

        try:
            result = await review_file_diff(filename, parsed.annotated)
        except LLMReviewError as e:
            logger.warning(str(e))
            file_summaries.append(f"- **{filename}**: ⚠️ review skipped (model error)")
            continue

        if result["summary"]:
            file_summaries.append(f"- **{filename}**: {result['summary']}")

        for c in result["comments"]:
            line = c["line"]
            if line not in parsed.valid_lines:
                # LLM hallucinated a line number outside the diff — drop it
                # rather than let the whole review call fail on GitHub's side.
                continue
            severity = c.get("severity", "suggestion")
            emoji = SEVERITY_EMOJI.get(severity, "🔵")
            all_comments.append({
                "path": filename,
                "line": line,
                "side": "RIGHT",
                "body": f"{emoji} **{severity.upper()}**\n\n{c['comment']}",
            })

            if len(all_comments) >= settings.max_comments_per_pr:
                break
        if len(all_comments) >= settings.max_comments_per_pr:
            file_summaries.append("- (comment limit reached — remaining files not fully reviewed)")
            break

    body = _build_summary_body(file_summaries, skipped, truncated_file_list,
                                len(all_comments), is_incremental)

    posted_ok = True
    if not all_comments and not settings.post_summary_even_if_clean:
        logger.info("PR #%s: no issues found, summary posting disabled — skipping", pr_number)
    elif not all_comments:
        # A review with zero inline comments still needs a non-empty body
        # and can't use REQUEST_CHANGES.
        await gh.create_issue_comment(owner, repo, pr_number, body)
    else:
        review = await gh.create_review(
            owner, repo, pr_number,
            commit_id=commit_id,
            body=body,
            comments=all_comments,
            event=settings.review_event,
        )
        if review.get("error"):
            logger.error("create_review failed (%s): %s — falling back to issue comment",
                          review.get("status"), review.get("detail"))
            fallback_body = body + "\n\n_(Inline comments could not be posted; showing summary only.)_"
            await gh.create_issue_comment(owner, repo, pr_number, fallback_body)
            posted_ok = review.get("status", 500) < 500  # don't advance state on a server-side hiccup

    # Advance the incremental checkpoint regardless of whether any
    # comments were posted — we've reviewed up to `commit_id` either way,
    # and re-reviewing the same range again on the next push would be wasted work.
    if posted_ok:
        await store.set_last_reviewed_sha(owner, repo, pr_number, commit_id)


async def _get_files_to_review(gh: GitHubClient, owner: str, repo: str,
                                pr_number: int, commit_id: str) -> tuple[list[dict] | None, bool]:
    """Returns (files, is_incremental). files is None if there's genuinely
    nothing new to review (redelivered event at the same SHA)."""
    last_sha = await store.get_last_reviewed_sha(owner, repo, pr_number)

    if not last_sha:
        files = await gh.get_pr_files(owner, repo, pr_number)
        return files, False

    if last_sha == commit_id:
        return None, True

    try:
        files = await gh.compare_commits(owner, repo, last_sha, commit_id)
        return files, True
    except httpx.HTTPStatusError as e:
        logger.warning(
            "compare_commits failed for PR #%s (%s -> %s): %s — falling back to full review",
            pr_number, last_sha, commit_id, e,
        )
        files = await gh.get_pr_files(owner, repo, pr_number)
        return files, False


def _build_summary_body(file_summaries: list[str], skipped: list[str], truncated: bool,
                         comment_count: int, is_incremental: bool) -> str:
    parts = ["## 🤖 AI Code Review\n"]
    scope_note = "Reviewed only the new commits since the last review" if is_incremental \
        else "Reviewed the full PR diff"
    parts.append(f"{scope_note} with `{settings.ollama_model}` (local). "
                 f"{comment_count} inline comment(s) posted.\n")

    if file_summaries:
        parts.append("### File notes\n" + "\n".join(file_summaries))
    else:
        parts.append("No issues found in the reviewed changes. ✅")

    if skipped:
        shown = ", ".join(f"`{s}`" for s in skipped[:10])
        more = f" and {len(skipped) - 10} more" if len(skipped) > 10 else ""
        parts.append(f"\n<details><summary>Skipped {len(skipped)} file(s)</summary>\n\n{shown}{more}\n</details>")

    if truncated:
        parts.append("\n⚠️ This PR changes more files than the configured limit — only the first "
                      f"{settings.max_files_per_pr} were reviewed.")

    return "\n\n".join(parts)
