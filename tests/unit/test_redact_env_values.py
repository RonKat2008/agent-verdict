"""The redactor must remove the process's own configured secrets by exact value,
whatever their format, so an echoed API key never reaches the ledger."""

from __future__ import annotations

import pytest
from verdict_hot import redact

ODD_FORMAT_KEY = "my-odd-format-key-QWERTY-1234567890-zzzz"  # 40 chars


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


def test_longest_env_value_wins_when_one_is_a_prefix_of_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_A", "secretvalue-1234567")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_B", "secretvalue-1234567890")
    out, _ = redact.redact("prefix secretvalue-1234567890 suffix")
    assert "secretvalue-1234567890" not in out and "890" not in out
    assert out == "prefix [REDACTED:env-configured-secret] suffix"


def test_overlapping_env_values_are_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_A", "abcdefghijklmnopqr")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_B", "mnopqrstuvwxyz0123")
    out, _ = redact.redact("x abcdefghijklmnopqrstuvwxyz0123 y")
    assert (
        "abcdefghijklmnopqr" not in out
        and "mnopqrstuvwxyz0123" not in out
        and "stuvwxyz0123" not in out
    )


def test_env_value_with_regex_metacharacters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "a.b*c(d)[e]+f?g|h^i$jklm")
    out, hits = redact.redact("key=a.b*c(d)[e]+f?g|h^i$jklm done")
    assert "a.b*c(d)[e]+f?g|h^i$jklm" not in out and hits == 1


def test_values_shorter_than_16_chars_are_never_env_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_PROVIDER", "openrouter-main")
    out, hits = redact.redact("using openrouter-main today")
    assert out == "using openrouter-main today" and hits == 0
