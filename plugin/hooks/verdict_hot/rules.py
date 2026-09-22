"""PreToolUse deterministic rules gate (task-5-brief.md, PLAN.md 5.1).

Two rule families, evaluated by `decide()`:

- A denylist of destructive Bash commands (recursive deletes of a protected
  root, a force-push to a protected branch, `git clean -fdx`, a raw device
  write, a filesystem format, a world-writable recursive `chmod` on `/`, a
  download piped into a shell, a database drop or truncate). Each entry in
  `policy.denylist` is matched against `policy.denylist_ids` by position.
  Two of the ten entries (`deny_rm_root`, `deny_force_push_protected`) are
  deliberately broad regexes -- `_EXTRA_CHECKS` narrows a hit down with a
  little Python, since neither "is this really the repository root" nor
  "is the force flag paired with a protected branch, in either order" is
  expressible as one static string a user policy could still read and edit.
- Never-send and credential-reading `ask` rules, reusing `gates.is_never_send`
  (never duplicating its glob/regex tables), plus a fixed `git reset --hard`
  check (PLAN 5.1: not detectable as destructive without diffing the working
  tree, so it only asks).

This module is imported *only* by the `pre` branch of `verdict_hook.py`,
which fails CLOSED (module docstring there): it must never import `stop`,
`provider`, `state`, `questions`, or `span` (task-5 controller notes). A
dedicated unit test greps this file's source for one specific four-letter
verb (deliberately not spelled out here, so this sentence cannot trip its
own check) and fails if it ever appears: the only two outcomes for a tool
call this module has no opinion on are silence (`RuleDecision(None, None,
None, False)`) or asking first.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from functools import cache
from typing import TYPE_CHECKING, Literal, NamedTuple

from . import PLUGIN_VERSION, SCHEMA_V

if TYPE_CHECKING:
    from .parsers import PreEvent
    from .policy import Policy

_GIT_RESET_HARD_RE = re.compile(r"\bgit\s+reset\s+--hard\b")
_GIT_TOPLEVEL_SUBST = "$(git rev-parse --show-toplevel)"
_STATIC_DANGEROUS_RM_TARGETS = frozenset({"/", "~", "$HOME", "${HOME}"})
_BARE_CWD_GLOB_TARGETS = frozenset({"*", ".", "./*"})
_ALL_SLASHES_RE = re.compile(r"/+")
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")
# Fix round 2 item 1: matches one whole "..."/'...' span (including its
# quote characters) so its contents can be blanked out before a segment
# separator is ever searched for -- `X="|" rm -rf /` is one segment, not a
# stray `"` followed by a bogus second one.
_QUOTED_SPAN_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\']*\'')
_GIT_PUSH_RE = re.compile(r"\bgit\s+push\b")
_FORCE_FLAG_RE = re.compile(r"--force(?!-with-lease)\b|-f\b")
_PROTECTED_BRANCH_NAMES = frozenset({"main", "master", "prod", "production", "release"})

_GENERIC_DENY_REASON = "Blocked by a Verdict policy rule."
_REASONS: dict[str, str] = {
    "deny_rm_root": "Blocked: recursive delete of a protected root path.",
    "deny_force_push_protected": "Blocked: force-push to a protected branch.",
    "deny_git_clean": "Blocked: git clean with force and recursive flags.",
    "deny_raw_device_write": "Blocked: raw write to a block device.",
    "deny_mkfs": "Blocked: filesystem format command.",
    "deny_chmod_root": "Blocked: world-writable recursive permission change on the root.",
    "deny_pipe_to_shell_curl": "Blocked: piping a download directly into a shell.",
    "deny_pipe_to_shell_wget": "Blocked: piping a download directly into a shell.",
    "deny_db_drop": "Blocked: database drop statement.",
    "deny_db_truncate": "Blocked: database truncate statement.",
    "ask_never_send_path": "Confirm: this path is excluded from Verdict's own review.",
    "ask_secret_read": "Confirm: this command may print credential material.",
    "ask_git_reset_hard": "Confirm: git reset --hard can discard uncommitted work.",
}


class RuleDecision(NamedTuple):
    decision: Literal["deny", "ask"] | None
    rule_id: str | None
    reason: str | None
    never_send: bool


def decide(
    tool_name: str, tool_input: Mapping[str, object], policy: Policy, cwd: str
) -> RuleDecision:
    """The one entry point the `pre` hook branch calls. Pure and fail-open on
    its own (an internal exception is the caller's problem to fail closed
    on, per `verdict_hook.py`'s `_handle_pre`)."""
    from . import gates

    command = tool_input.get("command") if tool_name == "Bash" else None
    if isinstance(command, str):
        rule_id = _match_denylist(command, cwd, policy)
        if rule_id is not None:
            reason = _REASONS.get(rule_id, _GENERIC_DENY_REASON)
            return RuleDecision("deny", rule_id, reason, False)

    if gates.is_never_send(tool_name, tool_input, policy):
        rule_id = "ask_secret_read" if tool_name == "Bash" else "ask_never_send_path"
        return RuleDecision("ask", rule_id, _REASONS[rule_id], True)

    if isinstance(command, str) and _GIT_RESET_HARD_RE.search(command):
        return RuleDecision("ask", "ask_git_reset_hard", _REASONS["ask_git_reset_hard"], False)

    return RuleDecision(None, None, None, False)


@cache
def _compiled_patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern[str] | None, ...]:
    compiled: list[re.Pattern[str] | None] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            compiled.append(None)
    return tuple(compiled)


