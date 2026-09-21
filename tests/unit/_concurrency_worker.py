"""Top-level worker for the multiprocessing concurrency test (test_ledger.py).

Lives in its own module so `multiprocessing`'s spawn context can import it by
name in the child process.
"""

from __future__ import annotations

import os


def append_rows(session_id: str, pid_marker: int, count: int, home: str) -> None:
    os.environ["VERDICT_HOME"] = home
    from verdict_hot import ledger

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
            }
        )
