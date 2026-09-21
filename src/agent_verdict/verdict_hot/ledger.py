"""Append-only JSONL ledger (global-constraints.md, task-1-brief.md).

One `os.write` per row on an `O_APPEND` descriptor held under `fcntl.flock`,
never holding the lock across anything slow. If a session file's first row
carries a `schema_v` newer than this code's `SCHEMA_V`, appends spool to
`pending_dir()` instead so an older collector never corrupts a newer format.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from . import SCHEMA_V, paths

_FILE_MODE = 0o600
_REQUIRED_KEYS = ("session_id", "event", "schema_v")


def append_row(row: Mapping[str, object]) -> Path:
    for key in _REQUIRED_KEYS:
        if key not in row:
            raise ValueError(f"row missing required key: {key!r}")

    session_id = str(row["session_id"])
    target = paths.session_file(session_id)
    if _needs_spool(target):
        target = paths.pending_dir() / f"{session_id}.jsonl"

    line = json.dumps(dict(row), separators=(",", ":"), ensure_ascii=False) + "\n"
    data = line.encode("utf-8")

    fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, _FILE_MODE)
    try:
        os.fchmod(fd, _FILE_MODE)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, data)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return target


def read_session(session_id: str) -> list[dict[str, object]]:
    target = paths.session_file(session_id)
    rows: list[dict[str, object]] = []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in _split_lines(text):
        parsed = _try_parse_object(line)
        if parsed is not None:
            rows.append(parsed)
    return rows


def iter_sessions() -> Iterator[Path]:
    yield from sorted(paths.events_dir().glob("*.jsonl"))


def _needs_spool(target: Path) -> bool:
    first_row = _first_row(target)
    if first_row is None:
        return False
    schema_v = first_row.get("schema_v")
    return isinstance(schema_v, int) and schema_v > SCHEMA_V


def _first_row(target: Path) -> dict[str, object] | None:
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in _split_lines(text):
        return _try_parse_object(line)
    return None


def _split_lines(text: str) -> Iterator[str]:
    """Split on the literal "\\n" rows are terminated with.

    `str.splitlines()` also breaks on other Unicode line separators (for
    example U+2028), which JSON permits unescaped inside string values, so
    using it here could shred a single valid row into unparseable pieces.
    """
    for raw_line in text.split("\n"):
        line = raw_line.strip("\r").strip()
        if line:
            yield line


def _try_parse_object(line: str) -> dict[str, object] | None:
    try:
        parsed: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
