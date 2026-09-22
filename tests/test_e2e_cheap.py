"""Tests for scripts/e2e_cheap.py's pure stream/ledger assertions (gate G1.4).

No `claude` process is spawned here -- see the script's own docstring for
why real `claude -p` calls are budgeted separately and run at most a
handful of times total.
"""

from __future__ import annotations

from typing import Any

import e2e_cheap as ec
import pytest


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
