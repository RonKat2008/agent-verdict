"""Gate G2.6 (live half): Stop hook latency against a real provider
(task-7-brief.md controller notes ruling 3). Never run by a worker -- the
controller runs this with a real key, exactly like `scripts/e2e_cheap.py
--scenario stop-block`/`stop-shadow`. Refuses cleanly (exit 3, one-line
message) when `OPENROUTER_API_KEY` is absent, rather than failing
obscurely partway through a billed run.

Seeds a temp `VERDICT_HOME` with `tests/golden/_fixtures.py`'s
`unresolved_failure` scenario (an `Edit` that succeeds, then a failing
`npm test` `is_check` step that never resolves), expressed as ledger rows
rather than a directly-built `Span` (`_seeded_rows` below), and runs
`plugin/hooks/run.sh stop` against it `--n` times (default 50) in `shadow`
mode, timing each call and reading back the `hook_ms` field
`_stop_rows.action_row` already stamps on every `action` row.

Each of the `--n` runs uses a FRESH `session_id` (own ledger row and own
`.jsonl` file under the same temp `VERDICT_HOME`), deliberately, so the
runs stay independent: reusing one session across all `--n` calls would
let each run's own `verdict` rows (a real `acks_failures` answer from the
real model) accumulate and potentially acknowledge the seeded failure on a
later run, changing whether G-STOP even reaches the provider on that run
and making the `--n` measurements no longer comparable to each other. This
is the only way to get `--n` truly independent measurements of "one Stop
call against identical evidence" rather than `--n` measurements against a
ledger that quietly evolves underneath them.

Every subprocess call gets explicit `input=` and a `timeout=` of
`PER_CALL_TIMEOUT_S` (anti-hang rule, global-constraints.md).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from bench_hook import percentile

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_SH = REPO_ROOT / "plugin" / "hooks" / "run.sh"

# tests/golden/_fixtures.py's `_unresolved_failure()` scenario, expressed as
# the ledger rows that would produce an equivalent Span (module docstring).
PROMPT_ID = "bench-stop-live-prompt"
FINAL_MESSAGE = "I fixed the login bug. I also added a new test for it."

DEFAULT_N = 50
PER_CALL_TIMEOUT_S = 10.0
P50_MAX_MS = 900.0
P95_MAX_MS = 2500.0
_API_KEY_ENV = "OPENROUTER_API_KEY"
_REFUSE_NO_KEY_EXIT = 3


def _seeded_rows(session_id: str) -> list[dict[str, Any]]:
    return [
        {
            "schema_v": 1,
            "session_id": session_id,
            "event": "prompt",
            "prompt_id": PROMPT_ID,
            "prompt_excerpt": "Fix the login bug and add a test for it.",
        },
        {
            "schema_v": 1,
            "session_id": session_id,
            "event": "post",
            "prompt_id": PROMPT_ID,
            "tool_name": "Edit",
            "tool_use_id": "toolu_bench_stop_live_1",
            "input_excerpt": "src/auth.py",
            "status": "ok",
            "is_check": False,
            "never_send": False,
            "out_head": "",
            "out_tail": "",
        },
        {
            "schema_v": 1,
            "session_id": session_id,
            "event": "post_fail",
            "prompt_id": PROMPT_ID,
            "tool_name": "Bash",
            "tool_use_id": "toolu_bench_stop_live_2",
            "input_excerpt": "npm test",
            "status": "error",
            "exit_code": 1,
            "is_check": True,
            "never_send": False,
            "error_excerpt": "FAIL auth.test.js\n1 failing\nAssertionError: expected true",
        },
    ]


def _write_ledger(home: Path, session_id: str) -> None:
    events_dir = home / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    target = events_dir / f"{session_id}.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for row in _seeded_rows(session_id):
            fh.write(json.dumps(row) + "\n")
    os.chmod(home, 0o700)
    os.chmod(events_dir, 0o700)
    os.chmod(target, 0o600)


def _stop_payload(session_id: str) -> bytes:
    payload = {
        "background_tasks": [],
        "cwd": "/tmp/verdict-bench-stop-live",
        "hook_event_name": "Stop",
        "last_assistant_message": FINAL_MESSAGE,
        "permission_mode": "acceptEdits",
        "prompt_id": PROMPT_ID,
        "session_crons": [],
        "session_id": session_id,
        "stop_hook_active": False,
        "transcript_path": "/tmp/verdict-bench-stop-live/transcript.jsonl",
    }
    return json.dumps(payload).encode("utf-8")


def _read_rows(home: Path, session_id: str) -> list[dict[str, Any]]:
    path = home / "events" / f"{session_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_once(home: Path, api_key: str) -> float | None:
    """Seeds a fresh session, runs `run.sh stop` once, and returns the
    resulting `action` row's `hook_ms`, or `None` if no `action` row with
    `hook_ms` appeared (e.g. a stand-down before the provider gate)."""
    session_id = f"bench-stop-live-{uuid.uuid4()}"
    _write_ledger(home, session_id)

    env = {k: v for k, v in os.environ.items() if not k.startswith("VERDICT_")}
    env["VERDICT_HOME"] = str(home)
    env["CLAUDE_PLUGIN_OPTION_MODE"] = "shadow"
    env["CLAUDE_PLUGIN_OPTION_PROVIDER"] = "openrouter"
    env["CLAUDE_PLUGIN_OPTION_API_KEY"] = api_key
    subprocess.run(
        [str(RUN_SH), "stop"],
        input=_stop_payload(session_id),
        capture_output=True,
        timeout=PER_CALL_TIMEOUT_S,
        env=env,
        check=False,
    )

    action_rows = [r for r in _read_rows(home, session_id) if r.get("event") == "action"]
    if not action_rows:
        return None
    hook_ms = action_rows[-1].get("hook_ms")
    return float(hook_ms) if isinstance(hook_ms, (int, float)) else None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench_stop_live", description="Measure live Stop hook latency (G2.6)"
    )
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="measured runs (default 50)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    api_key = os.environ.get(_API_KEY_ENV, "").strip()
    if not api_key:
        print(
            f"bench_stop_live REFUSED: {_API_KEY_ENV} is not set. This benchmark calls a real "
            f"provider through run.sh and must not run without a key. Set {_API_KEY_ENV} (never "
            "read from ~/.config/agent-verdict/dev.env by this script) and re-run.",
            file=sys.stderr,
        )
        return _REFUSE_NO_KEY_EXIT

    hook_ms_values: list[float] = []
    with tempfile.TemporaryDirectory(prefix="verdict-bench-stop-live-") as home_dir:
        home = Path(home_dir)
        for i in range(args.n):
            try:
                hook_ms = run_once(home, api_key)
            except subprocess.TimeoutExpired:
                print(f"bench_stop_live: run {i} timed out after {PER_CALL_TIMEOUT_S}s")
                continue
            if hook_ms is None:
                print(f"bench_stop_live: run {i} produced no action row with hook_ms")
                continue
            hook_ms_values.append(hook_ms)

    if not hook_ms_values:
        print("GATE G2.6 FAILED: no run produced a measurable hook_ms", file=sys.stderr)
        return 1

    p50 = percentile(hook_ms_values, 50)
    p95 = percentile(hook_ms_values, 95)
    print(f"stop: p50={p50:.1f}ms p95={p95:.1f}ms (n={len(hook_ms_values)}/{args.n})")

    if p50 < P50_MAX_MS and p95 < P95_MAX_MS:
        print("GATE G2.6 PASSED")
        return 0
    print(
        f"GATE G2.6 FAILED: p50 {p50:.1f}ms (limit {P50_MAX_MS:.0f}ms) / "
        f"p95 {p95:.1f}ms (limit {P95_MAX_MS:.0f}ms)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
