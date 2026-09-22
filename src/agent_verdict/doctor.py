"""`verdict doctor`: local environment diagnostics (task-6-brief.md, gate G1.6).

Sections: interpreter resolution (mirrors `plugin/hooks/run.sh`'s own
resolution order), data-root existence and file/directory modes, plugin
registration (best-effort, via `claude plugin list`), `hook.log` outcome
counts for the last 7 days, a per-provider TLS reachability probe built on
`sslctx.build_context()`, and API key presence.

The TLS probe is the only thing in this package that opens a socket, and
it exposes this machine's IP to each provider host, so `--no-probe` turns
it off and `verdict doctor --no-probe` makes no network call at all (final
review, I4). Probing stays on by default: reachability is the reason the
section exists.

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
import os
import sys
from dataclasses import dataclass
from typing import Any

from agent_verdict import _doctor_checks as checks
from agent_verdict._doctor_checks import (
    _PROBE_HOSTS,
    DataRootCheck,
    breaker_open,
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
    breaker_open: bool

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
            "breaker_open": self.breaker_open,
        }


_NOT_PROBED = "not probed (--no-probe)"


def build_report(probe: bool = True) -> DoctorReport:
    interpreter = resolve_interpreter()
    data_root = check_data_root()
    plugin_registration = check_plugin_registration()
    outcomes = hook_log_outcomes()
    tls = {host: _safe_probe_tls(host) if probe else _NOT_PROBED for host in _PROBE_HOSTS}
    keys = key_presence()
    return DoctorReport(
        interpreter, data_root, plugin_registration, outcomes, tls, keys, breaker_open()
    )


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
    print(f"breaker open: {report.breaker_open} (informational)")


def _run_live_smoke() -> str:
    """Send one tiny real request to the configured provider and summarize
    the result. Never exercised by a test (hazards note, task-6-brief.md):
    it is the one code path in this package that makes a real network
    call. Imports are lazy so no test that never passes `--live-smoke`
    pays for, or accidentally triggers, this import."""
    from agent_verdict.verdict_hot import policy as policy_mod
    from agent_verdict.verdict_hot import provider as provider_mod

    active_policy = policy_mod.load_policy()
    preset = provider_mod.PRESETS.get(
        active_policy.provider.default, provider_mod.PRESETS["openrouter"]
    )
    api_key = os.environ.get("CLAUDE_PLUGIN_OPTION_API_KEY") or os.environ.get(preset.key_env)
    if not api_key:
        return "error: no_key"

    minimal_state = {
        "trusted_facts": {"steps": [], "unresolved_failures": []},
        "untrusted": {"claims": {}, "step_output_excerpts": {}, "final_message": ""},
    }
    minimal_questions = {"claims_done": {"type": "noul"}, "claims_check_passed": {"type": "noul"}}
    try:
        result = provider_mod.evaluate(
            minimal_state, minimal_questions, preset, api_key, active_policy.provider.deadline_s
        )
    except Exception as exc:  # noqa: BLE001 - summarize any failure, never crash doctor
        return f"error: {type(exc).__name__}"
    if not result.ok:
        return f"error: {result.error}"
    return f"ok {result.model_returned} {result.conn_ms:.0f} {result.infer_ms:.0f}"


def build_arg_parser(add_help: bool = True) -> argparse.ArgumentParser:
    """`add_help=False` makes this usable as an argparse `parents=` entry,
    so `cli.py` reuses these flags instead of redeclaring them."""
    parser = argparse.ArgumentParser(
        prog="verdict doctor",
        description="Diagnose the local agent-verdict environment",
        add_help=add_help,
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
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="skip the provider TLS reachability check (makes doctor network-free)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help="send one real, tiny request to the configured provider (makes a network call)",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Run `doctor` from an already-parsed namespace.

    `cli.py` builds its `doctor` subparser from `build_arg_parser` and calls
    this, so every flag is declared exactly once (final review, minor).
    """
    fix_result = fix_interpreter() if args.fix_interpreter else None
    report = build_report(probe=not args.no_probe)
    exit_code = exit_code_for(report, args.audit)
    live_smoke_result = _run_live_smoke() if getattr(args, "live_smoke", False) else None
    if args.json:
        payload = report.to_dict()
        if fix_result is not None:
            payload["fix_interpreter"] = {"path": fix_result[0], "message": fix_result[1]}
        if live_smoke_result is not None:
            payload["live_smoke"] = live_smoke_result
        print(json.dumps(payload))
    else:
        _print_report(report, fix_result)
        if live_smoke_result is not None:
            print(f"live-smoke: {live_smoke_result}")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


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
    "build_arg_parser",
    "run",
    "main",
]
