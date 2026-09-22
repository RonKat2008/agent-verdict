"""The redactor must remove the process's own configured secrets by exact value,
whatever their format, so an echoed API key never reaches the ledger."""

from __future__ import annotations

import pytest
from verdict_hot import redact

ODD_FORMAT_KEY = "my-odd-format-key-QWERTY-1234567890-zzzz"


def test_env_configured_secret_is_redacted_by_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", ODD_FORMAT_KEY)
    out, hits = redact.redact(f"token {ODD_FORMAT_KEY} printed")
    assert ODD_FORMAT_KEY not in out
    assert hits >= 1
    assert "[REDACTED:env-configured-secret]" in out


@pytest.mark.parametrize("name", ["OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"])
def test_provider_key_env_vars_are_redacted_by_value(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, ODD_FORMAT_KEY)
    out, _ = redact.redact(f"export {name}={ODD_FORMAT_KEY}")
    assert ODD_FORMAT_KEY not in out


def test_short_env_values_are_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MODE", "shadow")
    out, hits = redact.redact("running in shadow mode")
    assert out == "running in shadow mode" and hits == 0


def test_env_value_redaction_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", ODD_FORMAT_KEY)
    once, _ = redact.redact(f"k={ODD_FORMAT_KEY}")
    twice, hits = redact.redact(once)
    assert once == twice and hits == 0
