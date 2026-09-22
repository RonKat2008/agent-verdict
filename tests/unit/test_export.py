"""Tests for `verdict export --goldset` (task-6-brief.md ruling 3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from verdict_hot import redact as redact_mod

from agent_verdict import export
from agent_verdict.verdict_hot import ledger

_ID_HEX_RE = re.compile(r"^[0-9a-f]{16,64}$")
_MODEL_ID_RE = re.compile(r"^[\w./-]+$")


@pytest.fixture
def cli_verdict_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VERDICT_HOME", str(home))
    monkeypatch.delenv("VERDICT_CONTRIBUTOR_SALT", raising=False)
    return home


def _verdict_row(session_id: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_v": 1,
        "session_id": session_id,
        "event": "verdict",
        "ts": 1000.0,
        "prompt_id": "p1",
        "agent_id": None,
        "question_key": "claims_done",
        "question_type": "noul",
        "answer": {"type": "noul", "noul": 0.5},
        "provider": "openrouter",
        "model_returned": "typesafe/jev-1.13-20260917",
        "input_tokens": 100,
        "conn_ms": 1.0,
        "infer_ms": 2.0,
        "policy_version": "2026.09.1",
    }
    row.update(extra)
    return row


def _action_row(session_id: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_v": 1,
        "session_id": session_id,
        "event": "action",
        "ts": 1001.0,
        "prompt_id": "p1",
        "agent_id": None,
        "action": "pass",
        "would_have": None,
        "rule_id": None,
        "mode": "shadow",
        "hook_ms": 1.0,
        "open_failures": [],
    }
    row.update(extra)
    return row


def _post_row(session_id: str, tool_name: str, prompt_id: str = "p1") -> dict[str, object]:
    return {
        "schema_v": 1,
        "session_id": session_id,
        "event": "post",
        "ts": 999.0,
        "prompt_id": prompt_id,
        "agent_id": None,
        "tool_name": tool_name,
        "tool_use_id": "t1",
        "status": "ok",
        "exit_code": 0,
    }


def test_export_writes_jsonl_with_the_closed_field_set(
    cli_verdict_home: Path, tmp_path: Path
) -> None:
    ledger.append_row(_post_row("s1", "Bash"))
    ledger.append_row(_verdict_row("s1"))
    out_path = tmp_path / "goldset.jsonl"

    code = export.main(["--goldset", "--out", str(out_path)])

    assert code == 0
    lines = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    row = lines[0]
    expected_keys = {
        "event_id_hash",
        "contributor_id",
        "hook_kind",
        "tool_counts",
        "question_key",
        "question_type",
        "answer",
        "exit_code",
        "n_steps",
        "n_failures",
        "model_returned",
        "policy_version",
        "action",
        "would_have",
        "rule_id",
        "sha256_of_raw",
    }
    assert set(row) == expected_keys
    assert _ID_HEX_RE.match(row["event_id_hash"])
    assert _ID_HEX_RE.match(row["sha256_of_raw"])
    assert row["hook_kind"] in ("stop", "subagent-stop")
    assert row["answer"] == 0.5
    assert row["tool_counts"] == {"Bash": 1}
    assert _MODEL_ID_RE.match(row["model_returned"])


def test_export_contributor_id_is_anonymous_without_a_salt(
    cli_verdict_home: Path, tmp_path: Path
) -> None:
    ledger.append_row(_verdict_row("s1"))
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    row = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["contributor_id"] == "anonymous"


def test_export_contributor_id_is_hashed_with_a_salt(
    cli_verdict_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_CONTRIBUTOR_SALT", "some-salt")
    ledger.append_row(_verdict_row("s1"))
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    row = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["contributor_id"] != "anonymous"
    assert _ID_HEX_RE.match(row["contributor_id"])
    assert len(row["contributor_id"]) == 16


def test_export_never_leaks_free_text_or_matches_a_redaction_rule(
    cli_verdict_home: Path, tmp_path: Path
) -> None:
    ledger.append_row(
        _verdict_row(
            "s1",
            question_key="claim_c1",
            question_type="score",
            answer={"type": "score", "score": "mostly_complete"},
        )
    )
    ledger.append_row(_action_row("s1", action="flag", rule_id="R4"))
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    for line in out_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        for key, value in row.items():
            if not isinstance(value, str):
                continue
            cleaned, rule_ids = redact_mod.redact_detail(value)
            assert cleaned == value, f"{key}={value!r} was redacted"
            assert rule_ids == (), f"{key}={value!r} matched redaction rule(s) {rule_ids}"
