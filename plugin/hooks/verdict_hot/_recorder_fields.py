"""Per-event field builders for `recorders.py` (split out to stay under the
400-line file cap, task-7-brief.md cleanup item 5).

Everything here is exactly as it was in `recorders.py`: the never-send
short-circuit and the normalize -> redact -> truncate text pipeline
(`recorders.py`'s own module docstring documents the field order), plus the
one field builder per M1 event type. `recorders.build_row` imports these by
name (the same private-cross-module-import convention `span.py` already
uses for `gates._strip_leading`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from . import parsers
from .parsers import (
    PostEvent,
    PostFailEvent,
    PromptEvent,
    SessionEndEvent,
    SessionStartEvent,
    StopEvent,
)

if TYPE_CHECKING:
    from .policy import Policy

_NEVER_SEND_MARKER = "[never-send]"
_REDACTION_FAILED_MARKER = "[redaction failed]"


def _process_excerpt(
    raw_text: str, head: int, tail: int, never_send: bool
) -> tuple[str, int, int, bool]:
    """One-field pipeline: returns (excerpt, redaction_hits, sanitized_chars, failed)."""
    if never_send:
        return _NEVER_SEND_MARKER, 0, 0, False
    from . import redact, textnorm

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
    from . import redact, textnorm

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
    from . import _tool_output

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
    from . import _tool_output, gates

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


def _never_send_for(tool_name: str, tool_input: Mapping[str, object], policy: Policy) -> bool:
    from . import gates

    return gates.is_never_send(tool_name, tool_input, policy)


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


def _build_stop_fields(event: StopEvent, policy: Policy) -> dict[str, object]:
    from . import claims as claims_mod
    from . import textnorm

    redacted, hits, removed, failed = textnorm.sanitize(event.last_assistant_message)
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


__all__ = [
    "_build_post_fail_fields",
    "_build_post_fields",
    "_build_prompt_fields",
    "_build_session_end_fields",
    "_build_session_start_fields",
    "_build_stop_fields",
]
