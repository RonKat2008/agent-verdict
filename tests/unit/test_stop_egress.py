"""G2.5: every outbound provider body has passed through the M1 redactor.

Transport-patching test (PLAN 7, M2 gates; D-017 "one redact(), three call
sites: provider request, ledger write, export"). The final message reaches
`stop.handle` raw from the Stop payload, so a secret the assistant echoed
must be redacted *before* it is placed in the state sent to the provider,
and an env-configured key must be redacted by value the same way the
ledger path does it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from verdict_hot import ledger, stop
from verdict_hot import policy as policy_mod

ROOT = Path(__file__).resolve().parents[2]
_POLICY = policy_mod.load_policy(ROOT / "plugin" / "policies" / "default.json")

# A synthetic token shaped like a real provider key so the pattern rules hit.
_ECHOED_SECRET = "sk-or-v1-" + "f" * 64
_ENV_SECRET = "env-sentinel-value-that-is-long-enough-XYZ987654321"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    monkeypatch.delenv("VERDICT_CASSETTE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_PROVIDER", raising=False)
    return tmp_path


def _payload(last_message: str) -> dict[str, Any]:
    return {
        "session_id": "s1",
        "prompt_id": "p1",
        "agent_id": None,
        "permission_mode": "default",
        "background_tasks": [],
        "stop_hook_active": False,
        "last_assistant_message": last_message,
        "hook_event_name": "Stop",
    }


def _seed_failing_step() -> None:
    rows: list[dict[str, Any]] = [
        {"event": "prompt", "prompt_id": "p1", "prompt_excerpt": "run the tests"},
        {
            "event": "post_fail",
            "prompt_id": "p1",
            "tool_name": "Bash",
            "tool_use_id": "f1",
            "input_excerpt": "pytest",
            "status": "error",
            "exit_code": 1,
            "is_check": True,
            "soft_fail_candidate": False,
            "never_send": False,
            "error_excerpt": "1 failed",
        },
    ]
    for row in rows:
        ledger.append_row({"schema_v": 1, "session_id": "s1", **row})


def _capturing_transport(bodies: list[bytes]) -> Any:
    def _transport(
        host: str, path: str, body_bytes: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes, float, float]:
        bodies.append(body_bytes)
        request = json.loads(body_bytes.decode("utf-8"))
        answers = {
            key: {"type": q["type"], "noul": 0.5}
            if q["type"] == "noul"
            else {"type": "score", "score": 0.5}
            for key, q in request["questions"].items()
        }
        body = json.dumps({"model": "typesafe/jev-1.13-20260917", "answers": answers})
        return 200, body.encode("utf-8"), 1.0, 2.0

    return _transport


def test_an_echoed_secret_in_the_final_message_never_reaches_the_provider(home: Path) -> None:
    _seed_failing_step()
    bodies: list[bytes] = []
    message = f"All tests pass. For reference the key is {_ECHOED_SECRET} and it works."

    outcome = stop.handle(
        _payload(message),
        _POLICY,
        time.monotonic(),
        "k" * 40,
        transport=_capturing_transport(bodies),
    )

    assert bodies, "the provider was not called; the test proves nothing"
    assert outcome.outcome != "gate_unavailable"
    for body in bodies:
        assert _ECHOED_SECRET.encode() not in body
        assert _ECHOED_SECRET not in json.dumps(outcome.rows)


def test_an_env_configured_secret_is_redacted_by_value_before_egress(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", _ENV_SECRET)
    _seed_failing_step()
    bodies: list[bytes] = []
    message = f"Deployed with token {_ENV_SECRET}; everything passes."

    stop.handle(
        _payload(message),
        _POLICY,
        time.monotonic(),
        "k" * 40,
        transport=_capturing_transport(bodies),
    )

    assert bodies
    for body in bodies:
        assert _ENV_SECRET.encode() not in body


def test_redactor_failure_sends_a_marker_not_the_raw_message(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from verdict_hot import redact

    monkeypatch.setattr(redact, "redact", lambda text: ("", -1))
    _seed_failing_step()
    bodies: list[bytes] = []
    raw = "All tests pass with the unique phrase QUOKKA-PLATYPUS-9931."

    stop.handle(
        _payload(raw),
        _POLICY,
        time.monotonic(),
        "k" * 40,
        transport=_capturing_transport(bodies),
    )

    for body in bodies:
        assert b"QUOKKA-PLATYPUS-9931" not in body
