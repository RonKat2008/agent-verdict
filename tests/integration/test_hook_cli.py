"""Integration tests: drive the real launcher as a subprocess (Task 5).

Every subprocess call passes explicit ``input`` and ``timeout`` so a test can
never block on an inherited terminal stdin.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema_check import validate_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUN_SH = ROOT / "plugin" / "hooks" / "run.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
M1_FIXTURES = [
    ("session_start", "session_start"),
    ("user_prompt_submit", "prompt"),
    ("post_tool_use_bash", "post"),
    ("post_tool_use_failure_bash", "post_fail"),
    ("session_end", "session_end"),
]
# "stop" is exercised separately (test_stop_fixture_also_runs_the_m2_verifier):
# since M2, a Stop payload is also routed to stop.handle, which appends a
# second `action` row and (with no prior evidence) changes hook.log's
# recorded outcome from "ok" to a real Stop outcome ("pass" here) -- see
# tests/integration/test_stop_hook.py for the provider-calling paths.
SYSTEM_PYTHON = "/usr/bin/python3"
INTERPRETERS = [None] + ([SYSTEM_PYTHON] if os.path.exists(SYSTEM_PYTHON) else [])
SENTINEL_KEY = (
    "odd-format-sentinel-key-QWERTY-1234567890-zzzz"  # no rule matches its shape on purpose
)


def _run(
    home: Path, data: bytes, *, python: str | None = None, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("VERDICT_")}
    env["VERDICT_HOME"] = str(home)
    if python:
        env["VERDICT_PYTHON"] = python
    env.update(extra or {})
    return subprocess.run(
        [str(RUN_SH), "x"], input=data, capture_output=True, timeout=20, env=env, check=False
    )


def _rows(home: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((home / "events").glob("*.jsonl")) if (home / "events").exists() else []:
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return rows


def _log(home: Path) -> list[dict[str, object]]:
    log = home / "hook.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("python", INTERPRETERS, ids=lambda p: p or "default")
@pytest.mark.parametrize(("fixture", "event"), M1_FIXTURES)
def test_each_fixture_writes_exactly_one_valid_row(
    tmp_path: Path, fixture: str, event: str, python: str | None
) -> None:
    data = (FIXTURES / f"{fixture}.json").read_bytes()
    proc = _run(tmp_path, data, python=python)
    assert proc.returncode == 0
    assert proc.stdout == b""
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["event"] == event
    validate_row(rows[0])
    assert [entry["outcome"] for entry in _log(tmp_path)] == ["ok"]


def test_stop_fixture_also_runs_the_m2_verifier(tmp_path: Path) -> None:
    """The `stop.json` fixture has no prior evidence (no post/post_fail rows
    for its prompt_id), so the G-STOP gate skips the provider entirely: one
    `stop` recorder row plus one `action` row (`action="pass"`,
    `gate_reason="no_evidence"`), and hook.log's outcome is the verifier's
    own outcome, not the recorder's "ok" (task-4-brief.md)."""
    data = (FIXTURES / "stop.json").read_bytes()

    proc = _run(tmp_path, data)

    assert proc.returncode == 0
    assert proc.stdout == b""
    rows = _rows(tmp_path)
    assert [r["event"] for r in rows] == ["stop", "action"]
    for row in rows:
        validate_row(row)
    assert rows[1]["action"] == "pass"
    assert rows[1]["gate_reason"] == "no_evidence"
    assert [entry["outcome"] for entry in _log(tmp_path)] == ["pass"]


def test_pre_tool_use_is_skipped_without_a_row(tmp_path: Path) -> None:
    proc = _run(tmp_path, (FIXTURES / "pre_tool_use_bash.json").read_bytes())
    assert proc.returncode == 0 and proc.stdout == b""
    assert _rows(tmp_path) == []
    assert [entry["outcome"] for entry in _log(tmp_path)] == ["skipped"]


@pytest.mark.parametrize("data", [b"not json", b"", b"[1, 2, 3]", b'"a string"'])
def test_malformed_stdin_exits_zero_silently(tmp_path: Path, data: bytes) -> None:
    proc = _run(tmp_path, data)
    assert proc.returncode == 0 and proc.stdout == b""
    assert _rows(tmp_path) == []
    outcomes = [entry["outcome"] for entry in _log(tmp_path)]
    assert outcomes and outcomes[0] in {"exception", "skipped"}
    for entry in _log(tmp_path):
        assert "Traceback" not in json.dumps(entry)


def test_disable_writes_nothing_at_all(tmp_path: Path) -> None:
    home = tmp_path / "home"
    proc = _run(home, (FIXTURES / "stop.json").read_bytes(), extra={"VERDICT_DISABLE": "1"})
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    assert not home.exists()


def test_sentinel_key_never_reaches_ledger_or_log(tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "post_tool_use_bash.json").read_text())
    payload["tool_response"]["stdout"] = f"token {SENTINEL_KEY} printed"
    broken = dict(payload, tool_input="not a dict")  # makes the recorder raise
    for data in (json.dumps(payload).encode(), json.dumps(broken).encode()):
        _run(tmp_path, data, extra={"CLAUDE_PLUGIN_OPTION_API_KEY": SENTINEL_KEY})
    blob = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert SENTINEL_KEY not in blob


