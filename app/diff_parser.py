"""
Parses a GitHub `patch` string (unified diff for one file) into:
  1. A line-annotated version to feed the LLM (so it can cite real
     line numbers instead of guessing).
  2. The set of new-file line numbers that are valid targets for a
     GitHub review comment on the RIGHT side (added + context lines).

We only support RIGHT-side (new file) comments. GitHub rejects a review
comment whose line isn't part of the diff, so validating against this
set before posting prevents the whole review from failing.
"""
import re
from dataclasses import dataclass

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class ParsedPatch:
    annotated: str            # human/LLM-readable, each line prefixed with new-file line no.
    valid_lines: set[int]     # line numbers valid for a RIGHT-side comment
    truncated: bool = False


def parse_patch(patch: str, max_lines: int = 400) -> ParsedPatch | None:
    if not patch:
        return None

    lines = patch.split("\n")
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    annotated_out: list[str] = []
    valid_lines: set[int] = set()
    new_lineno = 0

    for line in lines:
        m = HUNK_HEADER.match(line)
        if m:
            new_lineno = int(m.group(2))
            annotated_out.append(line)
            continue

        if line.startswith("+") and not line.startswith("+++"):
            annotated_out.append(f"{new_lineno:>5}| {line}")
            valid_lines.add(new_lineno)
            new_lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            annotated_out.append(f"     | {line}")  # no new-file line number for deletions
        elif line.startswith("\\"):  # "\ No newline at end of file"
            annotated_out.append(line)
        else:
            # context line — present on both sides, valid to comment on
            annotated_out.append(f"{new_lineno:>5}| {line}")
            valid_lines.add(new_lineno)
            new_lineno += 1

    return ParsedPatch(
        annotated="\n".join(annotated_out),
        valid_lines=valid_lines,
        truncated=truncated,
    )


def should_skip_file(filename: str, ignored_patterns: list[str]) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(filename, pat) for pat in ignored_patterns)
