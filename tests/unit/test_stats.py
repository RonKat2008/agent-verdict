"""Tests for `verdict stats` (task-6-brief.md)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_verdict import stats
from agent_verdict.verdict_hot import ledger


@pytest.fixture
def cli_verdict_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VERDICT_HOME", str(home))
    return home


def _row(event: str, session_id: str, ts: float, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_v": 1,
        "session_id": session_id,
        "event": event,
        "ts": ts,
        "prompt_id": None,
        "agent_id": None,
    }
    row.update(extra)
    return row


def _seed_synthetic_ledger() -> None:
    ledger.append_row(_row("session_start", "s1", 1000.0))
    ledger.append_row(_row("prompt", "s1", 1001.0, redaction_hits=1))
    ledger.append_row(_row("post", "s1", 1002.0, never_send=False, redaction_hits=0))
    ledger.append_row(_row("post", "s1", 1003.0, never_send=True, redaction_hits=2))
    ledger.append_row(
        _row("post_fail", "s1", 1004.0, exit_code=3, never_send=False, redaction_hits=0)
    )
    ledger.append_row(_row("stop", "s1", 1005.0, claims=["done"], redaction_hits=0))
    ledger.append_row(_row("stop", "s2", 2000.0, claims=[], redaction_hits=0))
    ledger.append_row(_row("session_start", "s2", 2500.0))


def test_compute_stats_gives_exact_counts(cli_verdict_home: Path) -> None:
    _seed_synthetic_ledger()

    result = stats.compute_stats()

    assert result.sessions == 2
    assert result.prompts == 1
    assert result.tool_rows == 2
    assert result.failure_rows == 1
    assert result.stops == 2
    assert result.stops_with_claim == 1
    assert result.never_send_rows == 1
    assert result.redaction_hits == 3
    assert result.rows_per_event == {
        "session_start": 2,
        "prompt": 1,
        "post": 2,
        "post_fail": 1,
        "stop": 2,
    }
    expected_start = datetime.fromtimestamp(1000.0, tz=UTC).isoformat()
    expected_end = datetime.fromtimestamp(2500.0, tz=UTC).isoformat()
    assert result.date_range == {"start": expected_start, "end": expected_end}


def test_compute_stats_on_empty_ledger(cli_verdict_home: Path) -> None:
    result = stats.compute_stats()

    assert result.sessions == 0
    assert result.stops == 0
    assert result.date_range == {"start": None, "end": None}


def test_json_flag_prints_valid_json(
    cli_verdict_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_synthetic_ledger()

    assert stats.main(["--json"]) == 0

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["sessions"] == 2
    assert parsed["stops"] == 2


def test_count_flag_prints_only_the_stop_count(
    cli_verdict_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_synthetic_ledger()

    assert stats.main(["--count"]) == 0

    assert capsys.readouterr().out.strip() == "2"


def test_default_output_is_human_readable_text(
    cli_verdict_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_synthetic_ledger()

    assert stats.main([]) == 0

    out = capsys.readouterr().out
    assert "sessions: 2" in out
    assert "stops: 2" in out


def _action_row(session_id: str, ts: float, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_v": 1,
        "session_id": session_id,
        "event": "action",
        "ts": ts,
        "prompt_id": None,
        "agent_id": None,
        "action": "pass",
        "would_have": None,
        "mode": "shadow",
        "hook_ms": 1.0,
        "open_failures": [],
    }
    row.update(extra)
    return row


def _seed_action_rows() -> None:
    # One `stop` row per real Stop event (written by recorders.record
    # regardless of what stop.handle does), paired with each `action` row
    # below -- ruling I2: jev_reach_rate is evidence-action-rows divided by
    # STOP rows, not action rows, since a Stop whose handler raised writes
    # a `stop` row and no `action` row at all.
    for ts in (1.0, 2.0, 3.0, 4.0, 5.0):
        ledger.append_row(_stop_row("s1", ts))
    ledger.append_row(_action_row("s1", 1.0, action="pass", gate_reason="evidence"))
    ledger.append_row(
        _action_row("s1", 2.0, action="pass", would_have="block", gate_reason="evidence")
    )
    ledger.append_row(
        _action_row("s1", 3.0, action="flag", would_have=None, gate_reason="evidence")
    )
    ledger.append_row(_action_row("s1", 4.0, action="pass", gate_reason="skipped_plan_mode"))
    ledger.append_row(_action_row("s1", 5.0, action="gate_unavailable", gate_reason="no_key"))


def _stop_row(session_id: str, ts: float) -> dict[str, object]:
    return {
        "schema_v": 1,
        "session_id": session_id,
        "event": "stop",
        "ts": ts,
        "prompt_id": None,
        "agent_id": None,
        "stop_hook_active": False,
        "final_message_excerpt": "",
        "claims": [],
        "background_tasks_n": 0,
    }


def test_jev_reach_rate_and_action_breakdowns(cli_verdict_home: Path) -> None:
    _seed_action_rows()

    result = stats.compute_stats()

    assert result.jev_reach_rate == pytest.approx(3 / 5)
    assert result.actions_by_kind == {"pass": 3, "flag": 1, "gate_unavailable": 1}
    assert result.would_have_by_kind == {"block": 1}
    assert result.gate_unavailable_count == 1


def test_jev_reach_rate_divides_by_stop_rows_not_action_rows(cli_verdict_home: Path) -> None:
    """A Stop whose handler raised writes a `stop` row (recorders.record
    runs first) and no `action` row at all -- ruling I2."""
    _seed_action_rows()
    # A sixth Stop event that never got as far as writing an action row.
    ledger.append_row(_stop_row("s1", 6.0))

    result = stats.compute_stats()

    assert result.jev_reach_rate == pytest.approx(3 / 6)


def test_jev_reach_rate_is_zero_with_no_action_rows(cli_verdict_home: Path) -> None:
    result = stats.compute_stats()

    assert result.jev_reach_rate == 0.0
    assert result.actions_by_kind == {}
    assert result.would_have_by_kind == {}
    assert result.gate_unavailable_count == 0


def test_breaker_open_reflects_the_breaker_state_file(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_verdict.verdict_hot.breaker import Breaker

    result_closed = stats.compute_stats()
    assert result_closed.breaker_open is False

    Breaker().record(429, 1000.0)
    Breaker().record(429, 1000.0)
    Breaker().record(429, 1000.0)
    monkeypatch.setattr("time.time", lambda: 1000.0)

    result_open = stats.compute_stats()
    assert result_open.breaker_open is True


def test_non_live_verdict_rows_are_counted(cli_verdict_home: Path) -> None:
    """Final review I5: answers replayed from a cassette or injected by the
    fake provider must be visible as such."""
    base = {
        "schema_v": 1,
        "session_id": "s9",
        "event": "verdict",
        "prompt_id": "p1",
        "agent_id": None,
        "question_key": "claims_done",
        "question_type": "noul",
        "answer": {"type": "noul", "noul": 0.5},
        "provider": "openrouter",
        "model_returned": "m",
        "input_tokens": 1,
        "conn_ms": 0.0,
        "infer_ms": 0.0,
        "policy_version": "2026.09.1",
    }
    ledger.append_row({**base, "transport": "cassette"})
    ledger.append_row({**base, "transport": "fake"})
    ledger.append_row({**base, "transport": "live"})
    ledger.append_row(base)  # pre-stamp rows have unknown provenance: non-live

    result = stats.compute_stats()

    assert result.non_live_verdicts == 3
    assert result.to_dict()["non_live_verdicts"] == 3
