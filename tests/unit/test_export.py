"""Tests for `verdict export --goldset` (task-6-brief.md ruling 3)."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

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
    ledger.append_row(_action_row("s1"))
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
    ledger.append_row(_action_row("s1"))
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    row = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["contributor_id"] == "anonymous"


def test_export_contributor_id_is_hashed_with_a_salt(
    cli_verdict_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_CONTRIBUTOR_SALT", "some-salt")
    ledger.append_row(_verdict_row("s1"))
    ledger.append_row(_action_row("s1"))
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    row = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["contributor_id"] != "anonymous"
    assert _ID_HEX_RE.match(row["contributor_id"])
    assert len(row["contributor_id"]) == 16


def _recursive_strings(value: object) -> list[str]:
    """Every string reachable from `value`: dict keys, dict values, list
    items, and scalars -- so a free-text leak buried inside a nested dict
    (e.g. a `tool_counts` key) is never skipped (fix round 1, I1)."""
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            found.append(k)
            found.extend(_recursive_strings(v))
    elif isinstance(value, list):
        for item in value:
            found.extend(_recursive_strings(item))
    return found


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
        for value in _recursive_strings(row):
            cleaned, rule_ids = redact_mod.redact_detail(value)
            assert cleaned == value, f"{value!r} was redacted"
            assert rule_ids == (), f"{value!r} matched redaction rule(s) {rule_ids}"


# --- I1: MCP tool names never leak a server/product name via tool_counts -------


def test_tool_counts_buckets_mcp_tool_names_and_keeps_builtin_names(
    cli_verdict_home: Path, tmp_path: Path
) -> None:
    ledger.append_row(_post_row("s1", "Bash"))
    ledger.append_row(_post_row("s1", "mcp__github-mcp__search_issues"))
    ledger.append_row(_post_row("s1", "mcp__acme_corp_internal__do_thing"))
    ledger.append_row(_verdict_row("s1"))
    ledger.append_row(_action_row("s1"))
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    row = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["tool_counts"] == {"Bash": 1, "mcp": 2}
    for value in _recursive_strings(row):
        assert "github" not in value.lower()
        assert "acme" not in value.lower()


# --- C2: each verdict row attributes to the NEXT action row for its key --------


def test_two_stops_in_one_prompt_each_attribute_to_their_own_action_and_span(
    cli_verdict_home: Path, tmp_path: Path
) -> None:
    # Stop 1: one verdict row, then its own action row (a block).
    ledger.append_row(_verdict_row("s1", answer={"type": "noul", "noul": 0.5}))
    ledger.append_row(_action_row("s1", action="block", rule_id="R1"))

    # A step that happens strictly AFTER stop 1 (e.g. a retry) -- it must
    # count toward stop 2's span, never stop 1's.
    ledger.append_row(_post_row("s1", "Bash"))

    # Stop 2: a second verdict row sharing the same (prompt_id, agent_id)
    # key, then its own action row (a clean pass).
    ledger.append_row(_verdict_row("s1", answer={"type": "noul", "noul": 0.9}))
    ledger.append_row(_action_row("s1", action="pass", rule_id=None))

    out_path = tmp_path / "goldset.jsonl"
    export.main(["--goldset", "--out", str(out_path)])

    rows: list[dict[str, Any]] = [
        json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    first, second = rows

    assert first["answer"] == 0.5
    assert first["action"] == "block"
    assert first["rule_id"] == "R1"
    assert first["n_steps"] == 0
    assert first["tool_counts"] == {}

    assert second["answer"] == 0.9
    assert second["action"] == "pass"
    assert second["rule_id"] is None
    assert second["n_steps"] == 1
    assert second["tool_counts"] == {"Bash": 1}


def test_a_verdict_group_with_no_following_action_row_is_never_exported(
    cli_verdict_home: Path, tmp_path: Path
) -> None:
    ledger.append_row(_verdict_row("s1"))  # no action row ever follows
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    assert out_path.read_text(encoding="utf-8") == ""


# --- M7: the output file is written 0600 ---------------------------------------


def test_output_file_is_mode_0600(cli_verdict_home: Path, tmp_path: Path) -> None:
    ledger.append_row(_verdict_row("s1"))
    ledger.append_row(_action_row("s1"))
    out_path = tmp_path / "goldset.jsonl"

    export.main(["--goldset", "--out", str(out_path)])

    assert stat.S_IMODE(out_path.stat().st_mode) == 0o600


# --- Task 7 cleanup: no double-close once os.fdopen owns the fd ----------------


def test_write_goldset_does_not_double_close_the_fd_when_the_write_loop_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller notes addendum (task-7-brief.md): once `os.fdopen`
    succeeds, the `with` block owns `fd` and closes it itself on every exit
    path, including an exception raised while writing a row. The `except`
    handler in `_write_goldset` must not also call `os.close(fd)` in that
    case -- by the time it would, the OS may already have reused that
    descriptor number for an unrelated file. Regression test for
    `export.py`'s pre-fix `_write_goldset`, which called `os.close(fd)`
    unconditionally in its `except` handler."""
    close_calls: list[int] = []
    real_close = os.close

    def _tracking_close(fd: int) -> None:
        close_calls.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "close", _tracking_close)

    out_path = tmp_path / "goldset.jsonl"
    bad_rows: list[dict[str, Any]] = [{"ok": object()}]  # not JSON-serializable

    with pytest.raises(TypeError):
        export._write_goldset(out_path, bad_rows)

    assert close_calls == []
