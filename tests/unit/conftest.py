"""Shared fixtures for tests/unit (task-3-brief.md).

`isolated_verdict_home` points `VERDICT_HOME` at an empty temp directory so
`policy.load_policy()` can never pick up a real `~/.verdict/policy.json`
left on the machine running the tests, and clears `VERDICT_POLICY_DEFAULT`
so the packaged-default autodetection in `policy.py` is exercised rather
than a leftover test override from a previous run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from verdict_hot import policy as policy_mod


@pytest.fixture
def isolated_verdict_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VERDICT_HOME", str(home))
    monkeypatch.delenv("VERDICT_POLICY_DEFAULT", raising=False)
    return home


@pytest.fixture
def default_policy(isolated_verdict_home: Path) -> policy_mod.Policy:
    return policy_mod.load_policy()
