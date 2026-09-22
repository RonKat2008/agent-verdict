"""Tests for verdict_hot.stop (PLAN.md 5.3, 5.4; task-4-brief.md).

No test here ever opens a socket: every scenario that reaches the provider
step supplies a fake `transport` (the same 4-arg callable shape
`provider.py` documents), or points `VERDICT_CASSETTE_DIR` at a temp
directory holding a synthetic cassette. `ledger.append_row`/`read_session`
are exercised for real against an isolated `VERDICT_HOME` (`tmp_path`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from verdict_hot import ledger, stop
from verdict_hot import policy as policy_mod

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
_POLICY = policy_mod.load_policy(PACKAGED_DEFAULT)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    monkeypatch.delenv("VERDICT_CASSETTE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_PROVIDER", raising=False)
    return tmp_path


def _payload(
    session_id: str = "s1",
    prompt_id: str | None = "p1",
    agent_id: str | None = None,
    permission_mode: str = "default",
    background_tasks: list[Any] | None = None,
    stop_hook_active: bool = False,
    last_message: str = "",
    hook_event_name: str = "Stop",
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "prompt_id": prompt_id,
        "agent_id": agent_id,
        "permission_mode": permission_mode,
        "background_tasks": background_tasks or [],
        "stop_hook_active": stop_hook_active,
        "last_assistant_message": last_message,
        "hook_event_name": hook_event_name,
    }


def _seed(session_id: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        ledger.append_row({"schema_v": 1, "session_id": session_id, **row})


def _failing_step(prompt_id: str = "p1", tool_use_id: str = "f1") -> list[dict[str, Any]]:
    return [
        {"event": "prompt", "prompt_id": prompt_id, "prompt_excerpt": "fix it"},
        {
            "event": "post_fail",
            "prompt_id": prompt_id,
            "tool_name": "Bash",
            "tool_use_id": tool_use_id,
            "input_excerpt": "npm test",
            "status": "error",
            "exit_code": 1,
            "is_check": False,
            "never_send": False,
            "error_excerpt": "1 failed",
        },
    ]


_Transport = Callable[[str, str, bytes, dict[str, str]], tuple[int, bytes, float, float]]


def _answer_transport(overrides: dict[str, float] | None = None) -> _Transport:
    overrides = overrides or {}

    def _transport(
        host: str, path: str, body_bytes: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes, float, float]:
        request = json.loads(body_bytes.decode("utf-8"))
        questions = request["questions"]
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
        body = json.dumps(
            {
                "model": "typesafe/jev-1.13-20260917",
                "answers": answers,
                "usage": {"input_tokens": 10},
            }
        ).encode("utf-8")
        return 200, body, 1.0, 2.0

    return _transport


def _http_transport(status: int, calls: list[int]) -> _Transport:
    def _transport(
        host: str, path: str, body_bytes: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes, float, float]:
        calls.append(1)
        return status, b"{}", 1.0, 1.0

    return _transport


# --- Stand-down checks ---------------------------------------------------------


def test_plan_mode_stands_down(home: Path) -> None:
    outcome = stop.handle(_payload(permission_mode="plan"), _POLICY, time.monotonic(), "key")

    assert outcome.stdout_json is None
    assert outcome.outcome == "pass"
    assert outcome.rows[0]["gate_reason"] == "skipped_plan_mode"


def test_background_tasks_stands_down(home: Path) -> None:
    outcome = stop.handle(
        _payload(background_tasks=[{"id": "1"}]), _POLICY, time.monotonic(), "key"
    )

    assert outcome.stdout_json is None
    assert outcome.rows[0]["gate_reason"] == "skipped_background"


def test_guard_budget_spent_stands_down_before_evidence_gathering(home: Path) -> None:
    _seed(
        "s1",
        [
            {
                "event": "action",
                "prompt_id": "p1",
                "agent_id": None,
                "action": "block",
                "reason_hash": "h1",
            }
        ],
    )

    outcome = stop.handle(
        _payload(stop_hook_active=True),
        _POLICY,
        time.monotonic(),
        "key",
        transport=_answer_transport(),
    )

    assert outcome.rows[0]["gate_reason"] == "skipped_guard"
    assert outcome.rows[0]["action"] == "pass"


def test_g_stop_skips_the_provider_when_nothing_needs_judging(home: Path) -> None:
    calls: list[int] = []

    def _boom(*args: Any) -> Any:
        calls.append(1)
        raise AssertionError("provider must never be called")

    outcome = stop.handle(
        _payload(last_message="Done."), _POLICY, time.monotonic(), "key", transport=_boom
    )

    assert calls == []
    assert outcome.outcome == "pass"
    assert outcome.rows[0]["gate_reason"] == "no_evidence"
    assert len(outcome.rows) == 1


def test_always_verify_forces_a_call_despite_no_evidence(home: Path) -> None:
    always_verify_policy = _POLICY._replace(stop=_POLICY.stop._replace(always_verify=True))

    outcome = stop.handle(
        _payload(last_message="Done."),
        always_verify_policy,
        time.monotonic(),
        "key",
        transport=_answer_transport(),
    )

    action_row = outcome.rows[-1]
    assert action_row["gate_reason"] == "always_verify"
    assert any(row["event"] == "verdict" for row in outcome.rows)


def test_local_only_provider_never_builds_a_transport(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_PROVIDER", "local-only")
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(last_message="Done, all tests pass."), _POLICY, time.monotonic(), "key"
    )

    assert outcome.outcome == "pass"
    assert outcome.rows[0]["gate_reason"] == "local_only"


def test_missing_api_key_is_gate_unavailable(home: Path) -> None:
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(last_message="Done, all tests pass."), _POLICY, time.monotonic(), None
    )

    assert outcome.outcome == "gate_unavailable"
    assert outcome.rows[0]["gate_reason"] == "no_key"
    assert outcome.stdout_json is None


# --- Shadow vs enforce output ---------------------------------------------------


def test_shadow_mode_never_prints_even_for_a_block_decision(home: Path) -> None:
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(last_message="All tests pass now."),
        _POLICY,
        time.monotonic(),
        "key",
        transport=_answer_transport({"claims_done": 0.9, "acks_failures": 0.0}),
    )

    assert outcome.stdout_json is None
    action_row = outcome.rows[-1]
    assert action_row["action"] == "pass"
    assert action_row["would_have"] == "block"
    assert action_row["rule_id"] == "R1"


def test_enforce_mode_prints_exactly_the_block_json_for_r1(home: Path) -> None:
    enforce_policy = _POLICY._replace(mode="enforce")
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(last_message="All tests pass now."),
        enforce_policy,
        time.monotonic(),
        "key",
        transport=_answer_transport({"claims_done": 0.9, "acks_failures": 0.0}),
    )

    assert outcome.outcome == "block"
    assert outcome.stdout_json is not None
    parsed = json.loads(outcome.stdout_json)
    assert set(parsed) == {"decision", "reason"}
    assert parsed["decision"] == "block"
    assert "Rule R1" in parsed["reason"]
    assert outcome.stdout_json.endswith("\n")
    assert outcome.stdout_json.startswith('{"decision":"block","reason":"')


def test_enforce_mode_prints_exactly_the_flag_json_for_r4(home: Path) -> None:
    enforce_policy = _POLICY._replace(mode="enforce")

    outcome = stop.handle(
        _payload(last_message="I implemented the health check endpoint."),
        enforce_policy,
        time.monotonic(),
        "key",
        transport=_answer_transport({"claim_c1": 0.1}),
    )

    assert outcome.outcome == "flag"
    assert outcome.stdout_json is not None
    parsed = json.loads(outcome.stdout_json)
    assert set(parsed) == {"systemMessage"}
    assert parsed["systemMessage"].startswith("Verdict: ")
    assert "Rule R4" in parsed["systemMessage"]


def test_enforce_mode_second_block_with_same_rule_is_denied_by_the_guard(home: Path) -> None:
    enforce_policy = _POLICY._replace(mode="enforce")
    _seed("s1", _failing_step())
    transport = _answer_transport({"claims_done": 0.9, "acks_failures": 0.0})

    first = stop.handle(
        _payload(last_message="All tests pass now."),
        enforce_policy,
        time.monotonic(),
        "key",
        transport=transport,
    )
    assert first.outcome == "block"

    second = stop.handle(
        _payload(last_message="All tests pass now."),
        enforce_policy,
        time.monotonic(),
        "key",
        transport=transport,
    )

    assert second.outcome == "pass"
    assert second.rows[-1]["would_have"] == "block"
    assert second.rows[-1]["gate_reason"] == "guard_duplicate_reason"


def test_subagent_stop_never_blocks_when_subagent_block_is_false(home: Path) -> None:
    enforce_policy = _POLICY._replace(mode="enforce")
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(
            agent_id="agent-1", last_message="All tests pass now.", hook_event_name="SubagentStop"
        ),
        enforce_policy,
        time.monotonic(),
        "key",
        transport=_answer_transport({"claims_done": 0.9, "acks_failures": 0.0}),
    )

    assert outcome.outcome == "pass"
    assert outcome.stdout_json is None
    assert outcome.rows[-1]["would_have"] == "block"


def test_subagent_stop_blocks_when_subagent_block_is_true(home: Path) -> None:
    enforce_policy = _POLICY._replace(
        mode="enforce", stop=_POLICY.stop._replace(subagent_block=True)
    )
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(
            agent_id="agent-1", last_message="All tests pass now.", hook_event_name="SubagentStop"
        ),
        enforce_policy,
        time.monotonic(),
        "key",
        transport=_answer_transport({"claims_done": 0.9, "acks_failures": 0.0}),
    )

    assert outcome.outcome == "block"
    assert outcome.stdout_json is not None


# --- Provider failure and retry --------------------------------------------------


def test_provider_failure_is_gate_unavailable_with_no_stdout(home: Path) -> None:
    _seed("s1", _failing_step())
    calls: list[int] = []

    outcome = stop.handle(
        _payload(last_message="All tests pass now."),
        _POLICY,
        time.monotonic(),
        "key",
        transport=_http_transport(400, calls),
    )

    assert outcome.outcome == "gate_unavailable"
    assert outcome.stdout_json is None
    assert outcome.rows[0]["gate_reason"] == "provider_http_400"
    assert len(calls) == 1  # never retried on a 4xx


def test_retryable_failure_is_retried_once_then_succeeds(home: Path) -> None:
    _seed("s1", _failing_step())
    attempts: list[int] = []

    def _flaky(
        host: str, path: str, body_bytes: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes, float, float]:
        attempts.append(1)
        if len(attempts) == 1:
            return 503, b"{}", 1.0, 1.0
        return _answer_transport()(host, path, body_bytes, headers)

    outcome = stop.handle(
        _payload(last_message="All tests pass now."),
        _POLICY,
        time.monotonic(),
        "key",
        transport=_flaky,
    )

    assert len(attempts) == 2
    assert outcome.outcome == "pass"  # answers were all 0.0 -- no rule fires


def test_missing_cassette_is_gate_unavailable(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    monkeypatch.setenv("VERDICT_CASSETTE_DIR", str(cassette_dir))
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(last_message="All tests pass now."), _POLICY, time.monotonic(), "key"
    )

    assert outcome.outcome == "gate_unavailable"
    assert outcome.rows[0]["gate_reason"] == "cassette_missing"
    assert outcome.stdout_json is None


def test_sentinel_api_key_never_appears_in_any_row(home: Path) -> None:
    sentinel = "sentinel-key-should-never-leak-ABC123"
    _seed("s1", _failing_step())

    outcome = stop.handle(
        _payload(last_message="All tests pass now."),
        _POLICY,
        time.monotonic(),
        sentinel,
        transport=_answer_transport({"claims_done": 0.9, "acks_failures": 0.0}),
    )

    blob = json.dumps(outcome.rows)
    assert sentinel not in blob
    assert outcome.stdout_json is None or sentinel not in outcome.stdout_json


# --- Budget ----------------------------------------------------------------------


@pytest.mark.slow
def test_budget_a_sleeping_transport_still_yields_gate_unavailable_within_budget(
    home: Path,
) -> None:
    def _slow(
        host: str, path: str, body_bytes: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes, float, float]:
        time.sleep(3.0)
        return 200, b"{}", 1.0, 1.0

    _seed("s1", _failing_step())
    start = time.monotonic()

    outcome = stop.handle(
        _payload(last_message="All tests pass now."), _POLICY, start, "key", transport=_slow
    )

    elapsed = time.monotonic() - start
    assert elapsed < 2.6
    assert outcome.outcome == "gate_unavailable"
    assert outcome.stdout_json is None


def test_handle_never_raises_on_a_completely_malformed_payload(home: Path) -> None:
    outcome = stop.handle({}, _POLICY, time.monotonic(), "key")

    assert outcome.outcome == "gate_unavailable"
    assert outcome.stdout_json is None
