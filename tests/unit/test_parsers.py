"""Tests for verdict_hot.parsers (task-4-brief.md).

Ground truth is `tests/fixtures/hooks/*.json` (real captured payloads), not
the plan prose alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from verdict_hot import parsers

HOOKS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"


def _load(name: str) -> dict[str, object]:
    return dict(json.loads((HOOKS_FIXTURES / name).read_text(encoding="utf-8")))


# --- Fixture parsing: one test per M1 event -------------------------------


def test_parses_session_start_fixture() -> None:
    event = parsers.parse_event(_load("session_start.json"))

    assert isinstance(event, parsers.SessionStartEvent)
    assert event.common.hook_event_name == "SessionStart"
    assert event.common.session_id == "56422690-bc20-4983-b21e-8514b3d56b4b"
    assert event.common.prompt_id is None  # G13: absent on SessionStart
    assert event.source == "startup"
    assert event.model is None


def test_parses_user_prompt_submit_fixture() -> None:
    event = parsers.parse_event(_load("user_prompt_submit.json"))

    assert isinstance(event, parsers.PromptEvent)
    assert event.common.prompt_id == "9ec829d2-3162-444c-8179-b417ac801235"
    assert event.prompt.startswith("Run this exact shell command")


def test_parses_post_tool_use_bash_fixture() -> None:
    event = parsers.parse_event(_load("post_tool_use_bash.json"))

    assert isinstance(event, parsers.PostEvent)
    assert event.tool_name == "Bash"
    assert event.tool_input["command"] == "echo hello"
    assert isinstance(event.tool_response, dict)
    assert event.tool_response["stdout"] == "hello"
    assert event.duration_ms == 497.0
    assert event.mcp_server is None


def test_parses_post_tool_use_write_fixture() -> None:
    event = parsers.parse_event(_load("post_tool_use_write.json"))

    assert isinstance(event, parsers.PostEvent)
    assert event.tool_name == "Write"
    file_path = event.tool_input["file_path"]
    assert isinstance(file_path, str)
    assert file_path.endswith("note.txt")


def test_parses_post_tool_use_edit_fixture() -> None:
    event = parsers.parse_event(_load("post_tool_use_edit.json"))

    assert isinstance(event, parsers.PostEvent)
    assert event.tool_name == "Edit"


def test_parses_post_tool_use_read_fixture() -> None:
    event = parsers.parse_event(_load("post_tool_use_read.json"))

    assert isinstance(event, parsers.PostEvent)
    assert event.tool_name == "Read"


def test_parses_post_tool_use_agent_fixture() -> None:
    event = parsers.parse_event(_load("post_tool_use_agent.json"))

    assert isinstance(event, parsers.PostEvent)
    assert event.tool_name == "Agent"
    assert event.tool_input["description"] == "Run shell command and report output"


def test_parses_post_tool_use_failure_bash_fixture() -> None:
    event = parsers.parse_event(_load("post_tool_use_failure_bash.json"))

    assert isinstance(event, parsers.PostFailEvent)
    assert event.tool_name == "Bash"
    assert event.error == "Exit code 3\nboom"
    assert event.is_interrupt is False
    assert parsers.parse_exit_code(event.error) == 3


def test_parses_stop_fixture() -> None:
    event = parsers.parse_event(_load("stop.json"))

    assert isinstance(event, parsers.StopEvent)
    assert event.stop_hook_active is False
    assert event.background_tasks_n == 0
    assert "hello" in event.last_assistant_message


def test_parses_session_end_fixture() -> None:
    event = parsers.parse_event(_load("session_end.json"))

    assert isinstance(event, parsers.SessionEndEvent)
    assert event.reason == "other"
    assert event.common.prompt_id == "9ec829d2-3162-444c-8179-b417ac801235"


# --- PreToolUse rejected; SubagentStop parses like Stop (task-4-brief.md) --


def test_pre_tool_use_raises_parse_error() -> None:
    with pytest.raises(parsers.ParseError) as excinfo:
        parsers.parse_event(_load("pre_tool_use_bash.json"))
    assert excinfo.value.field == "hook_event_name"


def test_subagent_stop_parses_like_stop() -> None:
    """task-4-brief.md, controller notes ruling 12: SubagentStop carries the
    same stop_hook_active/last_assistant_message/background_tasks shape
    (G7) as Stop, so it reuses the same StopEvent parser; agent_id/
    agent_type are carried by Common already."""
    event = parsers.parse_event(_load("subagent_stop.json"))

    assert isinstance(event, parsers.StopEvent)
    assert event.common.agent_id == "a6ff92b84da5e1deb"
    assert event.common.agent_type == "general-purpose"
    assert event.stop_hook_active is False
    assert "sub" in event.last_assistant_message


def test_unknown_hook_event_name_raises_parse_error() -> None:
    with pytest.raises(parsers.ParseError):
        parsers.parse_event({"hook_event_name": "SomethingNew"})


# --- Strict field validation ------------------------------------------------


def test_missing_session_id_raises_parse_error() -> None:
    payload = _load("session_start.json")
    del payload["session_id"]
    with pytest.raises(parsers.ParseError) as excinfo:
        parsers.parse_event(payload)
    assert excinfo.value.field == "session_id"


def test_wrong_type_duration_ms_raises_parse_error() -> None:
    payload = _load("post_tool_use_bash.json")
    payload["duration_ms"] = "not-a-number"
    with pytest.raises(parsers.ParseError):
        parsers.parse_event(payload)


def test_missing_tool_response_raises_parse_error_on_post() -> None:
    payload = _load("post_tool_use_bash.json")
    del payload["tool_response"]
    with pytest.raises(parsers.ParseError) as excinfo:
        parsers.parse_event(payload)
    assert excinfo.value.field == "tool_response"


def test_non_bool_is_interrupt_raises_parse_error() -> None:
    payload = _load("post_tool_use_failure_bash.json")
    payload["is_interrupt"] = "false"
    with pytest.raises(parsers.ParseError):
        parsers.parse_event(payload)


def test_unknown_extra_fields_are_ignored() -> None:
    payload = _load("session_start.json")
    payload["some_future_field"] = {"nested": True}
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.SessionStartEvent)


def test_mcp_server_parses_when_present() -> None:
    payload = _load("post_tool_use_bash.json")
    payload["mcp_server"] = {"name": "github", "source": "project"}
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)
    assert event.mcp_server == {"name": "github", "source": "project"}


# --- parse_exit_code ---------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("Exit code 3\nboom", 3),
        ("Exit code 0", 0),
        ("Command timed out", None),
        ("note\nExit code 1", None),
        ("", None),
        ("Exit code abc", None),
    ],
)
def test_parse_exit_code(error: str, expected: int | None) -> None:
    assert parsers.parse_exit_code(error) == expected


# --- Hypothesis: parse_event never raises anything but ParseError ----------

_json_scalars = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text()
)
_json_values = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5), st.dictionaries(st.text(), children, max_size=5)
    ),
    max_leaves=10,
)
_arbitrary_payloads = st.dictionaries(st.text(), _json_values, max_size=8)


@settings(max_examples=200)
@given(_arbitrary_payloads)
def test_parse_event_never_raises_anything_but_parse_error(
    payload: dict[str, object],
) -> None:
    try:
        result = parsers.parse_event(payload)
    except parsers.ParseError:
        return
    assert result is not None
