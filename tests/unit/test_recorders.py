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


# --- Fix round 1: structural summaries instead of file/prompt content ------
#
# Critical + Important findings: tool_response for Write/Edit/Read carried
# whole file contents via the generic JSON dump, and Agent carried the full
# delegation prompt. Both are now replaced by per-tool structural summaries
# (or, for Agent, the subagent's result text plus structural fields) built
# from an explicit field allowlist, computed BEFORE the normalize/redact/
# truncate pipeline runs.


def _leaked_json_fragment(key: str, value: object) -> str:
    """The exact `"key": <value>` substring a naive `json.dumps` would emit."""
    return json.dumps({key: value})[1:-1]


def _row_str(row: dict[str, Any], key: str) -> str:
    value = row[key]
    assert isinstance(value, str)
    return value


def _out_head_summary(row: dict[str, Any]) -> dict[str, Any]:
    return dict(json.loads(_row_str(row, "out_head")))


def test_write_fixture_out_head_has_no_file_content(default_policy: Policy) -> None:
    payload = _load("post_tool_use_write.json")
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)
    assert isinstance(event.tool_response, dict)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert _leaked_json_fragment("content", event.tool_response["content"]) not in serialized
    assert row["out_kind"] == "structural"
    summary = _out_head_summary(row)
    assert summary["content_bytes"] == len(b"hi")
    assert summary["had_original"] is False
    assert summary["type"] == "create"
    assert summary["userModified"] is False
    assert summary["filePath"].endswith("note.txt")


def test_edit_fixture_out_head_has_no_file_content(default_policy: Policy) -> None:
    payload = _load("post_tool_use_edit.json")
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert _leaked_json_fragment("oldString", "hi") not in serialized
    assert _leaked_json_fragment("newString", "hello") not in serialized
    assert _leaked_json_fragment("originalFile", "hi") not in serialized
    assert row["out_kind"] == "structural"
    summary = _out_head_summary(row)
    assert summary["old_bytes"] == len(b"hi")
    assert summary["new_bytes"] == len(b"hello")
    assert summary["patch_hunks"] == 1
    assert summary["replaceAll"] is False
    assert summary["userModified"] is False


def test_read_fixture_out_head_has_no_file_content(default_policy: Policy) -> None:
    payload = _load("post_tool_use_read.json")
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert _leaked_json_fragment("content", "hi") not in serialized
    assert row["out_kind"] == "structural"
    summary = _out_head_summary(row)
    assert summary["numLines"] == 1
    assert summary["startLine"] == 1
    assert summary["totalLines"] == 1
    assert summary["type"] == "text"
    assert summary["filePath"].endswith("note.txt")


def test_agent_fixture_out_head_has_no_prompt(default_policy: Policy) -> None:
    payload = _load("post_tool_use_agent.json")
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)
    assert isinstance(event.tool_response, dict)
    prompt_text = event.tool_response["prompt"]
    assert isinstance(prompt_text, str) and len(prompt_text) > 20  # sanity: real fixture value

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert prompt_text not in serialized
    assert row["out_kind"] == "text"
    out_head = _row_str(row, "out_head")
    assert "async_launched" in out_head  # status: structural field kept
    assert "isAsync" in out_head or "true" in out_head.lower()


def test_notebook_edit_synthetic_payload_has_no_content(default_policy: Policy) -> None:
    """No real NotebookEdit fixture exists; this exercises the Edit-shaped
    branch reused for it (task-4 fix round 1, item 1) -- unverified against
    a real payload, per the controller's instruction."""
    payload = _load("post_tool_use_edit.json")
    payload["tool_name"] = "NotebookEdit"
    payload["tool_input"] = {
        "notebook_path": "/tmp/nb.ipynb",
        "cell_id": "abc123",
        "new_source": "print('hello')",
    }
    payload["tool_response"] = {
        "filePath": "/tmp/nb.ipynb",
        "oldString": "print('old')",
        "newString": "print('hello')",
        "originalFile": "print('old')",
        "replaceAll": False,
        "userModified": False,
        "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1}],
    }
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert "print('old')" not in serialized
    assert "print('hello')" not in serialized  # response newString, not tool_input
    assert row["out_kind"] == "structural"
    summary = _out_head_summary(row)
    assert summary["old_bytes"] == len(b"print('old')")
    assert summary["new_bytes"] == len(b"print('hello')")
    assert summary["patch_hunks"] == 1


def test_synthetic_write_with_large_content_never_leaks_marker(default_policy: Policy) -> None:
    marker = "UNIQUE_MARKER_XYZ_10KB"
    content = marker + ("x" * 10_000)
    payload = _load("post_tool_use_write.json")
    payload["tool_input"] = {"content": content, "file_path": "/tmp/big.txt"}
    payload["tool_response"] = {
        "type": "create",
        "filePath": "/tmp/big.txt",
        "content": content,
        "structuredPatch": [],
        "originalFile": None,
        "userModified": False,
    }
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert marker not in serialized
    summary = _out_head_summary(row)
    assert summary["content_bytes"] == len(content.encode("utf-8"))


def test_mcp_payload_with_large_text_field_is_omitted(default_policy: Policy) -> None:
    big_text = "B" * 5000
    payload = _load("post_tool_use_bash.json")
    payload["tool_name"] = "mcp__github__search_repos"
    payload["tool_input"] = {"query": "verdict"}
    payload["tool_response"] = {"status": "ok", "text": big_text}
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)

    row = recorders.build_row(event, default_policy, now=0.0)
    serialized = json.dumps(row)

    assert big_text not in serialized
    assert f"[omitted {len(big_text)} chars]" in serialized
    assert row["out_kind"] == "text"


def test_mcp_payload_with_short_text_field_is_kept(default_policy: Policy) -> None:
    payload = _load("post_tool_use_bash.json")
    payload["tool_name"] = "mcp__github__search_repos"
    payload["tool_input"] = {"query": "verdict"}
    payload["tool_response"] = {"status": "ok", "text": "short result"}
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)

    row = recorders.build_row(event, default_policy, now=0.0)

    assert "short result" in _row_str(row, "out_head")


def test_bash_output_still_kind_text(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("post_tool_use_bash.json"))
    row = recorders.build_row(event, default_policy, now=0.0)
    assert row["out_kind"] == "text"


def test_write_row_still_validates_against_schema(default_policy: Policy) -> None:
    event = parsers.parse_event(_load("post_tool_use_write.json"))
    row = recorders.build_row(event, default_policy, now=0.0)
    validate_row(row)


def test_build_row_purity_holds_for_write_event(default_policy: Policy) -> None:
    payload = _load("post_tool_use_write.json")
    event = parsers.parse_event(payload)
    assert isinstance(event, parsers.PostEvent)
    tool_input_before = dict(event.tool_input)
    tool_response_before = copy.deepcopy(event.tool_response)

    recorders.build_row(event, default_policy, now=0.0)

    assert dict(event.tool_input) == tool_input_before
    assert event.tool_response == tool_response_before
