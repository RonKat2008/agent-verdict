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

M2 (task-4-brief.md): after `recorders.record`, a payload whose own
`hook_event_name` is `"Stop"` or `"SubagentStop"` is also routed to
`stop.handle`, which enforces the 2.5s provider budget measured from
`start` (the same `time.monotonic()` value `main` already captures, passed
through so the budget is spent from hook entry, not from `stop.handle`
entry). Routing is decided from the PAYLOAD's own `hook_event_name`, the
same field `parsers.parse_event`/`recorders.record` already dispatch on,
not from `argv` -- `argv` remains only a cosmetic label for `hook.log`
(hooks.json's `args` always agrees with it in a real invocation, but
nothing here depends on that agreement). `stop.handle` never raises
(module docstring); when it returns a non-`None` `stdout_json` it is
printed verbatim, still followed by exit 0 in every case.

Fix round 1 item 1: `_run_stop` itself is NOT trusted to be exception-free
the way `stop.handle` is -- policy loading, the import of `verdict_hot.stop`,
and the final `sys.stdout.write` can all still raise (a broken packaged
default, an import failure, a closed stdout pipe). The call site wraps it
in its own `try/except Exception`, so a failure there degrades to the same
`(session_id, "exception", err_class)` outcome the rest of `_handle`
already uses, instead of propagating out of `main` and exiting non-zero.

Task 5 (task-5-brief.md, controller notes): argv event `"pre"` (hooks.json's
`args: ["pre"]` on the PreToolUse entry) is routed to `_handle_pre` instead
of `_handle`, entirely before `_handle` is ever reached -- this is the one
hook path that fails CLOSED. `_handle_pre` wraps `_run_pre` in its own
`try/except Exception`, isolated from every other event's fail-open
`try/except` in `_handle`: an internal exception there (a corrupt user
policy, a bug in `rules.decide`, a ledger write failure) writes one stderr
line and returns exit code 2 with no stdout, instead of degrading to an
`"exception"` outcome and exit 0 the way every other event does. A
PreToolUse payload that merely fails to *parse* is not treated as an
internal exception: `_run_pre` catches `parsers.ParseError` itself and
returns 0, so a shape Claude Code has not sent yet exits quietly rather
than blocking the tool call.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from verdict_hot.parsers import PreEvent
    from verdict_hot.rules import RuleDecision

_MAX_STDIN_BYTES = 5 * 1024 * 1024
_MODE_OFF = "off"
_PRE_EVENT_NAME = "pre"


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

    if event == _PRE_EVENT_NAME:
        return _handle_pre(start)

    session_id, outcome, err_class = _handle(start)
    total_ms = (time.monotonic() - start) * 1000.0
    logsafe.log_invocation(event, session_id, outcome, total_ms, err_class)
    return 0


def _handle_pre(start: float) -> int:
    """The PreToolUse rules gate entry point. Fails CLOSED (module
    docstring, task-5-brief.md): any exception from `_run_pre` -- not just
    the ones `_run_pre` itself anticipates -- becomes exit 2 with a single
    stderr line, never exit 0. Kept as its own `try/except`, never merged
    into `_handle`'s, so a bug here can never quietly degrade to that
    function's fail-open outcome instead."""
    try:
        return _run_pre(start)
    except Exception as exc:  # noqa: BLE001 - fail-closed contract (task-5-brief.md)
        sys.stderr.write(f"Verdict rules gate failed ({type(exc).__name__})\n")
        return 2


def _run_pre(start: float) -> int:
    """Parse, decide, record, and (maybe) print -- for one PreToolUse call.

    Fix round 1 item 4: a stdin read/parse failure (empty stdin, non-JSON
    stdin, an oversized payload, or a payload that isn't PreToolUse-shaped)
    is the caller's input, not a gate defect -- each of those returns 0
    with empty stdout via `_log_pre_skipped`, logged as outcome `"skipped"`.
    Everything past a successfully parsed payload (a broken user policy,
    `rules.decide`, output serialization) is still allowed to raise
    straight through to `_handle_pre`'s fail-closed wrapper -- in
    particular, `policy.load_policy()` here does NOT fall back to the
    packaged default the way `_run_stop` does: a corrupt override on the
    one path that fails closed must surface as exit 2, not silently run
    with a policy the user never wrote (controller notes ruling 8). The
    one exception, per fix round 1 item 5, is the ledger row write itself:
    that is best-effort (`_record_pre_row_best_effort`), so a `VERDICT_HOME`
    the process cannot write to still prints the decision and exits 0.
    """
    import time

    from verdict_hot import logsafe, parsers, rules
    from verdict_hot import policy as policy_mod

    try:
        payload = _read_stdin_json(_MAX_STDIN_BYTES)
    except ValueError:
        return _log_pre_skipped(start, None)

    if not isinstance(payload, dict):
        return _log_pre_skipped(start, _best_effort_session_id(payload))

    try:
        event = parsers.parse_pre_event(payload)
    except parsers.ParseError:
        return _log_pre_skipped(start, _best_effort_session_id(payload))

    active_policy = policy_mod.load_policy()
    decision = rules.decide(event.tool_name, event.tool_input, active_policy, event.cwd)
    ledger_err_class = _record_pre_row_best_effort(event, decision)

    stdout_json = rules.build_output_json(decision)
    if stdout_json is not None:
        sys.stdout.write(stdout_json)

    total_ms = (time.monotonic() - start) * 1000.0
    logsafe.log_invocation(_PRE_EVENT_NAME, event.session_id, "ok", total_ms, ledger_err_class)
    return 0


