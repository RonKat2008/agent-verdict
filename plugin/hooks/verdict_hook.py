"""Hook entry point: stdin -> one ledger row, always fail-open (task-5-brief.md).

Exits 0 with an empty stdout in every case: malformed stdin, an unknown or
malformed event (`parsers.ParseError`, logged as outcome `"skipped"`), an
unwritable data root, or any other exception (logged as outcome
`"exception"`, with only the exception's *class name* -- never `str(exc)`,
which could carry payload text or an env secret). Anything printed to
stdout could be read by Claude Code as a JSON hook decision on every exit
code (VERIFIED_FACTS A15); a non-zero exit shows the user a visible
"hook error" notice. M1 emits no decisions at all (global-constraints.md).

`VERDICT_DISABLE`, the plugin's own `mode` option
(`CLAUDE_PLUGIN_OPTION_MODE`, where `off` "disables recording entirely" per
`plugin/.claude-plugin/plugin.json`), and `os.name == "nt"` are checked
first, using only the `os` and `sys` names already paid for by the
interpreter, so a disabled hook does almost no work (controller notes,
task-5-brief.md). `mode: off` writes nothing at all, not even a hook.log
line, since the run never reaches an import. Every
`verdict_hot` module -- `logsafe` included -- is imported lazily, after
those two checks pass, per the per-event lazy-import rule (D-028).

Soft deadline (controller notes, task-5-brief.md): "enforces a 200 ms soft
deadline ... simplest compliant approach: measure and log, never sleep or
retry." `recorders.record` is a single call with no internal checkpoint to
abort from, so the compliant behavior here is exactly that simplest form:
`total_ms` is measured end to end and always logged; nothing is skipped
mid-flight and nothing ever sleeps or retries.
"""

from __future__ import annotations

import os
import sys

_MAX_STDIN_BYTES = 5 * 1024 * 1024
_MODE_OFF = "off"


def main(argv: list[str]) -> int:
    if os.environ.get("VERDICT_DISABLE"):
        return 0
    if os.environ.get("CLAUDE_PLUGIN_OPTION_MODE", "").strip().lower() == _MODE_OFF:
        return 0  # plugin.json: `off` disables recording entirely -- not even hook.log
    if os.name == "nt":
        _log_windows_disabled(argv)
        return 0

    import time

    start = time.monotonic()
    event = argv[0] if argv else "unknown"

    from verdict_hot import logsafe

    logsafe.install_excepthook()

    session_id, outcome, err_class = _handle()
    total_ms = (time.monotonic() - start) * 1000.0
    logsafe.log_invocation(event, session_id, outcome, total_ms, err_class)
    return 0


def _log_windows_disabled(argv: list[str]) -> None:
    from verdict_hot import logsafe

    event = argv[0] if argv else "unknown"
    logsafe.install_excepthook()
    logsafe.log_invocation(event, None, "disabled", 0.0)


def _handle() -> tuple[str | None, str, str | None]:
    """Read stdin, parse JSON, and record one ledger row.

    Never raises: every failure is converted into an `(session_id, outcome,
    err_class)` triple for `main` to log. `session_id` is best-effort --
    read directly off the raw payload so it is available even when
    `recorders.record` itself fails.
    """
    try:
        payload = _read_stdin_json(_MAX_STDIN_BYTES)
    except Exception as exc:  # noqa: BLE001 - fail-open contract (global-constraints.md)
        return None, "exception", type(exc).__name__

    session_id: str | None = None
    if isinstance(payload, dict):
        candidate = payload.get("session_id")
        if isinstance(candidate, str):
            session_id = candidate

    from collections.abc import Mapping
    from typing import cast

    from verdict_hot import parsers, recorders

    try:
        outcome = recorders.record(cast(Mapping[str, object], payload))
    except parsers.ParseError:
        return session_id, "skipped", None
    except Exception as exc:  # noqa: BLE001 - fail-open contract (global-constraints.md)
        return session_id, "exception", type(exc).__name__
    return session_id, outcome, None


def _read_stdin_json(cap: int) -> object:
    import json

    data = sys.stdin.buffer.read(cap + 1)
    if len(data) > cap:
        raise ValueError(f"stdin exceeds the {cap}-byte cap")
    parsed: object = json.loads(data.decode("utf-8"))
    return parsed


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
