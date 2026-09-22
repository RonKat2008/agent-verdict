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
