"""Top-level worker for the 8-process rotation-race test (test_logsafe.py).

Lives in its own module so `multiprocessing`'s spawn context can import it by
name in the child process (same reasoning as _concurrency_worker.py).
"""

from __future__ import annotations

import os


def write_log_lines(home: str, count: int, pid_marker: int, max_log_bytes: int) -> None:
    os.environ["VERDICT_HOME"] = home

    from verdict_hot import logsafe

    os.environ[logsafe._TEST_MAX_LOG_BYTES_ENV] = str(max_log_bytes)

    for i in range(count):
        logsafe.log_invocation(
            "post", f"pid-{pid_marker}", "ok", 1.0, extra={"pid": pid_marker, "n": i}
        )
