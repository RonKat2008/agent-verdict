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

import re
from collections.abc import Callable, Mapping
from functools import cache
from typing import TYPE_CHECKING, NamedTuple

from . import PLUGIN_VERSION, SCHEMA_V

if TYPE_CHECKING:
    from .parsers import PreEvent
    from .policy import Policy

_GIT_RESET_HARD_RE = re.compile(r"\bgit\s+reset\s+--hard\b")
_GIT_TOPLEVEL_SUBST = "$(git rev-parse --show-toplevel)"
_STATIC_DANGEROUS_RM_TARGETS = frozenset({"/", "~", "$HOME", "${HOME}"})
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")
_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FORCE_FLAG_RE = re.compile(r"--force(?!-with-lease)\b|-f\b")
_PROTECTED_BRANCH_RE = re.compile(r"\b(?:main|master|prod|production|release)\b")

_REASONS: dict[str, str] = {
    "deny_rm_root": "Blocked: recursive delete of a protected root path.",
    "deny_force_push_protected": "Blocked: force-push to a protected branch.",
    "deny_git_clean": "Blocked: git clean with force and recursive flags.",
    "deny_raw_device_write": "Blocked: raw write to a block device.",
    "deny_mkfs": "Blocked: filesystem format command.",
    "deny_chmod_root": "Blocked: world-writable recursive permission change on the root.",
    "deny_pipe_to_shell": "Blocked: piping a download directly into a shell.",
    "deny_db_drop": "Blocked: database drop statement.",
    "deny_db_truncate": "Blocked: database truncate statement.",
    "ask_never_send_path": "Confirm: this path is excluded from Verdict's own review.",
    "ask_secret_read": "Confirm: this command may print credential material.",
    "ask_git_reset_hard": "Confirm: git reset --hard can discard uncommitted work.",
}


class RuleDecision(NamedTuple):
    decision: str | None  # "deny", "ask", or None
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
            return RuleDecision("deny", rule_id, _REASONS[rule_id], False)

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


def _check_force_push_extra(command: str, _cwd: str) -> bool:
    return bool(_FORCE_FLAG_RE.search(command)) and bool(_PROTECTED_BRANCH_RE.search(command))


def _parse_rm_segment(segment: str) -> tuple[bool, list[str]] | None:
    """`(has_recursive_flag, targets)` if `segment` is an `rm` invocation
    (after skipping leading `VAR=value` assignments and `sudo`), else None."""
    import shlex

    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()

    index = 0
    while index < len(tokens) and (tokens[index] == "sudo" or _VAR_ASSIGN_RE.match(tokens[index])):
        index += 1
    if index >= len(tokens) or tokens[index] not in ("rm", "/bin/rm", "/usr/bin/rm"):
        return None

    rest = tokens[index + 1 :]
    flags = [t for t in rest if t != "-" and t != "--" and t.startswith("-")]
    targets = [t for t in rest if t not in ("-", "--") and not t.startswith("-")]
    has_recursive = "r" in "".join(flags).lower()
    return has_recursive, targets


def _normalize_rm_target(target: str) -> str:
    return target if target == "/" else target.rstrip("/")


def _check_rm_root_extra(command: str, cwd: str) -> bool:
    repo_root: str | None = None
    repo_root_checked = False
    for segment in _SEGMENT_SPLIT_RE.split(command):
        parsed = _parse_rm_segment(segment)
        if parsed is None or not parsed[0]:
            continue
        if _GIT_TOPLEVEL_SUBST in segment:
            return True
        if not repo_root_checked:
            repo_root = _find_repo_root(cwd)
            repo_root_checked = True
        for target in parsed[1]:
            normalized = _normalize_rm_target(target)
            if normalized in _STATIC_DANGEROUS_RM_TARGETS:
                return True
            if repo_root is not None and normalized == repo_root:
                return True
    return False


def _find_repo_root(cwd: str) -> str | None:
    """Walk `cwd` up to the first directory holding `.git`, stopping at `/`.
    Never shells out (controller notes ruling 4): a plain `Path` walk."""
    from pathlib import Path

    current = Path(cwd)
    if not current.is_absolute():
        return None
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