def _match_denylist(command: str, cwd: str, policy: Policy) -> str | None:
    # `strict=` (B905) is 3.10+ only; `policy.py` already enforces equal
    # lengths for `denylist`/`denylist_ids` at load time (`_build_denylist`).
    for pattern_re, rule_id in zip(  # noqa: B905
        _compiled_patterns(policy.denylist), policy.denylist_ids
    ):
        if pattern_re is None or not pattern_re.search(command):
            continue
        extra_check = _EXTRA_CHECKS.get(rule_id)
        if extra_check is None or extra_check(command, cwd):
            return rule_id
    return None


def _mask_quoted_spans(command: str) -> str:
    """`command` with every quoted span's contents replaced by `#` of the
    same length (fix round 2 item 1). Used only to find segment-separator
    positions safely; callers slice the ORIGINAL text at those offsets, so
    real quote characters and content survive for tokenization."""
    return _QUOTED_SPAN_RE.sub(lambda m: "#" * len(m.group(0)), command)


def _split_segments(command: str) -> list[str]:
    """Split `command` on `&&`/`||`/`;`/`|`/newline, never inside a quoted
    span: `X="|" rm -rf /` is one segment, not a stray `"` and a bogus
    second piece (fix round 2 item 1)."""
    masked = _mask_quoted_spans(command)
    segments: list[str] = []
    last = 0
    for match in _SEGMENT_SPLIT_RE.finditer(masked):
        segments.append(command[last : match.start()])
        last = match.end()
    segments.append(command[last:])
    return segments


def _shlex_tokens(segment: str) -> list[str]:
    """Best-effort shell tokenization: falls back to a plain whitespace split
    on unbalanced quotes rather than raising (`shlex.split`'s `ValueError`),
    since a malformed command should just fall through to "no opinion" on
    this rule, not crash the gate."""
    import shlex

    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _refspec_branch_candidates(token: str) -> list[str]:
    """A push refspec token can be `branch`, `+branch` (force marker),
    `remote/branch`, or `local:remote` -- return every plain branch-name
    candidate `token` could refer to, so a whole-token match never treats
    `feature/main-menu` as if it named `main` (fix round 1 item 8)."""
    candidates: list[str] = []
    for part in token.lstrip("+").split(":"):
        if not part:
            continue
        candidates.append(part)
        if "/" in part:
            candidates.append(part.rsplit("/", 1)[-1])
    return candidates


