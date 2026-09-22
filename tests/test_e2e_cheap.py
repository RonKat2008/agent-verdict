"""Tests for scripts/e2e_cheap.py's pure stream/ledger assertions (gate G1.4).

No `claude` process is spawned here -- see the script's own docstring for
why real `claude -p` calls are budgeted separately and run at most a
handful of times total.
"""

from __future__ import annotations

import json
from typing import Any

import e2e_cheap as ec
import pytest


def _stop_response(
    payload: dict[str, Any], *, exit_code: int = 0, outcome: str = "success"
) -> dict[str, Any]:
    """The verified Stop `hook_response` envelope (VERIFIED_FACTS A22)."""
    stdout = json.dumps(payload, separators=(",", ":")) + "\n"
    return {
        "type": "system",
        "subtype": "hook_response",
        "hook_id": "h1",
        "hook_name": "Stop",
        "hook_event": "Stop",
        "output": stdout,
        "stdout": stdout,
        "stderr": "",
        "exit_code": exit_code,
        "outcome": outcome,
    }


def test_parse_stream_skips_blank_lines_and_non_json() -> None:
    text = '{"type": "system", "subtype": "init"}\n\nnot json\n{"type": "result"}\n'
    events = ec.parse_stream(text)
    assert events == [{"type": "system", "subtype": "init"}, {"type": "result"}]


def test_assert_plugin_loaded_cleanly_passes_with_no_plugin_errors() -> None:
    events = [{"type": "system", "subtype": "init", "plugin_errors": []}]
    ec.assert_plugin_loaded_cleanly(events)  # must not raise


def test_assert_plugin_loaded_cleanly_fails_with_no_init_message() -> None:
    with pytest.raises(ec.AssertionFailure, match="no system/init"):
        ec.assert_plugin_loaded_cleanly([{"type": "result"}])


def test_assert_plugin_loaded_cleanly_fails_when_plugin_errors_present() -> None:
    events = [{"type": "system", "subtype": "init", "plugin_errors": ["boom"]}]
    with pytest.raises(ec.AssertionFailure, match="plugin_errors"):
        ec.assert_plugin_loaded_cleanly(events)


def test_no_hook_error_text_passes_on_clean_stream() -> None:
    ec.assert_no_hook_error_text('{"type": "result", "text": "all good"}')


def test_no_hook_error_text_ignores_a_pretooluse_notice() -> None:
    """agent-verdict registers no PreToolUse hook (plugin/hooks/hooks.json),
    so a PreToolUse hook error can only be another hook on the machine --
    observed in practice as the owner's own global "Fact-Forcing Gate"
    (G15, docs/VERIFIED_FACTS.md). It must never fail this gate.
    """
    text = "PreToolUse:Bash hook error: [Fact-Forcing Gate] present these facts..."
    ec.assert_no_hook_error_text(text)  # must not raise


def test_no_hook_error_text_fails_on_our_own_hook_event() -> None:
    text = "PostToolUseFailure:Bash hook error: something went wrong"
    with pytest.raises(ec.AssertionFailure, match="agent-verdict hook error"):
        ec.assert_no_hook_error_text(text)


def test_no_hook_error_text_fails_on_stop_hook_error() -> None:
    text = "Stop hook error: crashed"
    with pytest.raises(ec.AssertionFailure):
        ec.assert_no_hook_error_text(text)


def test_assert_ledger_rows_passes_with_all_required_rows() -> None:
    rows: list[dict[str, Any]] = [
        {"event": "session_start"},
        {"event": "prompt"},
        {"event": "post_fail", "exit_code": 3},
        {"event": "stop"},
    ]
    ec.assert_ledger_rows(rows)  # must not raise


def test_assert_ledger_rows_fails_when_a_required_event_is_missing() -> None:
    rows: list[dict[str, Any]] = [
        {"event": "prompt"},
        {"event": "post_fail", "exit_code": 3},
        {"event": "stop"},
    ]
    with pytest.raises(ec.AssertionFailure, match="session_start"):
        ec.assert_ledger_rows(rows)


def test_assert_ledger_rows_fails_without_a_matching_post_fail_exit_code() -> None:
    rows: list[dict[str, Any]] = [
        {"event": "session_start"},
        {"event": "prompt"},
        {"event": "post", "tool_name": "Bash", "status": "ok"},
        {"event": "stop"},
    ]
    with pytest.raises(ec.AssertionFailure, match="post_fail"):
        ec.assert_ledger_rows(rows)


# --- G2.4: stop-block/stop-shadow pure assertions (task-7-brief.md) -----------


