from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from verdict_hot import ledger, paths


def test_verdict_home_honors_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    assert paths.verdict_home() == tmp_path


def test_verdict_home_defaults_to_dot_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERDICT_HOME", raising=False)
    assert paths.verdict_home() == Path.home() / ".verdict"


def test_events_dir_created_0700_under_permissive_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path / "home"))
    old_umask = os.umask(0o022)
    try:
        events = paths.events_dir()
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(events.stat().st_mode) == 0o700


def test_pending_dir_created_0700_under_permissive_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path / "home"))
    old_umask = os.umask(0o022)
    try:
        pending = paths.pending_dir()
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(pending.stat().st_mode) == 0o700


def test_appended_file_is_0600_under_permissive_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path / "home"))
    old_umask = os.umask(0o022)
    try:
        target = ledger.append_row({"session_id": "s1", "event": "session_end", "schema_v": 1})
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("bad_id", ["../x", "", "a/b", "a\\b", "a\x00b", ".."])
def test_session_file_rejects_bad_ids(
    bad_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        paths.session_file(bad_id)


def test_session_file_accepts_a_plain_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    target = paths.session_file("abc-123")
    assert target.name == "abc-123.jsonl"


def test_hook_log_path_is_under_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    assert paths.hook_log() == tmp_path / "hook.log"
