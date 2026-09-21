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
rule (global-constraints.md).

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
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping

from . import PLUGIN_VERSION, SCHEMA_V, _tool_output, ledger, parsers, redact, textnorm
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
from .policy import Policy

_NEVER_SEND_MARKER = "[never-send]"
_REDACTION_FAILED_MARKER = "[redaction failed]"

_EVENT_NAMES: dict[type, str] = {
    SessionStartEvent: "session_start",
    PromptEvent: "prompt",
    PostEvent: "post",
    PostFailEvent: "post_fail",
    StopEvent: "stop",
    SessionEndEvent: "session_end",
}


def _cwd_hash(cwd: str) -> str:
    return hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:16]


def _process_excerpt(
    raw_text: str, head: int, tail: int, never_send: bool
) -> tuple[str, int, int, bool]:
    """One-field pipeline: returns (excerpt, redaction_hits, sanitized_chars, failed)."""
    if never_send:
        return _NEVER_SEND_MARKER, 0, 0, False
    normalized, removed = textnorm.normalize(raw_text)
    redacted, hits = redact.redact(normalized)
    failed = hits == -1
    if failed:
        redacted = _REDACTION_FAILED_MARKER
        hits = 0
    excerpt = textnorm.truncate_anchored(redacted, head, tail)
    return excerpt, hits, removed, failed


def _split_head_tail(text: str, head: int, tail: int) -> tuple[str, str]:
    if len(text) <= head:
        return text, ""
    if len(text) <= head + tail:
        return text[:head], text[head:]
    return text[:head], text[-tail:] if tail else ""


def _process_head_tail(
    raw_text: str, head: int, tail: int, never_send: bool
) -> tuple[str, str, int, int, bool]:
    """Two-field pipeline (out_head/out_tail): same order, split output."""
    if never_send:
        return _NEVER_SEND_MARKER, _NEVER_SEND_MARKER, 0, 0, False
    normalized, removed = textnorm.normalize(raw_text)
    redacted, hits = redact.redact(normalized)
    failed = hits == -1
    if failed:
        redacted = _REDACTION_FAILED_MARKER
        hits = 0
    out_head, out_tail = _split_head_tail(redacted, head, tail)
    return out_head, out_tail, hits, removed, failed


def _raw_input_excerpt(tool_name: str, tool_input: Mapping[str, object]) -> str:
    if tool_name == "Bash":
        command = tool_input.get("command")
        return command if isinstance(command, str) else ""
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        file_path = tool_input.get("file_path")
        return file_path if isinstance(file_path, str) else ""
    if tool_name == "WebFetch":
        url = tool_input.get("url")
        return url if isinstance(url, str) else ""
    if tool_name == "Agent":
        description = tool_input.get("description")
        if isinstance(description, str) and description:
            return description
        prompt = tool_input.get("prompt")
        return prompt[:300] if isinstance(prompt, str) else ""
    return _tool_output.json_compact(dict(tool_input))


def _command_from(tool_input: Mapping[str, object]) -> str | None:
    command = tool_input.get("command")
    return command if isinstance(command, str) else None


def _is_check_excluding_command_trigger(
    tool_name: str, command: str | None, tool_input: Mapping[str, object], policy: Policy
) -> bool:
    """G-CHECK tagging, except when the command itself is the never-send trigger.

    Controller notes: tags such as `is_check` may still be computed from the
    command ONLY if the command itself is not what fired the never-send
    check (e.g. `cat .env`, as opposed to a Write to a never-send path).
    """
    from . import gates

    if command is None:
        return False
    via_command = gates.is_never_send(tool_name, {"command": command}, policy)
    if via_command:
        return False
    return gates.is_check(command, policy)


def _build_session_start_fields(event: SessionStartEvent) -> dict[str, object]:
    # `cc_effort` maps from the payload's optional `effort.level` (A10), which
    # G14 confirms has never been observed on any captured fixture; M1's
    # SessionStartEvent interface (task-4-brief.md) carries no such field, so
    # this is always null for now rather than guessed at.
    return {"source": event.source, "model": event.model, "cc_effort": None}


def _build_prompt_fields(event: PromptEvent, policy: Policy) -> dict[str, object]:
    excerpt, hits, sanitized, failed = _process_excerpt(
        event.prompt, policy.store.prompt_max_chars, 0, never_send=False
    )
    return {
        "prompt_excerpt": excerpt,
        "redaction_hits": hits,
        "sanitized_chars": sanitized,
        "redaction_failed": failed,
    }