def test_arg_parser_defaults_to_check_fail_and_accepts_stop_scenarios() -> None:
    parser = ec.build_arg_parser()
    assert parser.parse_args([]).scenario == "check-fail"
    assert parser.parse_args(["--scenario", "stop-block"]).scenario == "stop-block"
    assert parser.parse_args(["--scenario", "stop-shadow"]).scenario == "stop-shadow"


def test_run_stop_scenario_refuses_cleanly_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert ec.run_stop_scenario("stop-block") == 3


def test_find_hook_decisions_reads_our_stop_hook_response_envelope() -> None:
    """VERIFIED_FACTS A22: a decision is read only from a Stop `hook_response`
    whose stdout is our decision JSON; other Stop hooks that echo stdin
    (as the owner's global hooks do) are ignored."""
    events: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init"},
        {**_stop_response({"decision": "block", "reason": "x"}), "stdout": '{"session_id":"s"}'},
        _stop_response({"decision": "block", "reason": "Rule R1 ... step 2 (Bash, exit 1): x"}),
    ]
    decisions = ec.find_hook_decisions(events)
    assert decisions == [{"decision": "block", "reason": "Rule R1 ... step 2 (Bash, exit 1): x"}]


def test_assert_stop_hook_responses_clean_rejects_a_nonzero_exit() -> None:
    events = [
        _stop_response({"decision": "block", "reason": "x"}, exit_code=1, outcome="cancelled")
    ]
    with pytest.raises(ec.AssertionFailure, match="not a clean exit 0"):
        ec.assert_stop_hook_responses_clean(events)


def test_stop_block_banner_is_tolerated_only_when_a_block_is_expected() -> None:
    banner = (
        '{"type":"system","subtype":"notification","key":"stop-hook-error",'
        '"text":"Stop hook error occurred","priority":"immediate"}\n'
    )
    ec.assert_no_hook_error_text(banner, expect_block=True)
    with pytest.raises(ec.AssertionFailure):
        ec.assert_no_hook_error_text(banner, expect_block=False)


def test_find_hook_decisions_finds_nothing_in_a_plain_stream() -> None:
    events: list[dict[str, Any]] = [{"type": "result", "text": "all done"}]
    assert ec.find_hook_decisions(events) == []


def test_assert_stop_block_scenario_passes_with_one_named_block() -> None:
    events: list[dict[str, Any]] = [
        _stop_response({"decision": "block", "reason": "step 1 (Bash, exit 1): pytest -q"})
    ]
    rows: list[dict[str, Any]] = [{"event": "action", "action": "block"}]
    ec.assert_stop_block_scenario(events, rows)  # must not raise


def test_assert_stop_block_scenario_fails_with_no_decision_in_the_stream() -> None:
    with pytest.raises(ec.AssertionFailure, match="no decision:block"):
        ec.assert_stop_block_scenario([], [{"event": "action", "action": "block"}])


def test_assert_stop_block_scenario_fails_when_reason_does_not_name_the_step() -> None:
    events: list[dict[str, Any]] = [_stop_response({"decision": "block", "reason": "generic"})]
    with pytest.raises(ec.AssertionFailure, match="failing Bash step"):
        ec.assert_stop_block_scenario(events, [{"event": "action", "action": "block"}])


def test_assert_stop_block_scenario_fails_with_more_than_one_block_row() -> None:
    events: list[dict[str, Any]] = [
        _stop_response({"decision": "block", "reason": "step 1 (Bash, exit 1): pytest -q"})
    ]
    rows: list[dict[str, Any]] = [
        {"event": "action", "action": "block"},
        {"event": "action", "action": "block"},
    ]
    with pytest.raises(ec.AssertionFailure, match="exactly one block"):
        ec.assert_stop_block_scenario(events, rows)


def test_assert_stop_shadow_scenario_passes_with_would_have_block_and_no_decision() -> None:
    rows: list[dict[str, Any]] = [{"event": "action", "action": "pass", "would_have": "block"}]
    ec.assert_stop_shadow_scenario([], rows)  # must not raise


def test_assert_stop_shadow_scenario_fails_when_a_decision_leaks_to_the_stream() -> None:
    events: list[dict[str, Any]] = [_stop_response({"decision": "block", "reason": "x"})]
    rows: list[dict[str, Any]] = [{"event": "action", "action": "pass", "would_have": "block"}]
    with pytest.raises(ec.AssertionFailure, match="printed a decision"):
        ec.assert_stop_shadow_scenario(events, rows)


def test_assert_stop_shadow_scenario_fails_without_a_would_have_block_row() -> None:
    rows: list[dict[str, Any]] = [{"event": "action", "action": "pass"}]
    with pytest.raises(ec.AssertionFailure, match="would_have"):
        ec.assert_stop_shadow_scenario([], rows)
