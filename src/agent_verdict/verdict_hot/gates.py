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

Task 6 (perf): every caller here only ever needs a boolean "did any pattern
in this list match" -- none attributes a hit back to a specific pattern (a
Bash command either is or is not a never-send trigger; a segment either is
or is not a check-runner). So each policy-supplied list is joined into ONE
alternation and compiled once, instead of once per pattern: on the
`post-fail` fixture this cut ~60 of the ~80 `re.compile` calls per process
down to 2. A pattern's own leading global inline flag (`(?i)`, `(?m)`, ...)
is rewritten to a *scoped* group flag (`(?i:...)`) so joining never changes
that one pattern's case-sensitivity or MULTILINE behavior, or leaks it onto
the other alternatives. `claims.py`'s success-verb patterns are the
exception: `_asserts_success` needs each verb's own match position for its
negation lookback, so those stay individually compiled (attribution is
required there).
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
# "env" (task-5 fix round 2 item 4): rules.py's `_parse_rm_segment` reuses
# this same table via `_strip_leading` so `env rm -rf /` is not missed;
# `is_check` picks up the same stripping for `env pytest -q` for free.
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
    "env",
    "nice",
    "xargs",
)


_GLOBAL_FLAGS_RE = re.compile(r"^\(\?([aiLmsux]+)\)")


def _scope_pattern(pattern: str) -> str:
    """Wrap `pattern` as one alternation branch, `(?:pattern)`.

    A pattern that opens with a global inline-flag group (`(?i)`, `(?m)`,
    ...) is rewritten to the scoped form (`(?i:pattern-body)`) instead, so
    the flag keeps applying to only that one branch once several patterns
    are joined with `|` -- a global `(?i)` anywhere in a joined pattern
    would otherwise make every OTHER branch case-insensitive too.
    """
    match = _GLOBAL_FLAGS_RE.match(pattern)
    if match:
        return f"(?{match.group(1)}:{pattern[match.end() :]})"
    return f"(?:{pattern})"


@cache
def _compiled(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
    """Join `patterns` into one alternation and compile it once.

    Falls back to compiling one-by-one and dropping any single malformed
    pattern (same fail-open contract as before this task) only on the rare
    path where the joined pattern itself fails to compile -- a malformed
    user-supplied pattern must never crash the hot path, and must never
    poison every other pattern in the same list either.
    """
    if not patterns:
        return None
    try:
        return re.compile("|".join(_scope_pattern(p) for p in patterns))
    except re.error:
        valid = []
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error:
                continue
            valid.append(pattern)
        return re.compile("|".join(_scope_pattern(p) for p in valid)) if valid else None


@cache
def _glob_regexes(globs: tuple[str, ...]) -> re.Pattern[str] | None:
    if not globs:
        return None
    parts = (_glob_to_regex(_expand_home(g)) for g in globs)
    return re.compile("|".join(f"(?:{p})" for p in parts))


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
    compiled = _compiled(patterns)
    return compiled is not None and compiled.search(text) is not None


def _matches_any_glob(candidate: str, globs: tuple[str, ...]) -> bool:
    compiled = _glob_regexes(globs)
    return compiled is not None and compiled.match(candidate) is not None


# Field names that carry a filesystem path across the built-in tools and the
# MCP servers seen in the wild (final review, I1: keying only on `file_path`
# let `mcp__fs__read {"path": "/home/u/.env"}` through). `url` is handled
# separately, since only a `file:` URL names a local path.
_PATH_KEYS = (
    "file_path",
    "path",
    "uri",
    "filename",
    "file",
    "notebook_path",
    "target",
    "source",
    "destination",
)
_PATH_LIST_KEYS = ("paths",)
_FILE_URI_PREFIX = "file://"
_MCP_TOOL_PREFIX = "mcp__"
_PATH_SHAPED_PREFIXES = ("/", "~", "./")


def _local_path(value: str) -> str:
    """`value` as a local path: a `file:` URI's path part, else itself."""
    if value.startswith(_FILE_URI_PREFIX):
        rest = value[len(_FILE_URI_PREFIX) :]
        slash = rest.find("/")  # drop an authority component (file://host/p)
        return rest[slash:] if slash > 0 else rest
    return value


def _path_candidates(tool_name: str, tool_input: Mapping[str, object]) -> list[str]:
    """Every value in `tool_input` that could name a file on this machine.

    Known path-carrying keys are read for every tool. For an MCP tool the
    server's schema is unknown, so any *top-level* string that looks like a
    path (`/`, `~/`, `./`) is a candidate too.
    """
    candidates: list[str] = []
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str):
            candidates.append(_local_path(value))
    for key in _PATH_LIST_KEYS:
        values = tool_input.get(key)
        if isinstance(values, (list, tuple)):
            candidates.extend(_local_path(v) for v in values if isinstance(v, str))
    url = tool_input.get("url")
    if isinstance(url, str) and url.startswith(_FILE_URI_PREFIX):
        candidates.append(_local_path(url))
    if tool_name.startswith(_MCP_TOOL_PREFIX):
        for key, value in tool_input.items():
            is_new_path_shaped_string = (
                key not in _PATH_KEYS
                and isinstance(value, str)
                and value.startswith(_PATH_SHAPED_PREFIXES)
            )
            if is_new_path_shaped_string:
                candidates.append(str(value))
    return candidates


def is_never_send(tool_name: str, tool_input: Mapping[str, object], policy: Policy) -> bool:
    """PLAN 5.1: a credential-path glob on any path-shaped field, or a Bash
    `command` regex.

    Keys off whichever fields are actually present in `tool_input` rather
    than branching on `tool_name`, so it stays correct for a tool that
    names its path field something other than `file_path` -- including the
    MCP servers M1's PostToolUse matcher already records (final review, I1).

    `path_exclude_globs` (fix round 1 item 4) is checked before
    `path_globs` so a public key (`*.pub`) or an example/template env file
    is never flagged even though it would otherwise match a broader
    include glob (`**/id_rsa*`, `**/.env*`).
    """
    for raw in _path_candidates(tool_name, tool_input):
        candidate = os.path.expanduser(raw)
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
    if compiled is None:
        return False
    for raw_segment in _SEGMENT_SPLIT_RE.split(command):
        candidate = _strip_leading(raw_segment)
        if compiled.match(candidate):
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