def _is_protected_branch_token(token: str) -> bool:
    return any(c in _PROTECTED_BRANCH_NAMES for c in _refspec_branch_candidates(token))


def _check_force_push_extra(command: str, _cwd: str) -> bool:
    """Scoped to one `git push` segment at a time (fix round 1 item 8): a
    force flag in one `&&`-joined command and an unrelated mention of a
    protected branch name in another must not combine into a false deny."""
    for segment in _split_segments(command):
        if not _GIT_PUSH_RE.search(segment) or not _FORCE_FLAG_RE.search(segment):
            continue
        if any(_is_protected_branch_token(t) for t in _shlex_tokens(segment)):
            return True
    return False


def _parse_rm_segment(segment: str) -> tuple[bool, list[str]] | None:
    """`(has_recursive_flag, targets)` if `segment` is an `rm` invocation
    (after stripping leading `VAR=value` assignments and wrapper commands
    such as `sudo`/`env`), else None.

    Reuses `gates._strip_leading` (fix round 2 item 4) rather than
    duplicating its wrapper table with a narrower, rules.py-only copy that
    only knew about `sudo`: `env rm -rf /` and `env FOO=1 rm -rf /` were
    missed before this, since `env` was never in that narrower list.
    """
    from .gates import _strip_leading

    tokens = _shlex_tokens(_strip_leading(segment))
    if not tokens or tokens[0] not in ("rm", "/bin/rm", "/usr/bin/rm"):
        return None

    rest = tokens[1:]
    flags = [t for t in rest if t != "-" and t != "--" and t.startswith("-")]
    targets = [t for t in rest if t not in ("-", "--") and not t.startswith("-")]
    has_recursive = "r" in "".join(flags).lower()
    return has_recursive, targets


def _strip_trailing_glob(target: str) -> str:
    """Strip one trailing `/*`, `/.`, or bare `*` (fix round 1 item 1):
    `rm -rf /*`, `rm -rf /.`, and `rm -rf ~/*` are exactly as destructive
    as the bare target, and a naive exact-match set missed all three."""
    for suffix in ("/*", "/."):
        if target.endswith(suffix):
            return target[: -len(suffix)] or "/"
    if target.endswith("*"):
        return target[:-1] or "/"
    return target


def _normalize_rm_target(target: str) -> str:
    stripped = _strip_trailing_glob(target)
    if stripped in _STATIC_DANGEROUS_RM_TARGETS:
        return stripped
    if _ALL_SLASHES_RE.fullmatch(stripped):
        return "/"  # `//`, `///`, ... -- posixpath.normpath keeps "//" as-is
    return os.path.normpath(stripped)


def _resolve_rm_target(target: str, cwd: str) -> str:
    """The effective path `target` refers to, for the danger comparison
    below (fix round 2 item 2). A bare `*`, `.`, or `./*` expands relative
    to `cwd` itself: `rm -rf *` run in `/` is as destructive as `rm -rf /`,
    but the same command in `/tmp/x` is not -- resolve it against `cwd`
    rather than the old behavior, which mapped a bare `*` to `/`
    unconditionally regardless of where it actually ran.
    """
    if target in _BARE_CWD_GLOB_TARGETS and cwd:
        return _normalize_rm_target(cwd)
    return _normalize_rm_target(target)


def _target_is_repo_root(normalized_target: str, repo_root: str) -> bool:
    """`os.path.realpath` the rm target before comparing to `repo_root`
    (fix round 2 item 3): `_find_repo_root` already resolves symlinks in
    `cwd` (fix round 1 item 7), so `rm -rf /symlink/to/repo` -- a target
    reached through a *different* symlink than the one `cwd` used -- must
    resolve the same way to still match. Only applied to an absolute
    path: the static sentinels (`~`, `$HOME`, ...) are handled by the
    caller before this is ever reached, and `os.path.realpath` on a
    relative string would resolve against the wrong (process) cwd.
    """
    if normalized_target == repo_root:
        return True
    if not os.path.isabs(normalized_target):
        return False
    return os.path.realpath(normalized_target) == repo_root


