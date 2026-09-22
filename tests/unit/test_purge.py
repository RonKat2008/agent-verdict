"""Tests for `verdict purge` (task-6-brief.md ruling 2).

Every subprocess-free interactive path is exercised by feeding `main()` an
explicit `input_stream` (a `io.StringIO`) rather than real stdin, per the
hazards note about GNU `timeout` not existing on this machine -- there is
no subprocess here at all, so that hazard does not apply, but the same
"never depend on inherited stdin" discipline is kept anyway.
"""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import pytest

from agent_verdict import purge
from agent_verdict.verdict_hot import ledger, paths


@pytest.fixture
def cli_verdict_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VERDICT_HOME", str(home))
    return home


def _row(event: str, session_id: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_v": 1,
        "session_id": session_id,
        "event": event,
        "ts": 1.0,
        "prompt_id": None,
        "agent_id": None,
    }
    row.update(extra)
    return row


def _age(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_requires_exactly_one_selector(cli_verdict_home: Path) -> None:
    assert purge.main([]) == 2
    assert purge.main(["--session", "s1", "--all"]) == 2


def test_session_selector_deletes_with_yes_flag(cli_verdict_home: Path) -> None:
    target = ledger.append_row(_row("session_start", "s1"))

    code = purge.main(["--session", "s1", "--yes"])

    assert code == 0
    assert not target.exists()


def test_all_selector_deletes_every_session(cli_verdict_home: Path) -> None:
    ledger.append_row(_row("session_start", "s1"))
    ledger.append_row(_row("session_start", "s2"))

    code = purge.main(["--all", "--yes"])

    assert code == 0
    assert list(paths.events_dir().glob("*.jsonl")) == []


def test_older_than_selector_deletes_only_old_sessions(cli_verdict_home: Path) -> None:
    old_target = ledger.append_row(_row("session_start", "old"))
    _age(old_target, 10)
    new_target = ledger.append_row(_row("session_start", "new"))

    code = purge.main(["--older-than", "7d", "--yes"])

    assert code == 0
    assert not old_target.exists()
    assert new_target.exists()


def test_older_than_accepts_hour_suffix(cli_verdict_home: Path) -> None:
    old_target = ledger.append_row(_row("session_start", "old"))
    _age(old_target, 1)

    code = purge.main(["--older-than", "2h", "--yes"])

    assert code == 0
    assert not old_target.exists()


def _fake_tty(text: str, monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    stream = io.StringIO(text)
    monkeypatch.setattr(stream, "isatty", lambda: True)
    return stream


def test_confirmation_prompt_declined_keeps_the_file(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = ledger.append_row(_row("session_start", "s1"))

    code = purge.main(["--session", "s1"], input_stream=_fake_tty("n\n", monkeypatch))

    assert code == 0
    assert target.exists()


def test_confirmation_prompt_accepted_deletes_the_file(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = ledger.append_row(_row("session_start", "s1"))

    code = purge.main(["--session", "s1"], input_stream=_fake_tty("y\n", monkeypatch))

    assert code == 0
    assert not target.exists()


def test_non_tty_without_yes_refuses(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = ledger.append_row(_row("session_start", "s1"))
    non_tty = io.StringIO("y\n")
    monkeypatch.setattr(non_tty, "isatty", lambda: False)

    code = purge.main(["--session", "s1"], input_stream=non_tty)

    assert code == 2
    assert target.exists()
    # M6 (fix round 1): the refusal happens BEFORE anything about the
    # would-delete list is printed.
    out = capsys.readouterr().out
    assert "would delete" not in out
    assert "unlabeled corpus session" not in out


def test_never_touches_labels_or_hooklog_or_breaker_or_index(cli_verdict_home: Path) -> None:
    ledger.append_row(_row("session_start", "s1"))
    home = paths.verdict_home()
    (home / "labels.jsonl").write_text("keep\n", encoding="utf-8")
    (home / "hook.log").write_text("keep\n", encoding="utf-8")
    (home / "breaker.json").write_text("{}", encoding="utf-8")
    (home / "index.db").write_text("keep", encoding="utf-8")

    code = purge.main(["--all", "--yes"])

    assert code == 0
    assert (home / "labels.jsonl").read_text(encoding="utf-8") == "keep\n"
    assert (home / "hook.log").read_text(encoding="utf-8") == "keep\n"
    assert (home / "breaker.json").exists()
    assert (home / "index.db").exists()


def test_reports_unlabeled_corpus_sessions_before_confirming(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger.append_row(_row("session_start", "s1"))
    ledger.append_row(_row("stop", "s1"))

    purge.main(["--session", "s1"], input_stream=_fake_tty("n\n", monkeypatch))

    out = capsys.readouterr().out
    assert "unlabeled corpus session" in out


def test_malformed_session_id_exits_2_instead_of_raising(
    cli_verdict_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fix round 1, M1: `paths.session_file` raises `ValueError` on a
    malformed id; `purge` must turn that into one message and exit 2."""
    code = purge.main(["--session", "../etc/passwd", "--yes"])

    assert code == 2
    out = capsys.readouterr()
    assert (out.out + out.err).strip() != ""


def test_never_follows_a_symlinked_session_file(cli_verdict_home: Path, tmp_path: Path) -> None:
    real_target = tmp_path / "outside.jsonl"
    real_target.write_text(json.dumps(_row("session_start", "s1")) + "\n", encoding="utf-8")
    link_path = paths.events_dir() / "s1.jsonl"
    link_path.symlink_to(real_target)

    code = purge.main(["--session", "s1", "--yes"])

    assert code == 0
    assert real_target.exists()
