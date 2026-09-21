"""Strict stdin parsers for the six M1 hook events (task-4-brief.md).

`parse_event` dispatches on `hook_event_name` and returns one of the six
frozen `NamedTuple` event types below (D-028: `NamedTuple`, never
`dataclass`, under `plugin/hooks/`). A missing required field or a field of
the wrong type raises `ParseError(field)`; unknown extra keys are ignored
(forward compatible with future Claude Code payload additions).

Ground truth is the captured fixtures in `tests/fixtures/hooks/*.json` and
`docs/VERIFIED_FACTS.md` section G, not the plan text alone (task-4-brief.md).
Notably: `prompt_id` is absent on `SessionStart` (G13); `agent_id` and
`agent_type` are not observed on any M1 top-level payload (only on
`SubagentStop`, which M1 does not record); `scratchpad_dir` and `effort`
were never observed on any captured fixture (G14) and M1's event interfaces
below do not carry them -- `cc_effort` is therefore always written as `null`
by `recorders.build_row` for M1's `session_start` rows.

`PreToolUse` and `SubagentStop` are out of scope for M1 (the controller's
recorders never register those two hook names); `parse_event` raises
`ParseError("hook_event_name")` for them so the M2 work can add real parsing
later without this module lying about what it currently supports.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import NamedTuple, Union

_EXIT_CODE_RE = re.compile(r"^Exit code (\d+)$")


class ParseError(Exception):
    """Raised with the offending field name when a payload fails to parse."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


class Common(NamedTuple):
    session_id: str
    prompt_id: str | None
    agent_id: str | None
    agent_type: str | None
    permission_mode: str | None
    cwd: str
    hook_event_name: str


class SessionStartEvent(NamedTuple):
    common: Common
    source: str
    model: str | None


class PromptEvent(NamedTuple):
    common: Common
    prompt: str


class PostEvent(NamedTuple):
    common: Common
    tool_name: str
    tool_use_id: str
    tool_input: Mapping[str, object]
    tool_response: object
    duration_ms: float | None
    mcp_server: Mapping[str, object] | None


class PostFailEvent(NamedTuple):
    common: Common
    tool_name: str
    tool_use_id: str
    tool_input: Mapping[str, object]
    error: str
    is_interrupt: bool
    duration_ms: float | None


class StopEvent(NamedTuple):
    common: Common
    stop_hook_active: bool
    last_assistant_message: str
    background_tasks_n: int


class SessionEndEvent(NamedTuple):
    common: Common
    reason: str


HookEvent = Union[  # noqa: UP007 - `X | Y` at runtime is 3.10+-only; this must run on 3.9
    SessionStartEvent, PromptEvent, PostEvent, PostFailEvent, StopEvent, SessionEndEvent
]


def _require_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ParseError(key)
    return value


def _require_nonempty_str(payload: Mapping[str, object], key: str) -> str:
    value = _require_str(payload, key)
    if not value:
        raise ParseError(key)
    return value


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ParseError(key)
    return value


def _require_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ParseError(key)
    return value


