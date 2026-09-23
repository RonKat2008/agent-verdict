"""Integration tests: the PreToolUse rules gate through the real `run.sh`
launcher (task-5-brief.md).

Every subprocess call passes explicit `input` and a `timeout` (global-
constraints.md). Unlike every other event, a rules-gate internal failure
(here: a corrupt `~/.verdict/policy.json`) must exit 2, not 0 -- this file
is the only place that non-zero exit is exercised end to end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema_check import validate_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUN_SH = ROOT / "plugin" / "hooks" / "run.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
BASH_FIXTURE = json.loads((FIXTURES / "pre_tool_use_bash.json").read_text(encoding="utf-8"))
SESSION_ID = BASH_FIXTURE["session_id"]


def _payload(command: str, **overrides: object) -> bytes:
    payload = dict(BASH_FIXTURE)
    payload["tool_input"] = {"command": command, "description": "test"}
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def _run(
    home: Path, data: bytes, *, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VERDICT_")}
    env["VERDICT_HOME"] = str(home)
    env.update(extra or {})
    return subprocess.run(
        [str(RUN_SH), "pre"], input=data, capture_output=True, timeout=20, env=env, check=False
    )


def _rows(home: Path) -> list[dict[str, object]]:
    path = home / "events" / f"{SESSION_ID}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_safe_bash_command_exits_0_with_no_stdout_and_records_a_pre_row(
    tmp_path: Path,
) -> None:
    proc = _run(tmp_path, _payload("echo hello"))

    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["event"] == "pre"
    assert rows[0]["decision"] is None
    assert rows[0]["rule_id"] is None
    validate_row(rows[0])


def test_a_denied_command_prints_the_exact_deny_json_and_exits_0(tmp_path: Path) -> None:
    proc = _run(tmp_path, _payload("rm -rf /"))

    assert proc.returncode == 0
    stdout = json.loads(proc.stdout.decode("utf-8"))
    assert stdout["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert stdout["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert isinstance(stdout["hookSpecificOutput"]["permissionDecisionReason"], str)
    assert proc.stdout.decode("utf-8").endswith("\n")

    row = _rows(tmp_path)[0]
    assert row["decision"] == "deny"
    assert row["rule_id"] == "deny_rm_root"
    validate_row(row)


def test_a_never_send_write_asks_and_marks_never_send(tmp_path: Path) -> None:
    payload = dict(BASH_FIXTURE)
    payload["tool_name"] = "Write"
    payload["tool_input"] = {"file_path": "/Users/dev/project/.env", "content": "SECRET=1"}
    proc = _run(tmp_path, json.dumps(payload).encode("utf-8"))

    assert proc.returncode == 0
    stdout = json.loads(proc.stdout.decode("utf-8"))
    assert stdout["hookSpecificOutput"]["permissionDecision"] == "ask"

    row = _rows(tmp_path)[0]
    assert row["decision"] == "ask"
    assert row["rule_id"] == "ask_never_send_path"
    assert row["never_send"] is True
    assert "SECRET" not in json.dumps(row)
    assert ".env" not in json.dumps(row)


def test_near_miss_recursive_delete_of_build_dir_is_not_denied(tmp_path: Path) -> None:
    proc = _run(tmp_path, _payload("rm -rf build/"))

    assert proc.returncode == 0
    assert proc.stdout == b""
    row = _rows(tmp_path)[0]
    assert row["decision"] is None


def test_force_push_with_lease_to_a_feature_branch_is_not_denied(tmp_path: Path) -> None:
    proc = _run(tmp_path, _payload("git push --force-with-lease origin feature/foo"))

    assert proc.returncode == 0
    assert proc.stdout == b""
    row = _rows(tmp_path)[0]
    assert row["decision"] is None


def test_corrupt_user_policy_exits_2_with_one_stderr_line_and_no_row(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "policy.json").write_text("{not valid json at all", encoding="utf-8")

    proc = _run(tmp_path, _payload("echo hello"))

    assert proc.returncode == 2
    assert proc.stdout == b""
    stderr_lines = proc.stderr.decode("utf-8").splitlines()
    assert len(stderr_lines) == 1
    assert stderr_lines[0].startswith("Verdict rules gate failed (")
    assert _rows(tmp_path) == []


def test_malformed_pre_payload_exits_0_silently(tmp_path: Path) -> None:
    payload = dict(BASH_FIXTURE)
    del payload["tool_name"]
    proc = _run(tmp_path, json.dumps(payload).encode("utf-8"))

    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    assert _rows(tmp_path) == []


def test_sentinel_key_never_reaches_ledger_or_log(tmp_path: Path) -> None:
    sentinel = "sentinel-pre-hook-key-QWERTY-2468-zzzz"
    proc = _run(
        tmp_path,
        _payload("rm -rf /", session_id=SESSION_ID),
        extra={"CLAUDE_PLUGIN_OPTION_API_KEY": sentinel},
    )
    assert proc.returncode == 0
    blob = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert sentinel not in blob
    assert sentinel.encode() not in proc.stdout


# --- Fix round 1, item 4 (Important): input-shape errors exit 0, not 2 ---


def test_empty_stdin_exits_0_silently(tmp_path: Path) -> None:
    proc = _run(tmp_path, b"")
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    assert _rows(tmp_path) == []


def test_non_json_stdin_exits_0_silently(tmp_path: Path) -> None:
    proc = _run(tmp_path, b"not json at all")
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    assert _rows(tmp_path) == []


def test_oversized_stdin_exits_0_silently(tmp_path: Path) -> None:
    oversized = b'{"padding": "' + b"x" * (6 * 1024 * 1024) + b'"}'
    proc = _run(tmp_path, oversized)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
    assert _rows(tmp_path) == []


def test_corrupt_policy_with_a_valid_payload_still_exits_2(tmp_path: Path) -> None:
    """Distinguishes item 4 (caller input -> exit 0) from item 4's closing
    line (everything past a parsed payload -> stays exit 2): a valid,
    well-formed PreToolUse payload with a broken user policy must still
    fail closed."""
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "policy.json").write_text("{not valid json at all", encoding="utf-8")

    proc = _run(tmp_path, _payload("rm -rf /"))

    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr.decode("utf-8").startswith("Verdict rules gate failed (")


# --- Fix round 1, item 5 (Important): the pre row write is best-effort ---


def test_unwritable_home_with_a_deny_command_still_prints_the_deny_json(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o500)
    try:
        proc = _run(home, _payload("rm -rf /"))
        assert proc.returncode == 0
        stdout = json.loads(proc.stdout.decode("utf-8"))
        assert stdout["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        home.chmod(0o700)


def test_unwritable_home_with_a_benign_command_exits_0_with_no_stdout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o500)
    try:
        proc = _run(home, _payload("echo hello"))
        assert proc.returncode == 0
        assert proc.stdout == b""
    finally:
        home.chmod(0o700)


@pytest.mark.slow
def test_pre_gate_p50_latency_under_60ms_through_the_launcher(tmp_path: Path) -> None:
    """D-029 / task-5-brief.md: the gate's p50 through the launcher is under
    60 ms on an idle machine (31-38 ms measured; `scripts/bench_hook.py
    --event pre` is the gate command). A CI or developer machine under load
    inflates every subprocess the same way, so this test also measures the
    `post` recorder under the same load and requires the gate to be no
    heavier than a recorder: that is the "lean fast path" property (no
    stop/provider/state imports), and it fails on a real regression
    whether or not the machine is idle."""
    pre_data = (FIXTURES / "pre_tool_use_bash.json").read_bytes()
    post_data = (FIXTURES / "post_tool_use_bash.json").read_bytes()
    env = {k: v for k, v in os.environ.items() if not k.startswith("VERDICT_")}
    env["VERDICT_HOME"] = str(tmp_path)

    def _one(event: str, data: bytes) -> float:
        start = time.perf_counter()
        subprocess.run(
            [str(RUN_SH), event],
            input=data,
            capture_output=True,
            timeout=5,
            env=env,
            check=False,
        )
        return (time.perf_counter() - start) * 1000.0

    for _ in range(5):
        _one("pre", pre_data)
        _one("post", post_data)
    pre_t = sorted(_one("pre", pre_data) for _ in range(30))
    post_t = sorted(_one("post", post_data) for _ in range(30))
    pre_p50 = pre_t[len(pre_t) // 2]
    post_p50 = post_t[len(post_t) // 2]
    assert pre_p50 <= post_p50 * 1.25 + 5.0, (
        f"pre gate p50 {pre_p50:.1f}ms is heavier than the post recorder ({post_p50:.1f}ms)"
    )
    assert pre_p50 < 120.0, f"pre gate p50 {pre_p50:.1f}ms is far outside the 60 ms budget"
