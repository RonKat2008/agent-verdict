"""Data root and path helpers (global-constraints.md).

`verdict_home()` is the single place `VERDICT_HOME` / `~/.verdict` is resolved;
no other module in this package expands `~/.verdict` directly. Directories are
created 0700 and re-asserted with an explicit chmod so the mode holds
regardless of the process umask.

Symlink policy: VERDICT_HOME itself may legitimately be a symlink (a user
pointing their data dir at another volume), so `hook_log()` passes
`allow_symlink=True` for the root only. Everything created *beneath* the root
(events/, pending/, and the session/log files inside them) refuses to be a
symlink, since that is the boundary where another process could otherwise
redirect a write outside directories the collector controls.
"""

from __future__ import annotations

import os
from pathlib import Path

_DIR_MODE = 0o700
_SESSION_ID_BAD_SUBSTRINGS = ("/", "\\", "..", "\x00", "\n", "\r")
_SESSION_ID_MAX_LEN = 128


def verdict_home() -> Path:
    override = os.environ.get("VERDICT_HOME")
    if override:
        return Path(override)
    return Path.home() / ".verdict"


def ensure_private_dir(path: Path, *, allow_symlink: bool = False) -> Path:
    """Create `path` (and parents) and force mode 0700, regardless of umask.

    Refuses a symlinked `path` unless `allow_symlink=True` (see module
    docstring): a hot-path collector should never silently follow a symlink
    planted by another process into a directory it does not control.
    """
    if not allow_symlink and path.is_symlink():
        raise ValueError(f"refusing to use a symlinked directory: {path}")
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
    if len(session_id) > _SESSION_ID_MAX_LEN:
        raise ValueError(
            f"session_id must be at most {_SESSION_ID_MAX_LEN} characters: {session_id!r}"
        )
    if session_id.startswith("."):
        raise ValueError(f"session_id must not start with '.': {session_id!r}")
    for bad in _SESSION_ID_BAD_SUBSTRINGS:
        if bad in session_id:
            raise ValueError(f"session_id must not contain {bad!r}: {session_id!r}")
    return events_dir() / f"{session_id}.jsonl"


def hook_log() -> Path:
    ensure_private_dir(verdict_home(), allow_symlink=True)
    return verdict_home() / "hook.log"
