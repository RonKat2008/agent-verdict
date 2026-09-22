"""Tests for verdict_hook.main/_handle/_run_stop (task-5-brief.md;
task-4-brief.md fix round 1 item 1).

`verdict_hook.py` is a plain top-level script under `plugin/hooks/` (on
`pythonpath` via `pyproject.toml`), importable directly as a module here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import verdict_hook
from verdict_hot import stop as stop_mod

_STOP_PAYLOAD: dict[str, Any] = {
    "session_id": "s1",
    "prompt_id": "p1",
    "agent_id": None,
    "hook_event_name": "Stop",
    "cwd": "/tmp",
    "permission_mode": "default",
    "background_tasks": [],
    "stop_hook_active": False,
    "last_assistant_message": "done",
}


def test_main_exits_0_and_prints_nothing_when_stop_handle_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Fix round 1 item 1 (Critical): an exception from anywhere in the
    Stop branch -- here simulated at `stop.handle` itself -- must not
    propagate out of `main` and exit the process non-zero."""
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    monkeypatch.setattr(verdict_hook, "_read_stdin_json", lambda cap: dict(_STOP_PAYLOAD))

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(stop_mod, "handle", _boom)

    rc = verdict_hook.main(["stop"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""


def test_main_exits_0_when_the_fallback_policy_load_itself_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A `PolicyError` from EITHER `load_policy()` call inside `_run_stop`
    (the primary, or its own fallback) must degrade to `exception`, not
    crash the process."""
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    monkeypatch.setattr(verdict_hook, "_read_stdin_json", lambda cap: dict(_STOP_PAYLOAD))

    from verdict_hot import policy as policy_mod

    def _always_broken(path: object = None) -> object:
        raise policy_mod.PolicyError("simulated: even the fallback is broken")

    monkeypatch.setattr(policy_mod, "load_policy", _always_broken)

    rc = verdict_hook.main(["stop"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""
