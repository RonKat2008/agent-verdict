"""Data root and path helpers (global-constraints.md).

`verdict_home()` is the single place `VERDICT_HOME` / `~/.verdict` is resolved;
no other module in this package expands `~/.verdict` directly. Directories are
created 0700 and re-asserted with an explicit chmod so the mode holds
regardless of the process umask.
"""

from __future__ import annotations

import os
from pathlib import Path

_DIR_MODE = 0o700
_SESSION_ID_BAD_SUBSTRINGS = ("/", "\\", "..", "\x00")


def verdict_home() -> Path:
    override = os.environ.get("VERDICT_HOME")
    if override:
        return Path(override)
    return Path.home() / ".verdict"


def ensure_private_dir(path: Path) -> Path:
    """Create `path` (and parents) and force mode 0700, regardless of umask."""
    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    os.chmod(path, _DIR_MODE)
    return path


def events_dir() -> Path:
    return ensure_private_dir(verdict_home() / "events")


def pending_dir() -> Path:
    return ensure_private_dir(verdict_home() / "pending")


def session_file(session_id: str) -> Path:
    if not session_id:
        raise ValueError("session_id must not be empty")
    for bad in _SESSION_ID_BAD_SUBSTRINGS:
        if bad in session_id:
            raise ValueError(f"session_id must not contain {bad!r}: {session_id!r}")
    return events_dir() / f"{session_id}.jsonl"


def hook_log() -> Path:
    ensure_private_dir(verdict_home())
    return verdict_home() / "hook.log"
