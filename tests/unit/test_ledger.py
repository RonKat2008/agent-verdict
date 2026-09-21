from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _concurrency_worker import append_rows  # noqa: E402
from schema_check import validate_row  # noqa: E402
from verdict_hot import ledger, paths  # noqa: E402


def _row(event: str, session_id: str = "s1", **extra: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_v": 1,
        "ts": 0.0,
        "event": event,
        "session_id": session_id,
        "prompt_id": None,
        "agent_id": None,
        "plugin_version": "0.0.1",
        "permission_mode": "default",
    }
    base.update(extra)
    return base


EVENT_ROWS: dict[str, dict[str, Any]] = {
    "session_start": _row(
        "session_start", "evt", source="startup", cwd_hash="abc123", cc_effort="medium"
    ),
    "prompt": _row("prompt", "evt", prompt_excerpt="do the thing", redaction_hits=0),
    "post": _row(
        "post",
        "evt",
        tool_use_id="t1",
        tool_name="Bash",
        input_excerpt="ls",
        out_head="",
        out_tail="",
        raw_bytes=0,
        duration_ms=1.0,
        is_check=False,
        soft_fail_candidate=False,
        redaction_hits=0,
        sanitized_chars=0,
    ),
    "post_fail": _row(
        "post_fail",
        "evt",
        tool_use_id="t2",
        tool_name="Bash",
        input_excerpt="ls",
        status="error",
        exit_code=1,
        is_interrupt=False,
        error_excerpt="boom",
        duration_ms=1.0,
    ),
    "stop": _row(
        "stop",
        "evt",
        stop_hook_active=False,
        final_message_excerpt="done",
        claims=[],
        background_tasks_n=0,
    ),
    "session_end": _row("session_end", "evt", reason="clear"),
}


def test_append_then_read_round_trips_unicode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    row = _row("session_end", reason="clear", note="héllo 世界  ")
    ledger.append_row(row)
    rows = ledger.read_session("s1")
    assert rows == [row]


def test_append_row_writes_exactly_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    ledger.append_row(_row("session_end", reason="clear"))
    target = paths.session_file("s1")
    content = target.read_text(encoding="utf-8")
    assert content.count("\n") == 1
    assert content.endswith("\n")


def test_read_session_skips_corrupt_middle_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    ledger.append_row(
        _row("session_start", "s2", source="startup", cwd_hash="x", cc_effort="medium")
    )
    target = paths.session_file("s2")
    with target.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
    ledger.append_row(_row("session_end", "s2", reason="clear"))
    rows = ledger.read_session("s2")
    assert len(rows) == 2
    assert rows[0]["event"] == "session_start"
    assert rows[1]["event"] == "session_end"


def test_read_session_skips_blank_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    ledger.append_row(_row("session_end", "s2b", reason="clear"))
    target = paths.session_file("s2b")
    with target.open("a", encoding="utf-8") as fh:
        fh.write("\n\n   \n")
    rows = ledger.read_session("s2b")
    assert len(rows) == 1


def test_read_session_of_missing_session_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    assert ledger.read_session("does-not-exist") == []


def test_spool_when_first_row_schema_newer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    target = paths.session_file("s3")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema_v": 2, "event": "session_start", "session_id": "s3"}) + "\n",
        encoding="utf-8",
    )
    written = ledger.append_row(_row("session_end", "s3", reason="clear"))
    assert written == paths.pending_dir() / "s3.jsonl"
    assert not target.read_text(encoding="utf-8").count("session_end")


def test_append_row_requires_session_id_event_schema_v(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        ledger.append_row({"event": "session_end", "schema_v": 1})
    with pytest.raises(ValueError):
        ledger.append_row({"session_id": "s1", "schema_v": 1})
    with pytest.raises(ValueError):
        ledger.append_row({"session_id": "s1", "event": "session_end"})


def test_iter_sessions_lists_session_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    ledger.append_row(_row("session_end", "sess-a", reason="clear"))
    ledger.append_row(_row("session_end", "sess-b", reason="clear"))
    names = sorted(p.name for p in ledger.iter_sessions())
    assert names == ["sess-a.jsonl", "sess-b.jsonl"]


@pytest.mark.parametrize("event", sorted(EVENT_ROWS))
def test_m1_event_rows_validate_against_ledger_schema(
    event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    row = EVENT_ROWS[event]
    ledger.append_row(row)
    rows = ledger.read_session("evt")
    assert len(rows) == 1
    validate_row(rows[0])


@pytest.mark.slow
def test_16_processes_append_500_rows_without_interleaving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = str(tmp_path)
    session_id = "concurrent"
    n_procs = 16
    total = 500
    counts = [total // n_procs] * n_procs
    for i in range(total % n_procs):
        counts[i] += 1

    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=append_rows, args=(session_id, pid, counts[pid], home))
        for pid in range(n_procs)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
        assert p.exitcode == 0

    monkeypatch.setenv("VERDICT_HOME", home)
    content = paths.session_file(session_id).read_text(encoding="utf-8")
    lines = content.splitlines()
    assert len(lines) == total

    seen: set[tuple[int, int]] = set()
    for line in lines:
        parsed = json.loads(line)
        key = (parsed["pid"], parsed["n"])
        assert key not in seen, f"duplicate/interleaved row: {key}"
        seen.add(key)

    for pid in range(n_procs):
        for n in range(counts[pid]):
            assert (pid, n) in seen, f"missing row pid={pid} n={n}"
