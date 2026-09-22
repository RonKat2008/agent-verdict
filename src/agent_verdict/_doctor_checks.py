"""Non-interpreter checks for `verdict doctor`: data root, plugin, log, TLS, keys.

Split out of `doctor.py` to keep files under 400 lines (task-6-brief.md
controller notes). Only `check_data_root` feeds the exit code (via
`doctor.exit_code_for`); everything else here is informational.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agent_verdict.verdict_hot import paths, sslctx
from agent_verdict.verdict_hot.breaker import Breaker

_KEY_ENV_NAMES = (
    "CLAUDE_PLUGIN_OPTION_API_KEY",
    "OPENROUTER_API_KEY",
    "TYPESAFE_API_KEY",
    "ANTHROPIC_API_KEY",
)
_PROBE_HOSTS = ("openrouter.ai", "api.typesafe.ai")
_TLS_PROBE_TIMEOUT = 3.0
_PLUGIN_LIST_TIMEOUT = 10.0
_AUDIT_WINDOW_SECONDS = 7 * 24 * 60 * 60
_DIR_MODE = 0o700
_FILE_MODE = 0o600


@dataclass(frozen=True)
class DataRootCheck:
    home: str
    exists: bool
    violations: tuple[str, ...]


def _mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _check_mode(path: Path, expected: int, violations: list[str]) -> None:
    actual = _mode_of(path)
    if actual != expected:
        violations.append(f"{path}: mode {oct(actual)} (expected {oct(expected)})")


def check_data_root() -> DataRootCheck:
    home = paths.verdict_home()
    if not home.exists():
        return DataRootCheck(str(home), False, ())

    violations: list[str] = []
    _check_mode(home, _DIR_MODE, violations)

    for sub in ("events", "pending"):
        subdir = home / sub
        if subdir.is_dir():
            _check_mode(subdir, _DIR_MODE, violations)

    for name in ("hook.log", "hook.log.1", "interpreter"):
        candidate = home / name
        if candidate.is_file():
            _check_mode(candidate, _FILE_MODE, violations)

    events_dir = home / "events"
    if events_dir.is_dir():
        for session_file in events_dir.glob("*.jsonl"):
            _check_mode(session_file, _FILE_MODE, violations)

    return DataRootCheck(str(home), True, tuple(violations))


def check_plugin_registration() -> str:
    claude_path = shutil.which("claude")
    if not claude_path:
        return "unknown"
    try:
        result = subprocess.run(
            [claude_path, "plugin", "list"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PLUGIN_LIST_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    output = result.stdout.decode("utf-8", "replace")
    return "registered" if "agent-verdict" in output else "not registered"


def hook_log_outcomes(now: float | None = None) -> dict[str, int]:
    """Outcome counts from `hook.log` rows timestamped in the last 7 days."""
    cutoff = (time.time() if now is None else now) - _AUDIT_WINDOW_SECONDS
    counts: dict[str, int] = {}
    log_path = paths.verdict_home() / "hook.log"
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return counts
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        ts = row.get("ts")
        if not isinstance(ts, (int, float)) or ts < cutoff:
            continue
        outcome = row.get("outcome")
        if isinstance(outcome, str):
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def probe_tls(host: str, timeout: float = _TLS_PROBE_TIMEOUT) -> str:
    """Raises on failure; callers wrap this defensively (informational only)."""
    context = sslctx.build_context()
    with (
        socket.create_connection((host, 443), timeout=timeout) as sock,
        context.wrap_socket(sock, server_hostname=host),
    ):
        return "reachable"


def key_presence() -> dict[str, str]:
    return {name: ("present" if os.environ.get(name) else "absent") for name in _KEY_ENV_NAMES}


def breaker_open(now: float | None = None) -> bool:
    """Whether the provider circuit breaker (`~/.verdict/breaker.json`) is
    currently open. Informational only, like the TLS probe and key
    presence above -- never affects `doctor`'s exit code."""
    return Breaker().is_open(now if now is not None else time.time())


__all__ = [
    "DataRootCheck",
    "check_data_root",
    "check_plugin_registration",
    "hook_log_outcomes",
    "probe_tls",
    "key_presence",
    "breaker_open",
    "_PROBE_HOSTS",
    "_KEY_ENV_NAMES",
]
