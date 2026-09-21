from __future__ import annotations

import pytest
from verdict_hot.policy import Policy


@pytest.mark.parametrize(
    "file_path",
    [
        ".env",
        "config/.env.local",
        "~/.ssh/id_ed25519",
        "server.pem",
    ],
)
def test_is_never_send_catches_credential_file_paths(
    default_policy: Policy, file_path: str
) -> None:
    from verdict_hot import gates

    assert gates.is_never_send("Write", {"file_path": file_path}, default_policy) is True


@pytest.mark.parametrize(
    "file_path",
    [
        "src/environment.py",
        "README.md",
    ],
)
def test_is_never_send_does_not_catch_ordinary_files(
    default_policy: Policy, file_path: str
) -> None:
    from verdict_hot import gates

    assert gates.is_never_send("Write", {"file_path": file_path}, default_policy) is False


def test_is_never_send_catches_bash_command_printing_a_credential_file(
    default_policy: Policy,
) -> None:
    from verdict_hot import gates

    assert gates.is_never_send("Bash", {"command": "cat .env"}, default_policy) is True


def test_is_never_send_false_for_ordinary_bash_command(default_policy: Policy) -> None:
    from verdict_hot import gates

    assert gates.is_never_send("Bash", {"command": "ls -la"}, default_policy) is False


def test_is_never_send_false_when_neither_field_present(default_policy: Policy) -> None:
    from verdict_hot import gates

    assert gates.is_never_send("Read", {}, default_policy) is False


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "npm test",
        "cargo test",
        "make check",
        "uv run mypy",
        "cd app && pytest -q",
        "python -m pytest",
        "sudo npm test",
        "FOO=bar pytest",
    ],
)
def test_is_check_recognizes_runner_commands(default_policy: Policy, command: str) -> None:
    from verdict_hot import gates

    assert gates.is_check(command, default_policy) is True


@pytest.mark.parametrize(
    "command",
    [
        "echo pytest",
        "echo 'run make check later'",
        "ls -la",
    ],
)
def test_is_check_rejects_non_leading_runner_mentions(default_policy: Policy, command: str) -> None:
    from verdict_hot import gates

    assert gates.is_check(command, default_policy) is False


def test_is_check_honors_checks_extra(default_policy: Policy) -> None:
    from dataclasses import replace

    from verdict_hot import gates

    extended = replace(default_policy, checks=replace(default_policy.checks, extra=("^biome\\b",)))

    assert gates.is_check("biome check .", extended) is True
    assert gates.is_check("biome check .", default_policy) is False


@pytest.mark.parametrize(
    ("tool_name", "command", "output"),
    [
        ("Bash", "pytest -q", "3 failed, 12 passed"),
        ("Bash", "python app.py", "Traceback (most recent call last):\n  File x"),
        ("Bash", "make check || true", ""),
        ("Bash", "npm test | tail -5", "ok"),
        ("WebFetch", None, 'HTTP/1.1 500 Internal Server Error\n{"error": true}'),
    ],
)
def test_is_soft_fail_candidate_true_cases(
    default_policy: Policy, tool_name: str, command: str | None, output: str
) -> None:
    from verdict_hot import gates

    assert gates.is_soft_fail_candidate(tool_name, command, output, default_policy) is True


def test_is_soft_fail_candidate_false_for_plain_ok_output(default_policy: Policy) -> None:
    from verdict_hot import gates

    assert gates.is_soft_fail_candidate("Bash", "pytest -q", "ok", default_policy) is False


def test_is_soft_fail_candidate_false_outside_soft_failure_tools(default_policy: Policy) -> None:
    from verdict_hot import gates

    traceback_output = "Traceback (most recent call last):"
    assert gates.is_soft_fail_candidate("Write", None, traceback_output, default_policy) is False
    assert gates.is_soft_fail_candidate("Edit", None, "3 failed, 1 passed", default_policy) is False
