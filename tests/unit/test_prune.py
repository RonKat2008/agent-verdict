"""Tests for verdict_hot.prune (task-6-brief.md controller notes, ruling 2)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from verdict_hot import paths, prune
from verdict_hot import policy as policy_mod

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    return tmp_path


def _policy_with_retention(days: int) -> policy_mod.Policy:
    base = policy_mod.load_policy(PACKAGED_DEFAULT)
    return base._replace(store=base.store._replace(retention_days=days))


def _write_session(
    home: Path, session_id: str, rows: list[dict[str, object]], age_days: float
) -> Path:
    target = paths.session_file(session_id)
    with open(target, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    old_time = time.time() - age_days * 86400
    os.utime(target, (old_time, old_time))
    return target


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


def test_does_nothing_when_retention_days_is_zero_or_missing(home: Path) -> None:
    policy = _policy_with_retention(0)
    target = _write_session(home, "s1", [_row("session_start", "s1")], age_days=1000)

    result = prune.prune(policy)

    assert result.deleted == 0
    assert target.exists()


def test_removes_an_old_stopless_session(home: Path) -> None:
    policy = _policy_with_retention(45)
    target = _write_session(home, "s1", [_row("session_start", "s1")], age_days=100)

    result = prune.prune(policy)

    assert result.deleted == 1
    assert not target.exists()


def test_skips_a_session_that_carries_a_stop_row(home: Path) -> None:
    policy = _policy_with_retention(45)
    target = _write_session(
        home, "s1", [_row("session_start", "s1"), _row("stop", "s1")], age_days=100
    )

    result = prune.prune(policy)

    assert result.deleted == 0
    assert target.exists()


def test_skips_a_labeled_session(home: Path) -> None:
    policy = _policy_with_retention(45)
    target = _write_session(home, "s1", [_row("session_start", "s1")], age_days=100)
    labels_path = home / "labels.jsonl"
    labels_path.write_text(
        json.dumps({"event_ref": {"session_id": "s1"}, "label": "ok"}) + "\n", encoding="utf-8"
    )

    result = prune.prune(policy)

    assert result.deleted == 0
    assert target.exists()


def test_skips_a_recent_session(home: Path) -> None:
    policy = _policy_with_retention(45)
    target = _write_session(home, "s1", [_row("session_start", "s1")], age_days=1)

    result = prune.prune(policy)

    assert result.deleted == 0
    assert target.exists()


def test_respects_the_20_file_delete_bound(home: Path) -> None:
    policy = _policy_with_retention(1)
    for i in range(30):
        _write_session(home, f"s{i}", [_row("session_start", f"s{i}")], age_days=100)

    result = prune.prune(policy)

    assert result.deleted == 20
    remaining = list((home / "events").glob("*.jsonl"))
    assert len(remaining) == 10


def test_respects_the_200_examined_bound(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune, "_MAX_DELETES", 10_000)
    policy = _policy_with_retention(1)
    for i in range(250):
        _write_session(home, f"s{i}", [_row("session_start", f"s{i}")], age_days=100)

    result = prune.prune(policy)

    assert result.examined == 200


def test_never_raises_on_a_corrupt_labels_file(home: Path) -> None:
    policy = _policy_with_retention(45)
    target = _write_session(home, "s1", [_row("session_start", "s1")], age_days=100)
    (home / "labels.jsonl").write_bytes(b"not json at all\n{also not json")

    result = prune.prune(policy)

    assert result.deleted == 1
    assert not target.exists()


def test_never_follows_a_symlinked_session_file(home: Path, tmp_path: Path) -> None:
    policy = _policy_with_retention(45)
    real_target = tmp_path / "outside.jsonl"
    real_target.write_text(json.dumps(_row("session_start", "s1")) + "\n", encoding="utf-8")
    old_time = time.time() - 100 * 86400
    os.utime(str(real_target), (old_time, old_time))
    link_path = paths.events_dir() / "s1.jsonl"
    link_path.symlink_to(real_target)

    result = prune.prune(policy)

    assert result.deleted == 0
    assert real_target.exists()
