"""Bounded SessionEnd-time ledger prune (task-6-brief.md controller notes,
ruling 2; D-021).

Called lazily from `verdict_hook.py`'s `SessionEnd` branch, after
`recorders.record`. Never raises: any exception aborts the run silently,
same fail-open discipline as the rest of the hot path. Bounded so a
misbehaving or huge data root can never turn a `SessionEnd` hook into a
slow one: at most `_MAX_STAT_CALLS` `stat` calls, at most `_MAX_SECONDS`
of wall time (`time.monotonic()`), and at most `_MAX_DELETES` files
removed, whichever limit is hit first.

A session is prunable when its file's mtime is older than
`policy.store.retention_days` days AND it is not protected. Protected
means either of:

- its session id appears in `labels.jsonl` (read at most the first 1 MiB,
  tolerating malformed lines), or
- it carries at least one `stop` ledger row -- an "unlabeled corpus
  session" during the study (D-021), which only `verdict purge` may ever
  remove.

In practice this makes the automatic prune remove only aborted or empty
sessions (no `stop` row was ever written for them); every session that
reached a real Stop event is left for `verdict purge` or the labeling
pipeline to deal with.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from . import paths

if TYPE_CHECKING:
    from .policy import Policy

_MAX_STAT_CALLS = 200
_MAX_SECONDS = 0.05
_MAX_DELETES = 20
_LABELS_READ_CAP = 1024 * 1024
_SESSION_READ_CAP = 1024 * 1024
_SECONDS_PER_DAY = 86400
_LABELS_FILENAME = "labels.jsonl"


class PruneResult(NamedTuple):
    deleted: int
    examined: int


def labeled_session_ids() -> frozenset[str]:
    import json

    path = paths.verdict_home() / _LABELS_FILENAME
    try:
        with open(path, "rb") as fh:
            data = fh.read(_LABELS_READ_CAP)
    except OSError:
        return frozenset()

    ids: set[str] = set()
    for raw_line in data.split(b"\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ref = row.get("event_ref")
        if isinstance(ref, dict):
            session_id = ref.get("session_id")
            if isinstance(session_id, str):
                ids.add(session_id)
    return frozenset(ids)


def has_stop_row(path: Path) -> bool:
    import json

    try:
        with open(path, "rb") as fh:
            data = fh.read(_SESSION_READ_CAP)
    except OSError:
        return False

    for raw_line in data.split(b"\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("event") == "stop":
            return True
    return False


def _elapsed_over_budget(start: float) -> bool:
    return (time.monotonic() - start) > _MAX_SECONDS


def _prune_unsafe(policy: Policy, now: float) -> PruneResult:
    retention_days = policy.store.retention_days
    if not retention_days:
        return PruneResult(0, 0)

    cutoff = now - retention_days * _SECONDS_PER_DAY
    start = time.monotonic()
    deleted = 0
    examined = 0
    labeled: frozenset[str] | None = None

    for session_path in sorted(paths.events_dir().glob("*.jsonl")):
        if examined >= _MAX_STAT_CALLS or deleted >= _MAX_DELETES:
            break
        if _elapsed_over_budget(start):
            break

        examined += 1
        try:
            mtime = session_path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue

        if labeled is None:
            labeled = labeled_session_ids()
        if session_path.stem in labeled:
            continue
        if has_stop_row(session_path):
            continue

        try:
            if session_path.is_symlink():
                continue
            session_path.unlink()
            deleted += 1
        except OSError:
            continue

    return PruneResult(deleted, examined)


def prune(policy: Policy, now: float | None = None) -> PruneResult:
    """Fail-open: any unexpected exception yields `PruneResult(0, 0)`
    rather than propagating into the `SessionEnd` hook's outcome."""
    try:
        return _prune_unsafe(policy, now if now is not None else time.time())
    except Exception:  # noqa: BLE001 - fail-open contract (global-constraints.md)
        return PruneResult(0, 0)


__all__ = ["PruneResult", "prune"]
