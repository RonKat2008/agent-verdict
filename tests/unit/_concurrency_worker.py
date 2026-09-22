"""Top-level worker for the multiprocessing concurrency test (test_ledger.py).

Lives in its own module so `multiprocessing`'s spawn context can import it by
name in the child process.

Each row carries a filler payload well over 5 KiB (final review, minor): a
short row fits in a single atomic pipe/file write on every platform, so the
test could pass with the `flock` removed and would prove nothing. Above
PIPE_BUF (512 bytes on macOS, 4 KiB on Linux) a write can be split, and an
unlocked appender interleaves visibly.
"""

from __future__ import annotations

import os

ROW_FILLER_CHARS = 5 * 1024 + 256
"""Filler length per row: over 5 KiB, so one row cannot be written atomically."""


def row_filler(pid_marker: int) -> str:
    """A per-process filler string: any interleaving shows up as a row whose
    filler is not a single repeated character."""
    return chr(ord("a") + (pid_marker % 26)) * ROW_FILLER_CHARS


def append_rows(session_id: str, pid_marker: int, count: int, home: str) -> None:
    os.environ["VERDICT_HOME"] = home
    from verdict_hot import ledger

    filler = row_filler(pid_marker)
    for i in range(count):
        ledger.append_row(
            {
                "schema_v": 1,
                "ts": 0.0,
                "event": "post",
                "session_id": session_id,
                "prompt_id": None,
                "agent_id": None,
                "pid": pid_marker,
                "n": i,
                "filler": filler,
            }
        )