def test_stale_interpreter_file_falls_through_to_python3(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "interpreter").write_text("/nonexistent/python3\n")
    proc = _run(tmp_path, (FIXTURES / "session_end.json").read_bytes())
    assert proc.returncode == 0 and proc.stdout == b""
    assert [r["event"] for r in _rows(tmp_path)] == ["session_end"]


def test_interpreter_file_is_honored_when_valid(tmp_path: Path) -> None:
    python = shutil.which("python3")
    assert python
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "interpreter").write_text(python + "\n")
    proc = _run(tmp_path, (FIXTURES / "session_end.json").read_bytes())
    assert proc.returncode == 0 and len(_rows(tmp_path)) == 1


def test_file_modes_are_private(tmp_path: Path) -> None:
    _run(tmp_path, (FIXTURES / "stop.json").read_bytes())
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "events").stat().st_mode) == 0o700
    for path in [tmp_path / "hook.log", *(tmp_path / "events").glob("*.jsonl")]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_hook_run_never_imports_network_or_dataclasses(tmp_path: Path) -> None:
    env = dict(os.environ, VERDICT_HOME=str(tmp_path))
    proc = subprocess.run(
        [
            shutil.which("python3") or "python3",
            "-S",
            "-X",
            "importtime",
            str(ROOT / "plugin/hooks/verdict_hook.py"),
            "post",
        ],
        input=(FIXTURES / "post_tool_use_bash.json").read_bytes(),
        capture_output=True,
        timeout=20,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    imported = {
        line.rsplit("|", 1)[-1].strip()
        for line in proc.stderr.decode().splitlines()
        if line.startswith("import time:")
    }
    assert not imported & {
        "ssl",
        "_ssl",
        "socket",
        "http.client",
        "dataclasses",
        "verdict_hot.sslctx",
    }


def test_launcher_is_posix_sh_and_executable() -> None:
    assert os.access(RUN_SH, os.X_OK)
    subprocess.run(["sh", "-n", str(RUN_SH)], check=True, timeout=10, stdin=subprocess.DEVNULL)
    code = [
        line
        for line in RUN_SH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    exec_lines = [line for line in code if line.strip().startswith("exec ")]
    assert len(exec_lines) == 1 and " -S " in exec_lines[0] and " -E" not in exec_lines[0]


def test_interpreter_file_tolerates_trailing_whitespace_and_crlf(tmp_path: Path) -> None:
    python = shutil.which("python3")
    assert python
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "interpreter").write_bytes(f"  {python}  \r\n".encode())
    proc = _run(tmp_path, (FIXTURES / "session_end.json").read_bytes())
    assert proc.returncode == 0 and len(_rows(tmp_path)) == 1


def test_relative_interpreter_path_is_ignored(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "interpreter").write_text("bin/python3\n")
    proc = _run(tmp_path, (FIXTURES / "session_end.json").read_bytes())
    assert proc.returncode == 0 and len(_rows(tmp_path)) == 1  # fell through to PATH python3


# --- Final review C2: the advertised `mode` option is honored --------------


@pytest.mark.parametrize("value", ["off", "OFF", "  off  "])
def test_mode_off_writes_nothing_at_all(tmp_path: Path, value: str) -> None:
    """`plugin.json` says `off` "disables recording entirely", so not even
    hook.log is written: the entry point exits before any import."""
    home = tmp_path / "home"
    proc = _run(
        home, (FIXTURES / "stop.json").read_bytes(), extra={"CLAUDE_PLUGIN_OPTION_MODE": value}
    )
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    assert not home.exists()


@pytest.mark.parametrize("value", ["shadow", "enforce"])
def test_mode_shadow_and_enforce_still_record(tmp_path: Path, value: str) -> None:
    proc = _run(
        tmp_path, (FIXTURES / "stop.json").read_bytes(), extra={"CLAUDE_PLUGIN_OPTION_MODE": value}
    )
    assert proc.returncode == 0 and proc.stdout == b""
    assert [r["event"] for r in _rows(tmp_path)] == ["stop", "action"]


def test_policy_mode_off_skips_the_row_but_still_logs(tmp_path: Path) -> None:
    """Second line of defense: `mode` set in ~/.verdict/policy.json. The hook
    itself ran, so hook.log records a `skipped` invocation; no ledger row is
    written."""
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "policy.json").write_text(json.dumps({"mode": "off"}), encoding="utf-8")

    proc = _run(tmp_path, (FIXTURES / "stop.json").read_bytes())

    assert proc.returncode == 0 and proc.stdout == b""
    assert _rows(tmp_path) == []
    assert [entry["outcome"] for entry in _log(tmp_path)] == ["skipped"]


def test_corrupt_user_policy_on_a_stop_payload_still_exits_0(tmp_path: Path) -> None:
    """Fix round 1 item 1 (Critical): a corrupt `~/.verdict/policy.json`
    must never crash the Stop branch and exit non-zero, whether or not the
    fallback to the packaged default happens to recover."""
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "policy.json").write_text("{not valid json at all", encoding="utf-8")

    proc = _run(tmp_path, (FIXTURES / "stop.json").read_bytes())

    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""
