"""Never-send check and evidence-gate tagging (PLAN.md 5.0, 5.1; task-3-brief.md).

Three functions, all pure and policy-driven:

- `is_never_send`: PLAN 5.1's "never-send and credential paths" rule --
  a glob match on a Write/Edit/NotebookEdit `file_path`, or a regex match on
  a Bash `command` that would print such a file.
- `is_check`: PLAN 5.0's G-CHECK gate -- does this Bash command *start* a
  shell segment with a known test/build/lint runner.
- `is_soft_fail_candidate`: PLAN 5.0's G-SOFT gate -- output that looks like
  a failure, a command that masks its own exit status, or an HTTP error
  body, restricted to `soft_failure.tools`.

Every policy-supplied pattern list (globs and regexes alike) is compiled
lazily and cached by `functools.cache`, keyed on the pattern tuple itself
(tuples are hashable) -- never at import (task-3-brief.md: "no regex
compilation at import; compile lazily and cache").
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from .policy import Policy

# Fixed, small, internal-only patterns (not policy-supplied): compiling
# these two at import time is the same cost textnorm.py already pays for
# its own fixed patterns, and keeps the hot loop in `is_check` simple.
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")
_VAR_ASSIGN_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S*)\s*")

# Wrapper commands that precede the real runner (task-3-brief.md,
# global-constraints.md, fix round 1 item 2): stripped left to right,
# repeatedly, alongside leading VAR=value assignments, until nothing more
# strips. Once a wrapper is stripped, the runner underneath is matched by
# its own bare pattern (e.g. "npx playwright test" -> "playwright test"),
# so wrappers don't need one runner_pattern per wrapper+runner combination.
_WRAPPERS = (
    "uv run",
    "npx",
    "bunx",
    "yarn dlx",
    "pnpm exec",
    "poetry run",
    "pipx run",
    "hatch run",
    "pdm run",
    "rye run",
    "bundle exec",
    "python -m",
    "python3 -m",
    "time",
    "sudo",
)


@cache
def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue  # a malformed user-supplied pattern never crashes the hot path
    return tuple(compiled)


@cache
def _glob_regexes(globs: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(_glob_to_regex(_expand_home(g))) for g in globs)


def _expand_home(pattern: str) -> str:
    if pattern == "~" or pattern.startswith("~/"):
        return str(Path.home()) + pattern[1:]
    return pattern


def _glob_to_regex(pattern: str) -> str:
    """Translate a `**`/`*`/`?` glob into an anchored regex string.

    `**/` matches zero or more leading path segments; a bare trailing `**`
    matches anything (including further `/`); `*` and `?` never cross a
    `/`. Everything else is escaped, so this is safe to feed `re.compile`
    even when the source pattern contains regex metacharacters.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern[i : i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "^" + "".join(out) + "$"


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(regex.search(text) for regex in _compiled(patterns))


def _matches_any_glob(candidate: str, globs: tuple[str, ...]) -> bool:
    return any(regex.match(candidate) for regex in _glob_regexes(globs))


def is_never_send(tool_name: str, tool_input: Mapping[str, object], policy: Policy) -> bool:
    """PLAN 5.1: Write/Edit-style `file_path` glob, or a Bash `command` regex.

    Keys off whichever field is actually present in `tool_input` rather
    than branching on `tool_name`, so it stays correct if a future tool
    reuses either field name.

    `path_exclude_globs` (fix round 1 item 4) is checked before
    `path_globs` so a public key (`*.pub`) or an example/template env file
    is never flagged even though it would otherwise match a broader
    include glob (`**/id_rsa*`, `**/.env*`).
    """
    file_path = tool_input.get("file_path")
    if isinstance(file_path, str):
        candidate = os.path.expanduser(file_path)
        if not _matches_any_glob(
            candidate, policy.never_send.path_exclude_globs
        ) and _matches_any_glob(candidate, policy.never_send.path_globs):
            return True
    command = tool_input.get("command")
    return isinstance(command, str) and _matches_any(command, policy.never_send.bash_patterns)


def _strip_leading(segment: str) -> str:
    """Strip leading `VAR=value` assignments and known wrappers, repeatedly."""
    changed = True
    while changed:
        changed = False
        match = _VAR_ASSIGN_RE.match(segment)
        if match:
            segment = segment[match.end() :]
            changed = True
            continue
        stripped = segment.lstrip()
        for wrapper in _WRAPPERS:
            if stripped == wrapper or stripped.startswith(wrapper + " "):
                segment = stripped[len(wrapper) :]
                changed = True
                break
    return segment.lstrip()


def is_check(command: str, policy: Policy) -> bool:
    """PLAN 5.0 G-CHECK: a runner must START a shell segment.

    Segments are split on `&&`, `||`, `;`, `|`, and newlines, after
    stripping leading assignments and wrapper commands from each one.
    `echo pytest` is false (the segment starts with `echo`); `cd app &&
    pytest -q` is true (the second segment starts with `pytest`).
    """
    runners = policy.checks.runner_patterns + policy.checks.extra
    compiled = _compiled(runners)
    for raw_segment in _SEGMENT_SPLIT_RE.split(command):
        candidate = _strip_leading(raw_segment)
        if any(regex.match(candidate) for regex in compiled):
            return True
    return False


def is_soft_fail_candidate(
    tool_name: str, command: str | None, output: str, policy: Policy
) -> bool:
    """PLAN 5.0 G-SOFT, restricted to `soft_failure.tools`."""
    if tool_name not in policy.soft_failure.tools:
        return False
    if _matches_any(output, policy.soft_failure.output_patterns):
        return True
    if command is not None and _matches_any(command, policy.soft_failure.masking_patterns):
        return True
    return _matches_any(output, policy.soft_failure.http_error_patterns)