def _build_post_fields(event: PostEvent, policy: Policy) -> dict[str, object]:
    from . import gates

    tool_input = event.tool_input
    command = _command_from(tool_input)
    never_send = gates.is_never_send(event.tool_name, tool_input, policy)
    is_check_val = _is_check_excluding_command_trigger(event.tool_name, command, tool_input, policy)

    raw_input = _raw_input_excerpt(event.tool_name, tool_input)
    raw_output, out_kind = _tool_output.tool_output_text(event.tool_name, event.tool_response)
    raw_bytes = _tool_output.raw_output_bytes(event.tool_name, event.tool_response)
    soft_fail = gates.is_soft_fail_candidate(event.tool_name, command, raw_output, policy)

    input_excerpt, in_hits, in_sanitized, in_failed = _process_excerpt(
        raw_input, policy.store.input_excerpt_max, 0, never_send
    )
    out_head, out_tail, out_hits, out_sanitized, out_failed = _process_head_tail(
        raw_output, policy.store.excerpt_head, policy.store.excerpt_tail, never_send
    )

    mcp_server_obj = None
    if event.mcp_server is not None:
        name = event.mcp_server.get("name")
        source = event.mcp_server.get("source")
        mcp_server_obj = {
            "name": name if isinstance(name, str) else None,
            "source": source if isinstance(source, str) else None,
        }

    duration_ms = float(event.duration_ms) if event.duration_ms is not None else 0.0

    return {
        "tool_use_id": event.tool_use_id,
        "tool_name": event.tool_name,
        "input_excerpt": input_excerpt,
        "out_head": out_head,
        "out_tail": out_tail,
        "out_kind": out_kind,
        "raw_bytes": raw_bytes,
        "duration_ms": duration_ms,
        "is_check": is_check_val,
        "soft_fail_candidate": soft_fail,
        "mcp_server": mcp_server_obj,
        "redaction_hits": in_hits + out_hits,
        "sanitized_chars": in_sanitized + out_sanitized,
        "status": "ok",
        "never_send": never_send,
        "redaction_failed": in_failed or out_failed,
    }


def _build_post_fail_fields(event: PostFailEvent, policy: Policy) -> dict[str, object]:
    tool_input = event.tool_input
    command = _command_from(tool_input)
    never_send = _never_send_for(event.tool_name, tool_input, policy)
    is_check_val = _is_check_excluding_command_trigger(event.tool_name, command, tool_input, policy)

    raw_input = _raw_input_excerpt(event.tool_name, tool_input)
    input_excerpt, in_hits, in_sanitized, in_failed = _process_excerpt(
        raw_input, policy.store.input_excerpt_max, 0, never_send
    )
    error_excerpt, err_hits, err_sanitized, err_failed = _process_excerpt(
        event.error, policy.store.error_head, policy.store.error_tail, never_send
    )

    duration_ms = float(event.duration_ms) if event.duration_ms is not None else 0.0
    exit_code = parsers.parse_exit_code(event.error) if not never_send else None

    return {
        "tool_use_id": event.tool_use_id,
        "tool_name": event.tool_name,
        "input_excerpt": input_excerpt,
        "status": "error",
        "exit_code": exit_code,
        "is_interrupt": event.is_interrupt,
        "error_excerpt": error_excerpt,
        "duration_ms": duration_ms,
        "is_check": is_check_val,
        "never_send": never_send,
        "redaction_hits": in_hits + err_hits,
        "sanitized_chars": in_sanitized + err_sanitized,
        "redaction_failed": in_failed or err_failed,
    }


def _never_send_for(tool_name: str, tool_input: Mapping[str, object], policy: Policy) -> bool:
    from . import gates

    return gates.is_never_send(tool_name, tool_input, policy)


def _build_stop_fields(event: StopEvent, policy: Policy) -> dict[str, object]:
    from . import claims as claims_mod

    normalized, removed = textnorm.normalize(event.last_assistant_message)
    redacted, hits = redact.redact(normalized)
    failed = hits == -1
    if failed:
        redacted = _REDACTION_FAILED_MARKER
        hits = 0

    claim_list = list(claims_mod.extract_claims(redacted, policy))
    excerpt = textnorm.truncate_anchored(redacted, policy.store.final_message_max_chars, 0)

    return {
        "stop_hook_active": event.stop_hook_active,
        "final_message_excerpt": excerpt,
        "claims": claim_list,
        "background_tasks_n": event.background_tasks_n,
        "redaction_hits": hits,
        "sanitized_chars": removed,
        "redaction_failed": failed,
    }


def _build_session_end_fields(event: SessionEndEvent) -> dict[str, object]:
    return {"reason": event.reason}


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
        return policy_mod.load_policy(path=policy_mod._default_policy_path())


def record(payload: Mapping[str, object]) -> str:
    """Parse, build, and append one ledger row for `payload`.

    Returns `"ok"` on success. Raises `parsers.ParseError` for a payload M1
    does not record (PreToolUse, SubagentStop, an unknown hook, or a
    malformed required field); the caller (Task 5's entry point) treats that
    as `skipped`. Any other unexpected exception also propagates, and the
    caller treats it as `exception` -- both per the fail-open model-path
    contract (global-constraints.md).
    """
    event = parsers.parse_event(payload)
    policy = _load_policy_fail_open()
    row = build_row(event, policy, time.time())
    ledger.append_row(row)
    return "ok"


__all__ = ["build_row", "record"]
