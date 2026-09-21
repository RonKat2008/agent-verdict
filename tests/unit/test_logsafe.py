from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
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
