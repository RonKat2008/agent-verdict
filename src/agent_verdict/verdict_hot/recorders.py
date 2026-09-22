"""Payload to ledger row, per M1 event (task-4-brief.md, PLAN.md 4.3, 5.2).

`build_row` is pure: no I/O, no clock (`now` is a parameter), and it never
mutates `event` or `policy` (both are `NamedTuple`s already, and the
`Mapping` fields on `event` are only ever read with `.get`). `record`
does the impure part: parse stdin, load the policy, build the row, and
append it to the ledger.

Text pipeline order for every free-text field (controller notes): the
never-send check runs first (PLAN 5.1) -- if it fires, EVERY excerpt on the
row becomes the literal string `"[never-send]"` and the row gets
`never_send: true`. Otherwise: `textnorm.normalize`, then `redact.redact`,
then `textnorm.truncate_anchored` (redact before truncate, so a secret can
never be split across the cut). A redactor exception (`redact` returning
`-1` hits) makes the field `"[redaction failed]"` and the row records
`redaction_failed: true`; the raw text is never written in that case either.

`gates` (needed only for `post`/`post_fail`, to compute `is_check` and
`soft_fail_candidate`) and `claims` (needed only for `stop`) are imported
lazily inside the builders that need them, per the hot-path lazy-import
rule (global-constraints.md). Task 6 extended this to `redact` and
`textnorm` (needed only when a builder actually runs the text pipeline --
never for `session_start`/`session_end`) and `_tool_output` (needed only
for `post`), after `-X importtime` profiling showed both were still
imported at module level for every event (task-6-brief.md).

`post` rows never store a tool's raw file content or an Agent delegation
prompt in `out_head`/`out_tail` (task-4 fix round 1): `_tool_output.py`
builds a structural summary for Write/Edit/NotebookEdit/Read/Glob/Grep
(`out_kind="structural"`), keeps only an Agent's result text plus a small
structural-field allowlist (`out_kind="text"`), and for every other tool
(Bash, WebFetch, MCP, unknown) walks the response and replaces any string
over 2,000 characters stored under a content-shaped key name
(`content`, `originalFile`, `oldString`, `newString`, `prompt`, `file`,
`data`, `body`, `text`) with `"[omitted N chars]"` before the usual
normalize/redact/truncate pipeline runs. See `_tool_output.py`'s docstring
for the full per-tool field allowlists.

The per-event field builders themselves live in `_recorder_fields.py`
(split out, task-7-brief.md cleanup item 5, to keep this file under the
400-line cap); this module keeps row assembly (`_common_fields`,
`build_row`) and the impure `record` entry point.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from . import PLUGIN_VERSION, SCHEMA_V, ledger, parsers
from ._recorder_fields import (
    _build_post_fail_fields,
    _build_post_fields,
    _build_prompt_fields,
    _build_session_end_fields,
    _build_session_start_fields,
    _build_stop_fields,
)
from .parsers import (
    Common,
    HookEvent,
    PostEvent,
    PostFailEvent,
    PromptEvent,
    SessionEndEvent,
    SessionStartEvent,
    StopEvent,
)

if TYPE_CHECKING:
    # Only used for type annotations below (never instantiated or
    # isinstance-checked here): guarding it keeps `verdict_hot.policy`
    # out of every event's import graph except where `_load_policy_fail_open`
    # already loads it for real (task-6-brief.md's lazy-import pass).
    from .policy import Policy

_MODE_OFF = "off"

_EVENT_NAMES: dict[type, str] = {
    SessionStartEvent: "session_start",
    PromptEvent: "prompt",
    PostEvent: "post",
    PostFailEvent: "post_fail",
    StopEvent: "stop",
    SessionEndEvent: "session_end",
}


# FNV-1a 64-bit (task-6-brief.md item 2): `cwd_hash` only needs a cheap,
# deterministic, non-reversible-enough identifier for grouping rows by
# working directory -- it is not a security boundary, so a cryptographic
# hash is unnecessary cost. `hashlib` alone pulls in the `_hashlib` C
# extension (measured ~14ms of the per-process startup budget on the
# `post-fail` fixture -X importtime profile) for every event, since
# `_cwd_hash` runs on every row via `_common_fields`. FNV-1a is a plain
# stdlib-free integer loop: no import at all, let alone a C-extension one.
# This changes stored `cwd_hash` values from M1's earlier sha256-based ones
# (acceptable per global-constraints.md: no external consumers yet).
_FNV64_OFFSET_BASIS = 0xCBF29CE484222325
_FNV64_PRIME = 0x100000001B3
_FNV64_MASK = 0xFFFFFFFFFFFFFFFF


def _fnv1a_64(data: bytes) -> int:
    digest = _FNV64_OFFSET_BASIS
    for byte in data:
        digest ^= byte
        digest = (digest * _FNV64_PRIME) & _FNV64_MASK
    return digest


def _cwd_hash(cwd: str) -> str:
    return f"{_fnv1a_64(cwd.encode('utf-8')):016x}"


def _common_fields(common: Common) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_v": SCHEMA_V,
        "session_id": common.session_id,
        "prompt_id": common.prompt_id,
        "agent_id": common.agent_id,
        "plugin_version": PLUGIN_VERSION,
        "cwd_hash": _cwd_hash(common.cwd),
    }
    if common.permission_mode is not None:
        row["permission_mode"] = common.permission_mode
    return row


def build_row(event: HookEvent, policy: Policy, now: float) -> dict[str, object]:
    row = _common_fields(event.common)
    row["ts"] = float(now)
    row["event"] = _EVENT_NAMES[type(event)]

    if isinstance(event, SessionStartEvent):
        row.update(_build_session_start_fields(event))
    elif isinstance(event, PromptEvent):
        row.update(_build_prompt_fields(event, policy))
    elif isinstance(event, PostEvent):
        row.update(_build_post_fields(event, policy))
    elif isinstance(event, PostFailEvent):
        row.update(_build_post_fail_fields(event, policy))
    elif isinstance(event, StopEvent):
        row.update(_build_stop_fields(event, policy))
    elif isinstance(event, SessionEndEvent):
        row.update(_build_session_end_fields(event))
    else:  # pragma: no cover - HookEvent is exhaustive above
        raise TypeError(f"unsupported event type: {type(event)!r}")
    return row


def _load_policy_fail_open() -> Policy:
    """Load the effective policy, falling back to the packaged default.

    `build_row` itself stays pure (no I/O); this impure fallback belongs to
    `record`, per the controller notes: a broken user policy override must
    not stop the row from being recorded at all.
    """
    from . import policy as policy_mod

    try:
        return policy_mod.load_policy()
    except policy_mod.PolicyError:
        return policy_mod.load_policy(path=policy_mod.default_policy_path())


def record(payload: Mapping[str, object]) -> str:
    """Parse, build, and append one ledger row for `payload`.

    Returns `"ok"` on success, or `"skipped"` with no row written when the
    effective policy's `mode` is `off` (final review, C2). The entry point
    already exits on the plugin's own `CLAUDE_PLUGIN_OPTION_MODE=off`; this
    is the second line, for a user who sets `mode` in
    `~/.verdict/policy.json` instead.

    Raises `parsers.ParseError` for a payload M1
    does not record (PreToolUse, SubagentStop, an unknown hook, or a
    malformed required field); the caller (Task 5's entry point) treats that
    as `skipped`. Any other unexpected exception also propagates, and the
    caller treats it as `exception` -- both per the fail-open model-path
    contract (global-constraints.md).
    """
    event = parsers.parse_event(payload)
    policy = _load_policy_fail_open()
    if policy.mode.strip().lower() == _MODE_OFF:
        return "skipped"
    row = build_row(event, policy, time.time())
    ledger.append_row(row)
    return "ok"


__all__ = ["build_row", "record"]
