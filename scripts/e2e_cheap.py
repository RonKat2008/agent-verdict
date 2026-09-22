"""Gate G1.4: headless end-to-end smoke test (task-6-brief.md).

Runs `claude -p` against a task built to fail (`sh -c 'exit 3'`) with the
plugin loaded via `--plugin-dir ./plugin` (never installed into the user's
own Claude Code) and a temp `VERDICT_HOME`, then asserts:

- the stream's `system`/`init` message carries no `plugin_errors`;
- the raw stream text contains no "hook error" notice (VERIFIED_FACTS A15);
- the temp ledger holds a `session_start` row, a `prompt` row, a `stop`
  row, and a `post_fail` row with `exit_code == 3`.

Per G15, the owner's global gate hook blocks the first Bash call of a
headless session and the model retries under a new `tool_use_id`, so this
only asserts presence of rows, never call order.

This is the ONLY place in M1 that calls `claude -p`: each run is a real,
billed API call. Per the controller notes, run this at most three times
total while developing. Runs in a temporary working directory, never the
project tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugin"
PROMPT = "Run this exact shell command and tell me its exit code: sh -c 'exit 3'"
DEFAULT_MODEL = "haiku"
FALLBACK_MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 6
TIMEOUT_S = 240.0
EXPECTED_EXIT_CODE = 3
_HOOK_ERROR_CONTEXT_CHARS = 40

# agent-verdict's own registered hook events (plugin/hooks/hooks.json). M1
# registers no PreToolUse hook at all, so a "PreToolUse ... hook error"
# notice can only come from some other hook on this machine -- observed in
# practice (G15, docs/VERIFIED_FACTS.md): the owner's own global
# "Fact-Forcing Gate" PreToolUse hook blocks the first Bash call of every
# headless session, completely independent of agent-verdict, and its
# "PreToolUse:Bash hook error: [Fact-Forcing Gate] ..." text is expected to
# appear in the stream on this development machine. Only a hook error
# notice naming one of *our* events is a real agent-verdict regression.
_OUR_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
)


class AssertionFailure(Exception):
    """A gate assertion failed; the message is printed and the run fails."""


def _claude_command(model: str) -> list[str]:
    return [
        "claude",
        "-p",
        PROMPT,
        "--plugin-dir",
        str(PLUGIN_DIR),
        "--model",
        model,
        "--max-turns",
        str(MAX_TURNS),
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
    ]


def run_claude(model: str, verdict_home: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["VERDICT_HOME"] = str(verdict_home)
    return subprocess.run(
        _claude_command(model),
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_S,
        text=True,
        check=False,
    )


def _looks_like_model_rejection(stderr_text: str) -> bool:
    lowered = stderr_text.lower()
    return "model" in lowered and any(
        phrase in lowered for phrase in ("not found", "invalid", "unknown", "unrecognized")
    )


def parse_stream(stdout_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def assert_plugin_loaded_cleanly(events: list[dict[str, Any]]) -> None:
    init = next(
        (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None
    )
    if init is None:
        raise AssertionFailure("no system/init message found in the stream")
    errors = init.get("plugin_errors") or init.get("pluginErrors")
    if errors:
        raise AssertionFailure(f"system/init reported plugin_errors: {errors}")


def _is_our_hook_error(stdout_text: str, idx: int) -> bool:
    window_start = max(0, idx - _HOOK_ERROR_CONTEXT_CHARS)
    preceding = stdout_text[window_start:idx]
    return any(event in preceding for event in _OUR_HOOK_EVENTS)


def assert_no_hook_error_text(stdout_text: str) -> None:
    """Fails only on a hook error notice naming one of agent-verdict's own
    hook events -- see `_OUR_HOOK_EVENTS` for why a PreToolUse (or other
    unrelated) hook error is never ours to fail the gate on.
    """
    lowered = stdout_text.lower()
    search_from = 0
    while True:
        idx = lowered.find("hook error", search_from)
        if idx == -1:
            return
        if _is_our_hook_error(stdout_text, idx):
            context = stdout_text[max(0, idx - 100) : idx + 200]
            raise AssertionFailure(
                f"stream output contains an agent-verdict hook error notice: ...{context}..."
            )
        search_from = idx + len("hook error")


def load_ledger_rows(verdict_home: Path) -> list[dict[str, Any]]:
    from agent_verdict.verdict_hot import ledger

    rows: list[dict[str, Any]] = []
    for session_path in ledger.iter_sessions():
        rows.extend(ledger.read_session(session_path.stem))
    return rows


def assert_ledger_rows(rows: list[dict[str, Any]]) -> None:
    events_seen = {row.get("event") for row in rows}
    for required in ("session_start", "prompt", "stop"):
        if required not in events_seen:
            raise AssertionFailure(f"no {required!r} row found in the ledger ({rows!r})")

    post_fail_ok = any(
        row.get("event") == "post_fail" and row.get("exit_code") == EXPECTED_EXIT_CODE
        for row in rows
    )
    if not post_fail_ok:
        raise AssertionFailure(
            f"no post_fail row with exit_code == {EXPECTED_EXIT_CODE} found in the ledger"
        )


def _run_with_fallback(verdict_home: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = run_claude(DEFAULT_MODEL, verdict_home, cwd)
    if result.returncode != 0 and _looks_like_model_rejection(result.stderr):
        print(
            f"model alias {DEFAULT_MODEL!r} rejected, retrying with {FALLBACK_MODEL!r}",
            file=sys.stderr,
        )
        result = run_claude(FALLBACK_MODEL, verdict_home, cwd)
    return result


def main(argv: list[str] | None = None) -> int:
    del argv  # no CLI flags yet; kept for a consistent script entry-point shape
    with (
        tempfile.TemporaryDirectory(prefix="verdict-e2e-home-") as home_dir,
        tempfile.TemporaryDirectory(prefix="verdict-e2e-cwd-") as cwd_dir,
    ):
        verdict_home = Path(home_dir)
        cwd = Path(cwd_dir)
        # Also set it in this process's own environment (not just the
        # subprocess env dict built in run_claude) so load_ledger_rows'
        # paths.verdict_home() reads the same temp home when we read the
        # ledger back after claude exits.
        os.environ["VERDICT_HOME"] = str(verdict_home)

        try:
            result = _run_with_fallback(verdict_home, cwd)
        except subprocess.TimeoutExpired:
            print(f"e2e-cheap FAILED: claude -p timed out after {TIMEOUT_S}s", file=sys.stderr)
            return 1
        except FileNotFoundError:
            print("e2e-cheap FAILED: claude binary not found on PATH", file=sys.stderr)
            return 1

        events = parse_stream(result.stdout)
        try:
            assert_plugin_loaded_cleanly(events)
            assert_no_hook_error_text(result.stdout)
            rows = load_ledger_rows(verdict_home)
            assert_ledger_rows(rows)
        except AssertionFailure as exc:
            print(f"e2e-cheap FAILED: {exc}", file=sys.stderr)
            print(f"--- claude exit code: {result.returncode} ---", file=sys.stderr)
            debug_dir = Path(tempfile.gettempdir())
            (debug_dir / "verdict-e2e-cheap-stdout.log").write_text(result.stdout)
            (debug_dir / "verdict-e2e-cheap-stderr.log").write_text(result.stderr)
            print(f"--- full stdout/stderr saved under {debug_dir} ---", file=sys.stderr)
            rows = load_ledger_rows(verdict_home)
            print(f"--- {len(rows)} ledger row(s) recorded ---", file=sys.stderr)
            for row in rows:
                summary = {
                    k: row.get(k)
                    for k in ("event", "tool_name", "status", "exit_code", "never_send")
                    if k in row
                }
                print(f"  {summary}", file=sys.stderr)
            print(f"--- last 2000 chars of stdout ---\n{result.stdout[-2000:]}", file=sys.stderr)
            print(f"--- last 2000 chars of stderr ---\n{result.stderr[-2000:]}", file=sys.stderr)
            return 1

    print("e2e-cheap PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
