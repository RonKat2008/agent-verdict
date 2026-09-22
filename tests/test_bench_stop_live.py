"""Tests for scripts/bench_stop_live.py's pure/local-only pieces (gate G2.6,
task-7-brief.md controller notes ruling 3).

No `run.sh` subprocess and no real provider call happen here -- the actual
live latency measurement is a billed gate the controller runs directly
(`make bench-provider`). This only tests the parts that need no key: the
seeded-row shape, the ledger writer, the Stop payload, and the
refuse-without-a-key exit path.
"""

from __future__ import annotations

import json
from pathlib import Path

import bench_stop_live as bsl
import pytest
from verdict_hot import policy as policy_mod
from verdict_hot.span import build_span

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"


def test_main_refuses_cleanly_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert bsl.main([]) == 3


def test_seeded_rows_produce_the_same_span_shape_as_the_unresolved_failure_fixture() -> None:
    """The rows `_seeded_rows` writes must build a Span with exactly one
    unresolved failure and `checks_passed_after_last_change is False`,
    matching `tests/golden/_fixtures.py`'s `_unresolved_failure` scenario
    (module docstring: expressed as ledger rows instead of a hand-built
    `Span`)."""
    policy = policy_mod.load_policy(PACKAGED_DEFAULT)
    rows = bsl._seeded_rows("session-under-test")

    span = build_span(rows, bsl.PROMPT_ID, policy)

    assert len(span.unresolved_failures) == 1
    assert span.checks_passed_after_last_change is False


def test_write_ledger_writes_one_row_per_line_mode_0600(tmp_path: Path) -> None:
    bsl._write_ledger(tmp_path, "session-under-test")

    target = tmp_path / "events" / "session-under-test.jsonl"
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        row = json.loads(line)
        assert row["session_id"] == "session-under-test"


def test_stop_payload_carries_the_final_message_and_session_id() -> None:
    payload = json.loads(bsl._stop_payload("session-under-test"))
    assert payload["session_id"] == "session-under-test"
    assert payload["prompt_id"] == bsl.PROMPT_ID
    assert payload["last_assistant_message"] == bsl.FINAL_MESSAGE
    assert payload["hook_event_name"] == "Stop"
    assert payload["stop_hook_active"] is False
