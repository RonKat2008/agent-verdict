"""Integration: provider failure injection through the real `run.sh`
launcher (task-7-brief.md controller notes ruling 2, PLAN.md G2.3, G2.5).

`VERDICT_FAKE_PROVIDER` (`_stop_provider.fake_provider_mode`) is a
test-only environment variable, guarded exactly like `VERDICT_CASSETTE_DIR`:
it selects a canned transport response (`timeout`, `429`, `500`,
`malformed`) or, for `no_network`, swaps the real preset host for an
RFC 2606 `.invalid` name so the real `connect_error` path runs without a
socket ever reaching a real host. No test in this file opens a real
network connection to a real host. Every subprocess call passes explicit
`input` and a `timeout=` (global-constraints.md).

G2.3 requires each of these five to give exit 0, empty stdout, and an
`action` row with `gate_unavailable`, within 3 s wall. G2.5 requires that a
simulated 500 and a simulated timeout never leak the API key into the
ledger, `hook.log`, or `verdict export` output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema_check import validate_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUN_SH = ROOT / "plugin" / "hooks" / "run.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
STOP_PAYLOAD = json.loads((FIXTURES / "stop.json").read_text(encoding="utf-8"))
SESSION_ID = STOP_PAYLOAD["session_id"]
PROMPT_ID = STOP_PAYLOAD["prompt_id"]
SENTINEL_KEY = "sentinel-failure-injection-key-QWERTY-1234-zzzz"
_WALL_BUDGET_S = 3.0


def _seeded_rows() -> list[dict[str, Any]]:
    """One prompt row and one unresolved `post_fail` row: the minimum
    evidence G-STOP needs to call the provider at all (same shape
    test_stop_hook.py's `_seeded_rows` uses)."""
    return [
        {
            "schema_v": 1,
            "session_id": SESSION_ID,
            "event": "prompt",
            "prompt_id": PROMPT_ID,
            "prompt_excerpt": "npm test is failing, please fix it",
        },
        {
            "schema_v": 1,
            "session_id": SESSION_ID,
            "event": "post_fail",
            "prompt_id": PROMPT_ID,
            "tool_name": "Bash",
            "tool_use_id": "toolu_failure_injection_1",
            "input_excerpt": "npm test",
            "status": "error",
            "exit_code": 1,
            "is_check": True,
            "never_send": False,
            "error_excerpt": "1 failed, 0 passed",
        },
    ]


def _write_ledger(home: Path, rows: list[dict[str, Any]]) -> None:
    events_dir = home / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    target = events_dir / f"{SESSION_ID}.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    os.chmod(home, 0o700)
    os.chmod(events_dir, 0o700)
    os.chmod(target, 0o600)


def _rows(home: Path) -> list[dict[str, Any]]:
    path = home / "events" / f"{SESSION_ID}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run(
    home: Path, fake_mode: str, api_key: str
) -> tuple[subprocess.CompletedProcess[bytes], float]:
    """Never sets `VERDICT_CASSETTE_DIR`: a fake-provider run must never
    fall back to a recorded cassette by accident."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("VERDICT_")}
    env["VERDICT_HOME"] = str(home)
    env["VERDICT_FAKE_PROVIDER"] = fake_mode
    env["CLAUDE_PLUGIN_OPTION_MODE"] = "enforce"
    env["CLAUDE_PLUGIN_OPTION_API_KEY"] = api_key
    start = time.monotonic()
    proc = subprocess.run(
        [str(RUN_SH), "stop"],
        input=(FIXTURES / "stop.json").read_bytes(),
        capture_output=True,
        timeout=20,
        env=env,
        check=False,
    )
    return proc, time.monotonic() - start


@pytest.mark.parametrize(
    "fake_mode,expected_gate_reason",
    [
        ("timeout", "provider_timeout"),
        ("429", "provider_http_429"),
        ("500", "provider_http_500"),
        ("malformed", "provider_invalid_json"),
        ("no_network", "provider_connect_error"),
    ],
)
def test_provider_failure_through_run_sh_is_gate_unavailable_within_3s(
    tmp_path: Path, fake_mode: str, expected_gate_reason: str
) -> None:
    home = tmp_path / "home"
    _write_ledger(home, _seeded_rows())

    proc, wall_s = _run(home, fake_mode, "fake-test-key")

    assert proc.returncode == 0
    assert proc.stdout == b""
    assert wall_s < _WALL_BUDGET_S, f"{fake_mode} took {wall_s:.2f}s, over the 3s G2.3 budget"

    rows = _rows(home)
    action_row = rows[-1]
    assert action_row["action"] == "gate_unavailable"
    assert action_row["gate_reason"] == expected_gate_reason
    for row in rows[2:]:  # skip the two seeded rows
        validate_row(row)


@pytest.mark.parametrize("fake_mode", ["500", "timeout"])
def test_sentinel_api_key_never_leaks_for_a_simulated_provider_error(
    tmp_path: Path, fake_mode: str
) -> None:
    """G2.5 extension (controller notes ruling 2): a simulated 500 and a
    simulated timeout must not leak the key into the ledger, `hook.log`, or
    `verdict export` output."""
    home = tmp_path / "home"
    _write_ledger(home, _seeded_rows())

    proc, _wall_s = _run(home, fake_mode, SENTINEL_KEY)

    assert proc.returncode == 0
    blob = "".join(p.read_text() for p in home.rglob("*") if p.is_file())
    assert SENTINEL_KEY not in blob
    assert SENTINEL_KEY.encode() not in proc.stdout
    assert SENTINEL_KEY.encode() not in proc.stderr

    out_path = home / "goldset.jsonl"
    export_proc = subprocess.run(
        [sys.executable, "-m", "agent_verdict.cli", "export", "--goldset", "--out", str(out_path)],
        input=b"",
        capture_output=True,
        timeout=20,
        env={**os.environ, "VERDICT_HOME": str(home)},
        check=False,
    )
    assert export_proc.returncode == 0
    assert SENTINEL_KEY.encode() not in export_proc.stdout
    assert SENTINEL_KEY.encode() not in export_proc.stderr
    if out_path.exists():
        assert SENTINEL_KEY not in out_path.read_text(encoding="utf-8")
