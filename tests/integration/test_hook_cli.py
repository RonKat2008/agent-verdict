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
    ("stop", "stop"),
    ("session_end", "session_end"),
]
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
    proc = _run(tmp_path, (FIXTURES / "stop.json").read_bytes())
    assert proc.returncode == 0 and proc.stdout == b""
    assert [r["event"] for r in _rows(tmp_path)] == ["stop"]


def test_interpreter_file_is_honored_when_valid(tmp_path: Path) -> None:
    python = shutil.which("python3")
    assert python
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "interpreter").write_text(python + "\n")
    proc = _run(tmp_path, (FIXTURES / "stop.json").read_bytes())
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
