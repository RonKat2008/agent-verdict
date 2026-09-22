"""Interpreter resolution and `--fix-interpreter` probing for `verdict doctor`.

Split out of `doctor.py` to keep files under 400 lines (task-6-brief.md
controller notes). `resolve_interpreter` mirrors `plugin/hooks/run.sh`'s own
resolution order (`VERDICT_PYTHON`, then the `interpreter` file under
`verdict_home()`, then `python3` on PATH) so the report describes what the
hook launcher would actually pick. `fix_interpreter` probes a fixed
candidate list and keeps the first interpreter that both imports the
hot-path stdlib set quickly and can complete a real TLS handshake -- run
*inside the candidate interpreter itself*, since the TLS trap (E3) is a
per-interpreter CA store problem this process's own interpreter would not
reproduce.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_verdict.verdict_hot import paths

_IMPORT_CHECK_TIMEOUT = 1.0
_INTERPRETER_VERSION_TIMEOUT = 2.0
_TLS_PROBE_TIMEOUT = 3.0
_FILE_MODE = 0o600
_IMPORT_CHECK_CODE = "import json,sqlite3,ssl"
_TLS_HANDSHAKE_HOST = "openrouter.ai"

_TLS_HANDSHAKE_CODE = """
import socket, ssl, os, sys
candidates = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)
context = ssl.create_default_context()
if not context.cert_store_stats().get("x509_ca"):
    for candidate in candidates:
        if os.path.exists(candidate):
            context.load_verify_locations(cafile=candidate)
            break
host = sys.argv[1]
with socket.create_connection((host, 443), timeout=float(sys.argv[2])) as sock:
    with context.wrap_socket(sock, server_hostname=host):
        pass
"""


def _is_executable(path: str | None) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)  # type: ignore[arg-type]


@dataclass(frozen=True)
class InterpreterResolution:
    path: str | None
    step: str
    version: str | None


def _read_interpreter_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def _probe_version(python_path: str) -> str | None:
    try:
        result = subprocess.run(
            [python_path, "-S", "-c", "import sys; print(sys.version.split()[0])"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_INTERPRETER_VERSION_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", "replace").strip()
    return text or None


def resolve_interpreter() -> InterpreterResolution:
    verdict_python = os.environ.get("VERDICT_PYTHON")
    if _is_executable(verdict_python):
        assert verdict_python is not None
        return InterpreterResolution(
            verdict_python, "VERDICT_PYTHON environment variable", _probe_version(verdict_python)
        )

    interpreter_file = paths.verdict_home() / "interpreter"
    candidate = _read_interpreter_file(interpreter_file)
    if candidate and os.path.isabs(candidate) and _is_executable(candidate):
        return InterpreterResolution(
            candidate, f"interpreter file ({interpreter_file})", _probe_version(candidate)
        )

    path_python = shutil.which("python3")
    if path_python:
        return InterpreterResolution(path_python, "python3 on PATH", _probe_version(path_python))

    return InterpreterResolution(None, "no interpreter found", None)


def _probe_import_check(python_path: str) -> bool:
    try:
        result = subprocess.run(
            [python_path, "-S", "-c", _IMPORT_CHECK_CODE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_IMPORT_CHECK_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _probe_tls_handshake(python_path: str, timeout: float = _TLS_PROBE_TIMEOUT) -> bool:
    try:
        result = subprocess.run(
            [python_path, "-S", "-c", _TLS_HANDSHAKE_CODE, _TLS_HANDSHAKE_HOST, str(timeout)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _fix_candidates() -> list[str]:
    candidates: list[str] = []
    env_py = os.environ.get("VERDICT_PYTHON")
    if env_py:
        candidates.append(env_py)
    path_py = shutil.which("python3")
    if path_py and path_py not in candidates:
        candidates.append(path_py)
    if "/usr/bin/python3" not in candidates:
        candidates.append("/usr/bin/python3")
    return candidates


def _write_interpreter_file(python_path: str) -> Path:
    home = paths.ensure_private_dir(paths.verdict_home(), allow_symlink=True)
    target = home / "interpreter"
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        os.fchmod(fd, _FILE_MODE)
        os.write(fd, (python_path + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return target


def fix_interpreter() -> tuple[str | None, str]:
    """Probe VERDICT_PYTHON, PATH python3, /usr/bin/python3; keep the first
    that imports the hot-path stdlib set within 1s and completes a TLS
    handshake. Writes the winner's absolute path to
    `verdict_home()/interpreter`.
    """
    for candidate in _fix_candidates():
        if not _is_executable(candidate):
            continue
        if not _probe_import_check(candidate):
            continue
        if not _probe_tls_handshake(candidate):
            continue
        target = _write_interpreter_file(candidate)
        return candidate, f"selected {candidate}; wrote {target}"
    return None, "no interpreter passed the import check and TLS handshake"


__all__ = ["InterpreterResolution", "resolve_interpreter", "fix_interpreter"]
