"""Tests for verdict_hot.recorders (task-4-brief.md).

Every committed M1 fixture must parse and build a row that validates
against `schemas/ledger-v1.json`; a handful of tests target the specific
privacy and purity guarantees called out in the brief and controller notes:
secrets never reach the row, never-send suppresses file content, and
`build_row` never mutates its input.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from verdict_hot import ledger, parsers, recorders
from verdict_hot.policy import Policy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema_check import validate_row  # noqa: E402

HOOKS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"

ALL_M1_FIXTURES = [
    "session_start.json",
    "user_prompt_submit.json",
    "post_tool_use_bash.json",
    "post_tool_use_write.json",
    "post_tool_use_edit.json",
    "post_tool_use_read.json",
    "post_tool_use_agent.json",
    "post_tool_use_failure_bash.json",
    "stop.json",
    "session_end.json",
]


def _load(name: str) -> dict[str, Any]:
    return dict(json.loads((HOOKS_FIXTURES / name).read_text(encoding="utf-8")))


@pytest.mark.parametrize("fixture_name", ALL_M1_FIXTURES)
def test_every_m1_fixture_builds_a_schema_valid_row(
    fixture_name: str, default_policy: Policy
) -> None:
    payload = _load(fixture_name)
    event = parsers.parse_event(payload)

    row = recorders.build_row(event, default_policy, now=1_700_000_000.0)

    validate_row(row)  # raises AssertionError on any mismatch


def test_build_row_sets_schema_v_and_plugin_version(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("session_start.json"))

    row = recorders.build_row(event, default_policy, now=0.0)

    assert row["schema_v"] == 1
    assert isinstance(row["plugin_version"], str) and row["plugin_version"]
    assert row["ts"] == 0.0


def test_session_start_fixture_yields_explicit_null_prompt_id(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("session_start.json"))

    row = recorders.build_row(event, default_policy, now=0.0)

    assert "prompt_id" in row
    assert row["prompt_id"] is None
    assert row["agent_id"] is None
    assert row["event"] == "session_start"
    assert row["cc_effort"] is None


def test_post_fail_fixture_yields_error_status_and_exit_code(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("post_tool_use_failure_bash.json"))

    row = recorders.build_row(event, default_policy, now=0.0)

    assert row["event"] == "post_fail"
    assert row["status"] == "error"
    assert row["exit_code"] == 3
    assert row["is_interrupt"] is False


def test_secret_in_bash_stdout_never_reaches_the_row(default_policy: Policy) -> None:
    payload = _load("post_tool_use_bash.json")
    secret = "sk-or-v1-" + "a1b2c3d4" * 8  # 64 hex chars, matches the vendored openrouter rule
    payload["tool_response"] = {
        "stdout": f"here is a token: {secret}",
        "stderr": "",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
    }
    event = parsers.parse_event(payload)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert secret not in serialized
    redaction_hits = row["redaction_hits"]
    assert isinstance(redaction_hits, int)
    assert redaction_hits >= 1


def test_write_to_env_file_is_never_send_with_no_file_content(default_policy: Policy) -> None:
    payload = _load("post_tool_use_write.json")
    payload["tool_input"] = {"content": "SECRET_KEY=abcdef1234567890", "file_path": "/tmp/.env"}
    payload["tool_response"] = {
        "type": "create",
        "filePath": "/tmp/.env",
        "content": "SECRET_KEY=abcdef1234567890",
        "structuredPatch": [],
        "originalFile": None,
        "userModified": False,
    }
    event = parsers.parse_event(payload)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert row["never_send"] is True
    assert "SECRET_KEY" in payload["tool_input"]["content"]  # sanity: fixture really has it
    assert "SECRET_KEY" not in serialized
    assert "abcdef1234567890" not in serialized
    assert row["input_excerpt"] == "[never-send]"


def test_build_row_does_not_mutate_its_input(default_policy: Policy) -> None:
    payload = _load("post_tool_use_bash.json")
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)
    event_before = copy.deepcopy(
        {
            "tool_input": dict(event.tool_input),
            "tool_response": copy.deepcopy(event.tool_response),
            "common": event.common._asdict(),
        }
    )
    policy_before = copy.deepcopy(default_policy._asdict())

    recorders.build_row(event, default_policy, now=0.0)

    assert dict(event.tool_input) == event_before["tool_input"]
    assert event.tool_response == event_before["tool_response"]
    assert event.common._asdict() == event_before["common"]
    assert default_policy._asdict() == policy_before


def test_build_row_is_pure_same_inputs_same_output(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("stop.json"))

    row1 = recorders.build_row(event, default_policy, now=123.0)
    row2 = recorders.build_row(event, default_policy, now=123.0)

    assert row1 == row2


def test_stop_row_carries_claims_and_no_gate_reason_or_arm(default_policy: Policy) -> None:
    payload = _load("stop.json")
    payload["last_assistant_message"] = "I fixed the bug and the tests are passing."
    event = parsers.parse_event(payload)

    row = recorders.build_row(event, default_policy, now=0.0)

    assert row["event"] == "stop"
    assert isinstance(row["claims"], list)
    assert len(row["claims"]) >= 1
    assert "gate_reason" not in row
    assert "arm" not in row


def test_agent_tool_input_excerpt_uses_description_not_full_prompt(
    default_policy: Policy,
) -> None:
    event = parsers.parse_event(_load("post_tool_use_agent.json"))

    row = recorders.build_row(event, default_policy, now=0.0)

    assert row["input_excerpt"] == "Run shell command and report output"
    assert "Run the shell command" not in row["input_excerpt"]


def test_write_input_excerpt_is_file_path_only(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("post_tool_use_write.json"))

    row = recorders.build_row(event, default_policy, now=0.0)

    input_excerpt = row["input_excerpt"]
    assert isinstance(input_excerpt, str)
    assert input_excerpt.endswith("note.txt")
    assert input_excerpt != "hi"


def test_mcp_server_field_present_when_given(default_policy: Policy) -> None:
    payload = _load("post_tool_use_bash.json")
    payload["tool_name"] = "mcp__github__search_repos"
    payload["mcp_server"] = {"name": "github", "source": "project"}
    event = parsers.parse_event(payload)

    row = recorders.build_row(event, default_policy, now=0.0)

    assert row["mcp_server"] == {"name": "github", "source": "project"}


def test_mcp_server_field_null_when_absent(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("post_tool_use_bash.json"))

    row = recorders.build_row(event, default_policy, now=0.0)

    assert row["mcp_server"] is None


# --- record(): parse + build + append --------------------------------------


def test_record_appends_a_row_readable_from_the_ledger(isolated_verdict_home: Path) -> None:
    payload = _load("session_start.json")

    outcome = recorders.record(payload)

    assert outcome == "ok"
    rows = ledger.read_session(payload["session_id"])
    assert len(rows) == 1
    assert rows[0]["event"] == "session_start"
    validate_row(rows[0])


def test_record_raises_parse_error_for_pre_tool_use(isolated_verdict_home: Path) -> None:
    payload = _load("pre_tool_use_bash.json")

    with pytest.raises(parsers.ParseError):
        recorders.record(payload)


def test_record_falls_back_to_packaged_default_on_broken_user_policy(
    isolated_verdict_home: Path,
) -> None:
    isolated_verdict_home.mkdir(parents=True, exist_ok=True)
    (isolated_verdict_home / "policy.json").write_text("not json", encoding="utf-8")
    payload = _load("session_end.json")

    outcome = recorders.record(payload)

    assert outcome == "ok"
    rows = ledger.read_session(payload["session_id"])
    assert rows[0]["event"] == "session_end"