def _optional_number(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseError(key)
    return float(value)


def _require_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ParseError(key)
    return value


def _optional_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ParseError(key)
    return value


def _parse_common(payload: Mapping[str, object]) -> Common:
    return Common(
        session_id=_require_nonempty_str(payload, "session_id"),
        prompt_id=_optional_str(payload, "prompt_id"),
        agent_id=_optional_str(payload, "agent_id"),
        agent_type=_optional_str(payload, "agent_type"),
        permission_mode=_optional_str(payload, "permission_mode"),
        cwd=_require_nonempty_str(payload, "cwd"),
        hook_event_name=_require_nonempty_str(payload, "hook_event_name"),
    )


def _parse_session_start(payload: Mapping[str, object]) -> SessionStartEvent:
    common = _parse_common(payload)
    return SessionStartEvent(
        common=common,
        source=_require_str(payload, "source"),
        model=_optional_str(payload, "model"),
    )


def _parse_prompt(payload: Mapping[str, object]) -> PromptEvent:
    common = _parse_common(payload)
    return PromptEvent(common=common, prompt=_require_str(payload, "prompt"))


def _parse_post(payload: Mapping[str, object]) -> PostEvent:
    common = _parse_common(payload)
    if "tool_response" not in payload:
        raise ParseError("tool_response")
    return PostEvent(
        common=common,
        tool_name=_require_nonempty_str(payload, "tool_name"),
        tool_use_id=_require_nonempty_str(payload, "tool_use_id"),
        tool_input=_require_mapping(payload, "tool_input"),
        tool_response=payload["tool_response"],
        duration_ms=_optional_number(payload, "duration_ms"),
        mcp_server=_optional_mapping(payload, "mcp_server"),
    )


def _parse_post_fail(payload: Mapping[str, object]) -> PostFailEvent:
    common = _parse_common(payload)
    return PostFailEvent(
        common=common,
        tool_name=_require_nonempty_str(payload, "tool_name"),
        tool_use_id=_require_nonempty_str(payload, "tool_use_id"),
        tool_input=_require_mapping(payload, "tool_input"),
        error=_require_str(payload, "error"),
        is_interrupt=_require_bool(payload, "is_interrupt"),
        duration_ms=_optional_number(payload, "duration_ms"),
    )


def _parse_stop(payload: Mapping[str, object]) -> StopEvent:
    common = _parse_common(payload)
    background_tasks = payload.get("background_tasks")
    if not isinstance(background_tasks, list):
        raise ParseError("background_tasks")
    return StopEvent(
        common=common,
        stop_hook_active=_require_bool(payload, "stop_hook_active"),
        last_assistant_message=_require_str(payload, "last_assistant_message"),
        background_tasks_n=len(background_tasks),
    )


def _parse_session_end(payload: Mapping[str, object]) -> SessionEndEvent:
    common = _parse_common(payload)
    return SessionEndEvent(common=common, reason=_require_str(payload, "reason"))


_DISPATCH: dict[str, Callable[[Mapping[str, object]], HookEvent]] = {
    "SessionStart": _parse_session_start,
    "UserPromptSubmit": _parse_prompt,
    "PostToolUse": _parse_post,
    "PostToolUseFailure": _parse_post_fail,
    "Stop": _parse_stop,
    "SessionEnd": _parse_session_end,
}


def _parse_event_unsafe(payload: Mapping[str, object]) -> HookEvent:
    hook_event_name = payload.get("hook_event_name")
    if not isinstance(hook_event_name, str):
        raise ParseError("hook_event_name")
    builder = _DISPATCH.get(hook_event_name)
    if builder is None:
        # Covers PreToolUse and SubagentStop (M2) and anything unrecognized.
        raise ParseError("hook_event_name")
    return builder(payload)


def parse_event(payload: Mapping[str, object]) -> HookEvent:
    """Parse a hook stdin payload into one of the six M1 event types.

    Never raises anything other than `ParseError`: any unexpected exception
    (for example a `.get()` call landing on a value that is not actually a
    mapping, which arbitrary JSON-like input can produce) is converted to
    `ParseError`, so callers can rely on the two-outcome contract.
    """
    try:
        return _parse_event_unsafe(payload)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - contract: only ParseError escapes
        raise ParseError("payload") from exc


def parse_exit_code(error: str) -> int | None:
    """Extract the exit code from a `PostToolUseFailure` `error` string.

    Only matches when the FIRST LINE is exactly `Exit code N` (A2, G11):
    `"Command timed out"` and `"note\\nExit code 1"` both return `None`.
    """
    first_line = error.split("\n", 1)[0]
    match = _EXIT_CODE_RE.match(first_line)
    if match is None:
        return None
    return int(match.group(1))
