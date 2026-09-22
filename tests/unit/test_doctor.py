"""Tests for `verdict doctor` (task-6-brief.md, gate G1.6)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_verdict import _doctor_checks, _doctor_interpreter, doctor


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
    for name in (
        "CLAUDE_PLUGIN_OPTION_API_KEY",
        "OPENROUTER_API_KEY",
        "TYPESAFE_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main([]) == 0

    report = doctor.build_report()
    assert report.keys == {
        "CLAUDE_PLUGIN_OPTION_API_KEY": "absent",
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


# --- Final review I4: `--no-probe` makes doctor fully network-free ---------
#
# README and docs/CONSENT.md claimed "no network calls at all", but
# `verdict doctor` opens a TLS connection to each provider host, which
# exposes the machine's IP to them. The probe stays on by default (it is
# the point of the reachability line) and `--no-probe` turns it off.


def test_no_probe_opens_no_connection(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    def _record(host: str) -> str:
        calls.append(host)
        return "reachable"

    monkeypatch.setattr(doctor, "probe_tls", _record)

    assert doctor.main(["--no-probe"]) == 0

    assert calls == []
    out = capsys.readouterr().out
    assert "not probed" in out
    for host in _doctor_checks._PROBE_HOSTS:
        assert f"tls {host}: not probed" in out


def test_probing_is_on_by_default(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _record(host: str) -> str:
        calls.append(host)
        return "reachable"

    monkeypatch.setattr(doctor, "probe_tls", _record)

    assert doctor.main([]) == 0

    assert calls == list(_doctor_checks._PROBE_HOSTS)


def test_no_probe_reports_every_host_as_not_probed_in_json(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main(["--no-probe", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["tls"] == {
        host: "not probed (--no-probe)" for host in _doctor_checks._PROBE_HOSTS
    }


def test_breaker_state_is_reported_and_never_affects_exit_code(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_verdict.verdict_hot.breaker import Breaker

    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    report_closed = doctor.build_report()
    assert report_closed.breaker_open is False

    monkeypatch.setattr(time, "time", lambda: 1000.0)
    Breaker().record(429, 1000.0)
    Breaker().record(429, 1000.0)
    Breaker().record(429, 1000.0)

    assert doctor.main([]) == 0
    report_open = doctor.build_report()
    assert report_open.breaker_open is True


def test_live_smoke_flag_exists_and_is_off_by_default(
    cli_verdict_home: Path,
    working_interpreter: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def _boom(*args: object, **kwargs: object) -> object:
        calls.append(args)
        raise AssertionError("no transport should be built without --live-smoke")

    monkeypatch.setattr(doctor, "probe_tls", _unreachable)
    monkeypatch.setattr("agent_verdict.verdict_hot.provider.evaluate", _boom)

    assert doctor.main([]) == 0

    assert calls == []
    # The flag parses cleanly (even though it is never exercised by a real
    # network call in tests, per the hazards note).
    parser = doctor.build_arg_parser()
    args = parser.parse_args(["--live-smoke"])
    assert args.live_smoke is True


def test_no_probe_does_not_change_the_exit_code(
    cli_verdict_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit code still reflects only interpreter resolution and data-root
    modes (task-6-brief.md)."""
    monkeypatch.setattr(
        doctor, "resolve_interpreter", lambda: doctor.InterpreterResolution(None, "none", None)
    )
    monkeypatch.setattr(doctor, "probe_tls", _unreachable)

    assert doctor.main(["--no-probe"]) == 1
