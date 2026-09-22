"""`verdict doctor`: local environment diagnostics (task-6-brief.md, gate G1.6).

Sections: interpreter resolution (mirrors `plugin/hooks/run.sh`'s own
resolution order), data-root existence and file/directory modes, plugin
registration (best-effort, via `claude plugin list`), `hook.log` outcome
counts for the last 7 days, a per-provider TLS reachability probe built on
`sslctx.build_context()`, and API key presence.

Only interpreter resolution and data-root modes affect the exit code
(task-6-brief.md: "Exit code reflects only interpreter resolution and
data-root modes"). The TLS probe and key presence are informational only,
so `doctor` exits 0 with zero keys configured and every provider
unreachable -- the collector must be diagnosable on a machine that has
never talked to a provider at all.

Interpreter resolution/fix-interpreter probing lives in
`_doctor_interpreter.py`; the remaining checks live in `_doctor_checks.py`
(both split out to keep every file under 400 lines). This module re-exports
their public names so callers and tests use one surface (`doctor.<name>`),
and so monkeypatching `doctor.probe_tls` or `doctor.resolve_interpreter`
affects what `build_report`/`fix_interpreter` actually call.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

from agent_verdict import _doctor_checks as checks
from agent_verdict._doctor_checks import (
    _PROBE_HOSTS,
    DataRootCheck,
    check_data_root,
    check_plugin_registration,
    hook_log_outcomes,
    key_presence,
    probe_tls,
)
from agent_verdict._doctor_interpreter import (
    InterpreterResolution,
    fix_interpreter,
    resolve_interpreter,
)

_TLS_PROBE_TIMEOUT = checks._TLS_PROBE_TIMEOUT


def _safe_probe_tls(host: str) -> str:
    try:
        return probe_tls(host)
    except Exception as exc:  # noqa: BLE001 - informational probe, never affects exit code
        return f"unreachable ({type(exc).__name__})"


@dataclass(frozen=True)
class DoctorReport:
    interpreter: InterpreterResolution
    data_root: DataRootCheck
    plugin_registration: str
    hook_log_outcomes: dict[str, int]
    tls: dict[str, str]
    keys: dict[str, str]

    @property
    def exception_in_window(self) -> bool:
        return self.hook_log_outcomes.get("exception", 0) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "interpreter": {
                "path": self.interpreter.path,
                "step": self.interpreter.step,
                "version": self.interpreter.version,
            },
            "data_root": {
                "home": self.data_root.home,
                "exists": self.data_root.exists,
                "violations": list(self.data_root.violations),
            },
            "plugin_registration": self.plugin_registration,
            "hook_log_outcomes": dict(self.hook_log_outcomes),
            "tls": dict(self.tls),
            "keys": dict(self.keys),
        }


def build_report() -> DoctorReport:
    interpreter = resolve_interpreter()
    data_root = check_data_root()
    plugin_registration = check_plugin_registration()
    outcomes = hook_log_outcomes()
    tls = {host: _safe_probe_tls(host) for host in _PROBE_HOSTS}
    keys = key_presence()
    return DoctorReport(interpreter, data_root, plugin_registration, outcomes, tls, keys)


def exit_code_for(report: DoctorReport, audit: bool) -> int:
    if report.interpreter.path is None:
        return 1
    if report.data_root.violations:
        return 1
    if audit and report.exception_in_window:
        return 1
    return 0


def _print_report(report: DoctorReport, fix_result: tuple[str | None, str] | None) -> None:
    if fix_result is not None:
        path, message = fix_result
        status = "ok" if path else "failed"
        print(f"fix-interpreter: {status}: {message}")
    interpreter = report.interpreter
    print(
        f"interpreter: {interpreter.path} (via {interpreter.step}, version {interpreter.version})"
    )
    data_root = report.data_root
    print(f"data root: {data_root.home} (exists: {data_root.exists})")
    for violation in data_root.violations:
        print(f"  MODE VIOLATION: {violation}")
    print(f"plugin registration: {report.plugin_registration}")
    print(f"hook.log outcomes (7d): {report.hook_log_outcomes}")
    for host, status in report.tls.items():
        print(f"tls {host}: {status} (informational)")
    for name, status in report.keys.items():
        print(f"key {name}: {status} (informational)")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verdict doctor", description="Diagnose the local agent-verdict environment"
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="exit 1 if hook.log recorded an exception outcome in the last 7 days",
    )
    parser.add_argument(
        "--fix-interpreter",
        action="store_true",
        help="probe for a working interpreter and persist it to verdict_home()/interpreter",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    fix_result = fix_interpreter() if args.fix_interpreter else None
    report = build_report()
    exit_code = exit_code_for(report, args.audit)
    if args.json:
        payload = report.to_dict()
        if fix_result is not None:
            payload["fix_interpreter"] = {"path": fix_result[0], "message": fix_result[1]}
        print(json.dumps(payload))
    else:
        _print_report(report, fix_result)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DoctorReport",
    "InterpreterResolution",
    "DataRootCheck",
    "build_report",
    "check_data_root",
    "check_plugin_registration",
    "exit_code_for",
    "fix_interpreter",
    "hook_log_outcomes",
    "key_presence",
    "probe_tls",
    "resolve_interpreter",
    "main",
]
