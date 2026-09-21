from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest
from _logsafe_concurrency_worker import write_log_lines
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from verdict_hot import logsafe, paths


def test_scrub_redacts_env_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", "supersecretvalue123")
    text = "leaked: supersecretvalue123 end"
    assert logsafe.scrub(text) == "leaked: <redacted> end"


def test_scrub_ignores_short_env_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "short")
    text = "value: short end"
    assert logsafe.scrub(text) == text


def test_scrub_ignores_empty_env_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    text = "nothing to see here"
    assert logsafe.scrub(text) == text


def test_scrub_redacts_authorization_header() -> None:
    text = "Authorization: Bearer abc123def456"
    assert logsafe.scrub(text) == "Authorization: <redacted>"


def test_scrub_authorization_header_is_bounded_not_greedy_to_end_of_line() -> None:
    token = "sk-or-v1-" + "a" * 40
    text = f"Authorization: Bearer {token} trailing"
    result = logsafe.scrub(text)
    assert token not in result
    assert result == "Authorization: <redacted> trailing"


def test_scrub_redacts_sk_token() -> None:
    text = "key=sk-or-v1-abcdEFGH12345678zzzz done"
    result = logsafe.scrub(text)
    assert "sk-or-v1" not in result
    assert "<redacted>" in result


def test_scrub_leaves_ordinary_text_alone() -> None:
    text = "hello world, nothing secret here, sk is short"
    assert logsafe.scrub(text) == text


def test_log_invocation_writes_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    logsafe.log_invocation("post", "s1", "ok", 12.5)
    content = paths.hook_log().read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event"] == "post"
    assert row["session_id"] == "s1"
    assert row["outcome"] == "ok"
    assert row["schema_v"] == 1
    assert row["err_class"] is None
    assert row["total_ms"] == 12.5


def test_log_invocation_accepts_extra_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    logsafe.log_invocation(
        "post", "s1", "exception", 3.0, err_class="ValueError", extra={"conn_ms": 1.0}
    )
    row = json.loads(paths.hook_log().read_text(encoding="utf-8").splitlines()[0])
    assert row["err_class"] == "ValueError"
    assert row["conn_ms"] == 1.0


def test_log_invocation_never_raises_when_home_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("VERDICT_HOME", str(blocker))
    logsafe.log_invocation("post", "s1", "ok", 1.0)  # must not raise


def test_log_invocation_rotates_above_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    monkeypatch.setattr(logsafe, "_MAX_LOG_BYTES", 200)
    for _ in range(30):
        logsafe.log_invocation("post", "s1", "ok", 1.0)
    log_path = paths.hook_log()
    rotated = log_path.with_name(log_path.name + ".1")
    assert rotated.exists()
    assert log_path.exists()


def test_install_excepthook_logs_only_class_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    original_hook = sys.excepthook
    try:
        logsafe.install_excepthook()
        try:
            raise ValueError("secret-detail-should-not-appear")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
        assert exc_type is not None
        assert exc_value is not None
        sys.excepthook(exc_type, exc_value, exc_tb)
    finally:
        sys.excepthook = original_hook
    content = paths.hook_log().read_text(encoding="utf-8")
    assert "ValueError" in content
    assert "secret-detail-should-not-appear" not in content


def _split_on_literal_newline(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def test_log_invocation_scrubs_extra_before_serializing_not_the_json_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the critical bug: scrubbing the already-serialized JSON line
    let a long Authorization value eat the closing quote/brace and corrupt
    the line. Scrubbing must happen on the Python value before json.dumps."""
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    token = "sk-or-v1-" + "a" * 40
    logsafe.log_invocation(
        "post", "s1", "ok", 1.0, extra={"note": f"Authorization: Bearer {token} trailing"}
    )
    lines = _split_on_literal_newline(paths.hook_log().read_text(encoding="utf-8"))
    assert len(lines) == 1
    row = json.loads(lines[0])  # must not raise: the line must stay valid JSON
    assert token not in json.dumps(row)
    assert row["note"] == "Authorization: <redacted> trailing"


def test_log_invocation_scrubs_nested_dicts_and_lists_in_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    logsafe.log_invocation(
        "post",
        "s1",
        "ok",
        1.0,
        extra={"nested": {"headers": ["Authorization: Bearer abcdefgh12345678"]}},
    )
    lines = _split_on_literal_newline(paths.hook_log().read_text(encoding="utf-8"))
    row = json.loads(lines[0])
    assert row["nested"]["headers"] == ["Authorization: <redacted>"]


@settings(
    max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)
@given(text=st.text())
def test_log_invocation_arbitrary_extra_text_keeps_every_hook_log_line_valid_json(
    text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    logsafe.log_invocation("post", "s1", "ok", 1.0, extra={"note": text})
    content = paths.hook_log().read_text(encoding="utf-8")
    for line in _split_on_literal_newline(content):
        json.loads(line)  # must not raise for any prior or current example


@pytest.mark.slow
def test_8_processes_rotate_and_append_without_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = str(tmp_path)
    n_procs = 8
    per_proc = 200
    max_log_bytes = 2000  # small on purpose: forces many rotations

    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=write_log_lines, args=(home, per_proc, pid, max_log_bytes))
        for pid in range(n_procs)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
        assert p.exitcode == 0  # no exception escaped a child

    monkeypatch.setenv("VERDICT_HOME", home)
    log_path = paths.hook_log()
    rotated_path = log_path.with_name(log_path.name + ".1")

    all_lines: list[str] = []
    for path in (log_path, rotated_path):
        if path.exists():
            all_lines.extend(_split_on_literal_newline(path.read_text(encoding="utf-8")))

    total_written = n_procs * per_proc
    assert 0 < len(all_lines) <= total_written  # losing old history beyond one backup is OK
    for line in all_lines:
        json.loads(line)  # must not raise: no corrupt or interleaved line
