"""Integration test: the Stop verifier through the real `run.sh` launcher
(task-4-brief.md, controller notes ruling 5).

This is the only place a provider is reached through the subprocess path,
and it never touches a socket: `VERDICT_CASSETTE_DIR` points `stop.py` at a
`RecordedTransport`, and the cassette it needs is computed and written by
this test itself, using `agent_verdict.verdict_hot` (the CLI-importable
mirror of `plugin/hooks/verdict_hot`, task-1-brief.md) to build the exact
same span/state/questions/canonical request body `stop.py` will build at
run time, so its sha256 matches by construction rather than by guessing.

Every subprocess call passes explicit `input` and `timeout` (global-
constraints.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_verdict.verdict_hot import claims as claims_mod
from agent_verdict.verdict_hot import policy as policy_mod
from agent_verdict.verdict_hot import provider as provider_mod
from agent_verdict.verdict_hot import questions as questions_mod
from agent_verdict.verdict_hot import span as span_mod
from agent_verdict.verdict_hot import state as state_mod

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema_check import validate_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUN_SH = ROOT / "plugin" / "hooks" / "run.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"

STOP_PAYLOAD = json.loads((FIXTURES / "stop.json").read_text(encoding="utf-8"))
SESSION_ID = STOP_PAYLOAD["session_id"]
PROMPT_ID = STOP_PAYLOAD["prompt_id"]
LAST_MESSAGE = STOP_PAYLOAD["last_assistant_message"]
SENTINEL_KEY = "sentinel-stop-hook-key-QWERTY-7890-zzzz"


def _seeded_rows() -> list[dict[str, Any]]:
    """One prompt row and one unresolved `post_fail` row for the fixture's
    own (session_id, prompt_id) -- exactly the shape span.py's module
    docstring documents (`post`/`post_fail`/`prompt` fields)."""
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
            "tool_use_id": "toolu_test_stop_hook_1",
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


def _build_cassette(overrides: dict[str, float]) -> tuple[str, dict[str, Any]]:
    """Build the exact state/questions `stop.py` will build for the seeded
    rows above, and return (sha256_hex, cassette_dict) for a cassette that
    answers every question `build_questions` actually asks."""
    policy = policy_mod.load_policy(PACKAGED_DEFAULT)
    rows = _seeded_rows()
    span = span_mod.build_span(rows, PROMPT_ID, policy)
    claim_list = claims_mod.extract_claims(LAST_MESSAGE, policy)
    state, _overflow = state_mod.build_state(span, LAST_MESSAGE, claim_list, policy)
    questions = questions_mod.build_questions(state)

    preset = provider_mod.PRESETS[policy.provider.default]
    body = provider_mod.canonical_request_body(preset.model, state, questions)
    sha = hashlib.sha256(body).hexdigest()

    answers: dict[str, Any] = {}
    for key, question in questions.items():
        qtype = question["type"]
        value = overrides.get(key, 0.0)
        if qtype == "noul":
            answers[key] = {"type": "noul", "noul": value}
        elif qtype == "score":
            answers[key] = {"type": "score", "score": value}
        else:
            answers[key] = {"type": "choice", "choice": "not_started"}

    cassette = {"model_returned": preset.model, "answers": answers}
    return sha, cassette


def _run(
    home: Path, cassette_dir: Path, *, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VERDICT_")}
    env["VERDICT_HOME"] = str(home)
    env["VERDICT_CASSETTE_DIR"] = str(cassette_dir)
    env.update(extra or {})
    return subprocess.run(
        [str(RUN_SH), "stop"],
        input=(FIXTURES / "stop.json").read_bytes(),
        capture_output=True,
        timeout=20,
        env=env,
        check=False,
    )


def _rows(home: Path) -> list[dict[str, Any]]:
    path = home / "events" / f"{SESSION_ID}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_enforce_block_through_run_sh_with_a_synthetic_cassette(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    _write_ledger(home, _seeded_rows())

    sha, cassette = _build_cassette({"claims_done": 0.9, "acks_failures": 0.0})
    (cassette_dir / f"{sha}.json").write_text(json.dumps(cassette), encoding="utf-8")

    proc = _run(
        home,
        cassette_dir,
        extra={
            "CLAUDE_PLUGIN_OPTION_MODE": "enforce",
            "CLAUDE_PLUGIN_OPTION_API_KEY": "fake-test-key",
        },
    )

    assert proc.returncode == 0
    assert proc.stdout != b""
    stdout = json.loads(proc.stdout.decode("utf-8"))
    assert stdout == {"decision": "block", "reason": stdout["reason"]}
    assert "Rule R1" in stdout["reason"]

    rows = _rows(home)
    events = [r["event"] for r in rows]
    assert events.count("verdict") >= 1
    assert events[-1] == "action"
    for row in rows[2:]:  # skip the two seeded rows: only stop.py's own rows are re-validated
        validate_row(row)
    action_row = rows[-1]
    assert action_row["action"] == "block"
    assert action_row["rule_id"] == "R1"
    assert action_row["mode"] == "enforce"


def test_shadow_mode_never_prints_through_run_sh(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    _write_ledger(home, _seeded_rows())

    sha, cassette = _build_cassette({"claims_done": 0.9, "acks_failures": 0.0})
    (cassette_dir / f"{sha}.json").write_text(json.dumps(cassette), encoding="utf-8")

    proc = _run(
        home, cassette_dir, extra={"CLAUDE_PLUGIN_OPTION_API_KEY": "fake-test-key"}
    )  # default mode: shadow

    assert proc.returncode == 0
    assert proc.stdout == b""
    action_row = _rows(home)[-1]
    assert action_row["action"] == "pass"
    assert action_row["would_have"] == "block"


def test_sentinel_api_key_never_leaks_into_the_ledger_or_hook_log(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    _write_ledger(home, _seeded_rows())

    sha, cassette = _build_cassette({"claims_done": 0.9, "acks_failures": 0.0})
    (cassette_dir / f"{sha}.json").write_text(json.dumps(cassette), encoding="utf-8")

    proc = _run(
        home,
        cassette_dir,
        extra={
            "CLAUDE_PLUGIN_OPTION_MODE": "enforce",
            "CLAUDE_PLUGIN_OPTION_API_KEY": SENTINEL_KEY,
        },
    )

    assert proc.returncode == 0
    blob = "".join(p.read_text() for p in home.rglob("*") if p.is_file())
    assert SENTINEL_KEY not in blob
    assert SENTINEL_KEY.encode() not in proc.stdout
    assert SENTINEL_KEY.encode() not in proc.stderr


def test_missing_cassette_through_run_sh_is_gate_unavailable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    _write_ledger(home, _seeded_rows())
    # No cassette written: the request's sha256 matches nothing on disk.

    proc = _run(
        home,
        cassette_dir,
        extra={
            "CLAUDE_PLUGIN_OPTION_MODE": "enforce",
            "CLAUDE_PLUGIN_OPTION_API_KEY": "fake-test-key",
        },
    )

    assert proc.returncode == 0
    assert proc.stdout == b""
    action_row = _rows(home)[-1]
    assert action_row["action"] == "gate_unavailable"
    assert action_row["gate_reason"] == "cassette_missing"


@pytest.mark.parametrize("value", ["off", "OFF"])
def test_mode_off_skips_the_verifier_through_run_sh(tmp_path: Path, value: str) -> None:
    home = tmp_path / "home"
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    _write_ledger(home, _seeded_rows())

    proc = _run(home, cassette_dir, extra={"CLAUDE_PLUGIN_OPTION_MODE": value})

    assert proc.returncode == 0 and proc.stdout == b""
    # `off` is handled before any import: no new rows beyond the two seeded ones.
    assert len(_rows(home)) == 2


def test_an_unparseable_stop_payload_is_skipped_not_judged(tmp_path: Path) -> None:
    """Final review I4: a Stop payload the recorder cannot parse must not
    reach the verifier -- no `action` row, empty stdout, and hook.log keeps
    the recorder's `skipped` outcome instead of a verdict overwriting it."""
    home = tmp_path / "home"
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    _write_ledger(home, _seeded_rows())
    payload = json.loads((FIXTURES / "stop.json").read_text())
    for required in ("cwd", "transcript_path"):
        payload.pop(required, None)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VERDICT_")}
    env["VERDICT_HOME"] = str(home)
    env["VERDICT_CASSETTE_DIR"] = str(cassette_dir)
    env["CLAUDE_PLUGIN_OPTION_MODE"] = "enforce"

    proc = subprocess.run(
        [str(RUN_SH), "stop"],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=20,
        env=env,
        check=False,
    )

    assert proc.returncode == 0 and proc.stdout == b""
    assert [r["event"] for r in _rows(home) if r["event"] == "action"] == []
    log_lines = [json.loads(line) for line in (home / "hook.log").read_text().splitlines()]
    stop_lines = [entry for entry in log_lines if entry.get("event") == "stop"]
    assert stop_lines and stop_lines[-1]["outcome"] == "skipped"