def _check_rm_root_extra(command: str, cwd: str) -> bool:
    repo_root: str | None = None
    repo_root_checked = False
    for segment in _split_segments(command):
        parsed = _parse_rm_segment(segment)
        if parsed is None or not parsed[0]:
            continue
        if _GIT_TOPLEVEL_SUBST in segment:
            return True
        if not repo_root_checked:
            repo_root = _find_repo_root(cwd)
            repo_root_checked = True
        for target in parsed[1]:
            normalized = _resolve_rm_target(target, cwd)
            if normalized in _STATIC_DANGEROUS_RM_TARGETS:
                return True
            if repo_root is not None and _target_is_repo_root(normalized, repo_root):
                return True
    return False


def _find_repo_root(cwd: str) -> str | None:
    """Walk `cwd` up to the first directory holding `.git`, stopping at `/`.
    Never shells out (controller notes ruling 4): a plain `Path` walk.

    `.resolve()` (fix round 1 item 7) so a symlinked `cwd` -- or a symlinked
    *ancestor* of `cwd` -- still finds the real repository root: walking
    `.parent` on the unresolved path only ever climbs the symlink's own
    location, not the real directory tree the symlink's target lives in.
    """
    from pathlib import Path

    current = Path(cwd)
    if not current.is_absolute():
        return None
    current = current.resolve()
    while True:
        if (current / ".git").exists():
            return str(current)
        parent = current.parent
        if parent == current:
            return None
        current = parent


_EXTRA_CHECKS: dict[str, Callable[[str, str], bool]] = {
    "deny_rm_root": _check_rm_root_extra,
    "deny_force_push_protected": _check_force_push_extra,
}


def build_output_json(decision: RuleDecision) -> str | None:
    """The exact `hookSpecificOutput` shape (controller notes ruling 1), or
    None when there is nothing to print (the caller then writes no stdout
    at all)."""
    if decision.decision is None:
        return None
    import json

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.decision,
            "permissionDecisionReason": decision.reason,
        }
    }
    return json.dumps(payload, separators=(",", ":")) + "\n"


_FNV64_OFFSET_BASIS = 0xCBF29CE484222325
_FNV64_PRIME = 0x100000001B3
_FNV64_MASK = 0xFFFFFFFFFFFFFFFF


def _cwd_hash(cwd: str) -> str:
    """FNV-1a 64-bit, duplicated from `recorders.py` rather than imported
    (task-5 hazard: `recorders.py` is already at its line cap and is not to
    be grown for a one-function reuse)."""
    digest = _FNV64_OFFSET_BASIS
    for byte in cwd.encode("utf-8"):
        digest ^= byte
        digest = (digest * _FNV64_PRIME) & _FNV64_MASK
    return f"{digest:016x}"


def record_pre_row(event: PreEvent, decision: RuleDecision, now: float) -> None:
    """Append the one `pre` ledger row for this PreToolUse call: identifiers
    and the decision only, never the command or path (controller notes)."""
    from . import ledger

    row: dict[str, object] = {
        "schema_v": SCHEMA_V,
        "ts": float(now),
        "event": "pre",
        "session_id": event.session_id,
        "prompt_id": event.prompt_id,
        "agent_id": event.agent_id,
        "plugin_version": PLUGIN_VERSION,
        "cwd_hash": _cwd_hash(event.cwd),
        "tool_use_id": event.tool_use_id,
        "tool_name": event.tool_name,
        "rule_id": decision.rule_id,
        "decision": decision.decision,
        "never_send": decision.never_send,
    }
    if event.permission_mode is not None:
        row["permission_mode"] = event.permission_mode
    ledger.append_row(row)


__all__ = ["RuleDecision", "decide", "build_output_json", "record_pre_row"]
