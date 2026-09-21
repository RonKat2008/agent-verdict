"""Safe hook logging: scrub(), log_invocation(), rotation, excepthook.

`hook.log` never holds tool content or key material (global-constraints.md).
Every string value passes `scrub()` *before* the row is serialized to JSON
(never on the already-serialized line: a secret value containing a quote or
brace would otherwise corrupt the line once redaction rewrites it in place).
`log_invocation` never raises: a recorder crash must still exit 0 with an
empty stdout (fail-open model).

Rotation is guarded by an flock on a dedicated `hook.log.lock` file so two
hook processes racing to rotate `hook.log` at the same size threshold cannot
interleave or clobber `hook.log.1`.
"""

from __future__ import annotations

import contextlib
import fcntl
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
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

# Test-only escape hatch: multiprocess rotation tests need to shrink the
# rotation threshold in a *spawned child process*, where monkeypatching the
# `_MAX_LOG_BYTES` module attribute in the parent has no effect. Not part of
# the public interface.
_TEST_MAX_LOG_BYTES_ENV = "_VERDICT_TEST_MAX_LOG_BYTES"

# Bounded to the scheme token plus one credential token after "Authorization:"
# (e.g. "Bearer <token>", or a single bare token) so a header embedded inside
# a longer string only loses those one or two words, never the rest of the
# text after it.
_AUTH_HEADER_RE = re.compile(r"(Authorization:\s*)\S+(?:\s+\S+)?", re.IGNORECASE)
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


def _scrub_value(value: object) -> object:
    """Recursively scrub string leaves *and* dict keys. Returns new objects,
    never mutates the input (dicts/lists are rebuilt, not edited in place).
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return _scrub_dict(value)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    return value


def _scrub_dict(value: dict[object, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, val in value.items():
        scrubbed_key = _dedupe_key(result, scrub(str(key)))
        result[scrubbed_key] = _scrub_value(val)
    return result


def _dedupe_key(existing: dict[str, object], key: str) -> str:
    """Two distinct original keys can scrub to the same string (for example
    two different secret values that each collapse to "<redacted>"). Keep
    both rather than letting the second overwrite the first.
    """
    if key not in existing:
        return key
    suffix = 2
    candidate = f"{key}#{suffix}"
    while candidate in existing:
        suffix += 1
        candidate = f"{key}#{suffix}"
    return candidate


def _max_log_bytes() -> int:
    override = os.environ.get(_TEST_MAX_LOG_BYTES_ENV)
    if override:
        with contextlib.suppress(ValueError):
            return int(override)
    return _MAX_LOG_BYTES


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

    # Scrub the row's own Python values before serializing. Scrubbing the
    # already-serialized JSON text would let a secret's replacement text
    # (or a bounded-but-imperfect match) land across a closing quote or
    # brace and corrupt the line; scrubbing first means json.dumps always
    # serializes already-safe strings.
    scrubbed_row = _scrub_value(row)
    line = json.dumps(scrubbed_row, separators=(",", ":"), ensure_ascii=False) + "\n"

    log_path = paths.hook_log()
    _rotate_and_append(log_path, line.encode("utf-8"))


def _lock_path(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".lock")


def _rotate_and_append(log_path: Path, data: bytes) -> None:
    lock_fd = os.open(str(_lock_path(log_path)), os.O_WRONLY | os.O_CREAT | _NOFOLLOW, _FILE_MODE)
    try:
        os.fchmod(lock_fd, _FILE_MODE)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            _rotate_if_over_limit(log_path)
            _append_line(log_path, data)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _rotate_if_over_limit(log_path: Path) -> None:
    try:
        size = log_path.stat().st_size
    except OSError:
        return
    if size <= _max_log_bytes():
        return
    rotated = log_path.with_name(log_path.name + ".1")
    os.replace(log_path, rotated)


def _append_line(log_path: Path, data: bytes) -> None:
    fd = os.open(str(log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW, _FILE_MODE)
    try:
        os.fchmod(fd, _FILE_MODE)
        os.write(fd, data)
    finally:
        os.close(fd)


def install_excepthook() -> None:
    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: types.TracebackType | None,
    ) -> None:
        del exc_value, exc_tb  # never logged: could carry sensitive text
        log_invocation("excepthook", None, "exception", 0.0, err_class=exc_type.__name__)

    sys.excepthook = _hook
