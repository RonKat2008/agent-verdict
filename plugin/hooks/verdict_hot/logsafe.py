"""Safe hook logging: scrub(), log_invocation(), rotation, excepthook.

`hook.log` never holds tool content or key material (global-constraints.md).
Every string written passes `scrub()`, and `log_invocation` never raises: a
recorder crash must still exit 0 with an empty stdout (D-... fail-open model).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
import types
from collections.abc import Mapping
from pathlib import Path

from . import SCHEMA_V, paths

_REDACTED = "<redacted>"
_ENV_SECRET_PREFIX = "CLAUDE_PLUGIN_OPTION_"
_ENV_SECRET_EXACT_NAMES = ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY")
_MIN_ENV_SECRET_LEN = 8
_MAX_LOG_BYTES = 10 * 1024 * 1024
_FILE_MODE = 0o600
_VALID_OUTCOMES = frozenset({"ok", "skipped", "exception", "disabled"})

_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*).*", re.IGNORECASE)
_SK_TOKEN_RE = re.compile(r"sk-[A-Za-z0-9_-]{16,}")


def _env_secret_values() -> list[str]:
    values = []
    for name, value in os.environ.items():
        is_secret_name = name.startswith(_ENV_SECRET_PREFIX) or name in _ENV_SECRET_EXACT_NAMES
        if is_secret_name and value and len(value) >= _MIN_ENV_SECRET_LEN:
            values.append(value)
    return values


def scrub(text: str) -> str:
    result = text
    for value in _env_secret_values():
        result = result.replace(value, _REDACTED)
    result = _AUTH_HEADER_RE.sub(lambda m: m.group(1) + _REDACTED, result)
    result = _SK_TOKEN_RE.sub(_REDACTED, result)
    return result


def log_invocation(
    event: str,
    session_id: str | None,
    outcome: str,
    total_ms: float,
    err_class: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    with contextlib.suppress(Exception):
        _log_invocation_unsafe(event, session_id, outcome, total_ms, err_class, extra)


def _log_invocation_unsafe(
    event: str,
    session_id: str | None,
    outcome: str,
    total_ms: float,
    err_class: str | None,
    extra: Mapping[str, object] | None,
) -> None:
    row: dict[str, object] = {
        "ts": time.time(),
        "schema_v": SCHEMA_V,
        "event": event,
        "session_id": session_id,
        "outcome": outcome if outcome in _VALID_OUTCOMES else "exception",
        "err_class": err_class,
        "total_ms": total_ms,
    }
    if extra:
        row.update(extra)

    line = scrub(json.dumps(row, separators=(",", ":"), ensure_ascii=False)) + "\n"
    log_path = paths.hook_log()
    _rotate_if_needed(log_path)

    fd = os.open(str(log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, _FILE_MODE)
    try:
        os.fchmod(fd, _FILE_MODE)
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _rotate_if_needed(log_path: Path) -> None:
    try:
        size = log_path.stat().st_size
    except OSError:
        return
    if size <= _MAX_LOG_BYTES:
        return
    rotated = log_path.with_name(log_path.name + ".1")
    os.replace(log_path, rotated)


def install_excepthook() -> None:
    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: types.TracebackType | None,
    ) -> None:
        del exc_value, exc_tb  # never logged: could carry sensitive text
        log_invocation("excepthook", None, "exception", 0.0, err_class=exc_type.__name__)

    sys.excepthook = _hook