def _best_effort_session_id(payload: object) -> str | None:
    if isinstance(payload, dict):
        candidate = payload.get("session_id")
        if isinstance(candidate, str):
            return candidate
    return None


def _log_pre_skipped(start: float, session_id: str | None) -> int:
    import time

    from verdict_hot import logsafe

    total_ms = (time.monotonic() - start) * 1000.0
    logsafe.log_invocation(_PRE_EVENT_NAME, session_id, "skipped", total_ms)
    return 0


def _record_pre_row_best_effort(event: PreEvent, decision: RuleDecision) -> str | None:
    """Fix round 1 item 5: the `pre` row is best-effort. A write failure
    (an unwritable `VERDICT_HOME`, a full disk) must never stop the
    decision from reaching stdout -- only the exception's class name is
    kept, via `hook.log`'s existing `err_class` field, never `str(exc)`.
    """
    import time

    from verdict_hot import rules

    try:
        rules.record_pre_row(event, decision, time.time())
    except Exception as exc:  # noqa: BLE001 - the ledger write is best-effort (item 5)
        return type(exc).__name__
    return None


def _log_windows_disabled(argv: list[str]) -> None:
    from verdict_hot import logsafe

    event = argv[0] if argv else "unknown"
    logsafe.install_excepthook()
    logsafe.log_invocation(event, None, "disabled", 0.0)


_STOP_EVENT_NAMES = ("Stop", "SubagentStop")
_SESSION_END_EVENT_NAME = "SessionEnd"


def _handle(start: float) -> tuple[str | None, str, str | None]:
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
        outcome = "skipped"
    except Exception as exc:  # noqa: BLE001 - fail-open contract (global-constraints.md)
        return session_id, "exception", type(exc).__name__

    if isinstance(payload, dict) and payload.get("hook_event_name") in _STOP_EVENT_NAMES:
        try:
            outcome = _run_stop(cast(Mapping[str, object], payload), start)
        except Exception as exc:  # noqa: BLE001 - fail-open contract (fix round 1 item 1)
            return session_id, "exception", type(exc).__name__
    elif isinstance(payload, dict) and payload.get("hook_event_name") == _SESSION_END_EVENT_NAME:
        _prune_best_effort()

    return session_id, outcome, None


def _prune_best_effort() -> None:
    """Bounded SessionEnd-time prune (task-6-brief.md controller notes,
    ruling 2). Best-effort and silent: `prune.prune` already fails open on
    its own, and any failure loading the policy here (or importing the
    module at all) must never affect this hook's outcome or exit code."""
    try:
        from verdict_hot import policy as policy_mod
        from verdict_hot import prune as prune_mod

        prune_mod.prune(policy_mod.load_policy())
    except Exception:  # noqa: BLE001 - fail-open contract (global-constraints.md)
        return


def _run_stop(payload: Mapping[str, object], start: float) -> str:
    """Load the effective policy and run the Stop/SubagentStop verifier.

    Stands down before ever reaching `stop.handle` when the effective
    policy's `mode` is `off` (the same second line of defense
    `recorders.record` already applies): no row, no provider call, no
    stdout -- mirroring `test_policy_mode_off_skips_the_row_but_still_logs`.
    """
    from verdict_hot import policy as policy_mod
    from verdict_hot import stop

    try:
        active_policy = policy_mod.load_policy()
    except policy_mod.PolicyError:
        active_policy = policy_mod.load_policy(path=policy_mod.default_policy_path())

    if active_policy.mode.strip().lower() == _MODE_OFF:
        return "skipped"

    api_key = os.environ.get("CLAUDE_PLUGIN_OPTION_API_KEY")
    outcome = stop.handle(payload, active_policy, start, api_key)
    if outcome.stdout_json is not None:
        sys.stdout.write(outcome.stdout_json)
    return outcome.outcome


def _read_stdin_json(cap: int) -> object:
    import json

    data = sys.stdin.buffer.read(cap + 1)
    if len(data) > cap:
        raise ValueError(f"stdin exceeds the {cap}-byte cap")
    parsed: object = json.loads(data.decode("utf-8"))
    return parsed


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
