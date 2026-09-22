"""Tests for `verdict doctor` (task-6-brief.md, gate G1.6)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_verdict import _doctor_interpreter, doctor


@pytest.fixture
def cli_verdict_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VERDICT_HOME", str(home))
    monkeypatch.delenv("VERDICT_PYTHON", raising=False)
    return home


@pytest.fixture(autouse=True)
def _fake_plugin_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never depend on the `claude` binary being installed/fast in unit tests.
    monkeypatch.setattr(doctor, "check_plugin_registration", lambda: "unknown")


@pytest.fixture
def working_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor,
        "resolve_interpreter",
        lambda: doctor.InterpreterResolution(sys.executable, "test fixture", "3.99.0"),
    )


def _unreachable(host: str) -> str:
    raise OSError("no route to host")


def test_exits_zero_with_no_keys_and_unreachable_provider(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main([]) == 0

    report = doctor.build_report()
    assert report.keys == {
        "OPENROUTER_API_KEY": "absent",
        "TYPESAFE_API_KEY": "absent",
        "ANTHROPIC_API_KEY": "absent",
    }
    assert all(status.startswith("unreachable") for status in report.tls.values())


def test_exits_one_when_data_root_mode_is_0755(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_verdict_home.mkdir(parents=True)
    os.chmod(cli_verdict_home, 0o755)
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main([]) == 1


def test_exits_zero_when_data_root_does_not_exist(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main([]) == 0


def test_exits_one_when_no_interpreter_resolves(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        doctor,
        "resolve_interpreter",
        lambda: doctor.InterpreterResolution(None, "no interpreter found", None),
    )
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main([]) == 1


def test_audit_flips_to_one_when_an_exception_outcome_exists(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_verdict_home.mkdir(parents=True, mode=0o700)
    os.chmod(cli_verdict_home, 0o700)
    log_path = cli_verdict_home / "hook.log"
    log_path.write_text(
        json.dumps({"ts": time.time(), "outcome": "ok"})
        + "\n"
        + json.dumps({"ts": time.time(), "outcome": "exception"})
        + "\n"
    )
    os.chmod(log_path, 0o600)
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main([]) == 0
    assert doctor.main(["--audit"]) == 1


def test_audit_ignores_exceptions_outside_the_7_day_window(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli_verdict_home.mkdir(parents=True, mode=0o700)
    os.chmod(cli_verdict_home, 0o700)
    old_ts = time.time() - (8 * 24 * 60 * 60)
    log_path = cli_verdict_home / "hook.log"
    log_path.write_text(json.dumps({"ts": old_ts, "outcome": "exception"}) + "\n")
    os.chmod(log_path, 0o600)
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main(["--audit"]) == 0


def test_fix_interpreter_writes_an_absolute_existing_path(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_doctor_interpreter, "_probe_tls_handshake", lambda path, timeout=3.0: True)
    monkeypatch.setenv("VERDICT_PYTHON", sys.executable)

    path, message = doctor.fix_interpreter()

    assert path == sys.executable
    assert message
    written = (cli_verdict_home / "interpreter").read_text().strip()
    assert written == sys.executable
    assert Path(written).is_absolute()
    assert Path(written).exists()


def test_fix_interpreter_skips_a_candidate_that_fails_the_tls_handshake(
    cli_verdict_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _doctor_interpreter, "_probe_tls_handshake", lambda path, timeout=3.0: False
    )
    monkeypatch.setenv("VERDICT_PYTHON", sys.executable)

    path, message = doctor.fix_interpreter()

    assert path is None
    assert "no interpreter" in message.lower()
