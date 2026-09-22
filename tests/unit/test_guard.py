"""Tests for verdict_hot.guard (PLAN.md 5.4, D-015, task-4-brief.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from verdict_hot import guard
from verdict_hot import policy as policy_mod

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
_POLICY = policy_mod.load_policy(PACKAGED_DEFAULT)


def _block_row(
    session_id: str = "s1",
    prompt_id: str | None = "p1",
    agent_id: str | None = None,
    reason_hash: str = "hash-a",
) -> dict[str, Any]:
    return {
        "event": "action",
        "session_id": session_id,
        "prompt_id": prompt_id,
        "agent_id": agent_id,
        "action": "block",
        "reason_hash": reason_hash,
    }


def test_first_block_is_allowed_with_empty_history() -> None:
    result = guard.check([], "s1", "p1", None, "hash-a", _POLICY)

    assert result.allowed is True
    assert result.blocks_issued == 0


def test_default_max_blocks_per_prompt_is_one() -> None:
    rows = [_block_row(reason_hash="hash-a")]

    result = guard.check(rows, "s1", "p1", None, "hash-b", _POLICY)

    assert result.allowed is False
    assert result.blocks_issued == 1
    assert result.reason == "budget_spent"


def test_never_the_same_reason_hash_twice_even_under_the_limit() -> None:
    rows = [_block_row(reason_hash="hash-a")]
    custom = _POLICY._replace(stop=_POLICY.stop._replace(max_blocks_per_prompt=5, ceiling=5))

    result = guard.check(rows, "s1", "p1", None, "hash-a", custom)

    assert result.allowed is False
    assert result.reason == "duplicate_reason"


def test_a_different_reason_hash_is_allowed_up_to_the_configured_limit() -> None:
    rows = [_block_row(reason_hash="hash-a")]
    custom = _POLICY._replace(stop=_POLICY.stop._replace(max_blocks_per_prompt=2, ceiling=2))

    result = guard.check(rows, "s1", "p1", None, "hash-b", custom)

    assert result.allowed is True
    assert result.blocks_issued == 1


def test_ceiling_caps_a_user_override_that_raises_max_blocks_per_prompt() -> None:
    rows = [_block_row(reason_hash="hash-a"), _block_row(reason_hash="hash-b")]
    custom = _POLICY._replace(stop=_POLICY.stop._replace(max_blocks_per_prompt=10, ceiling=2))

    result = guard.check(rows, "s1", "p1", None, "hash-c", custom)

    assert result.allowed is False
    assert result.reason == "budget_spent"


def test_key_is_scoped_to_session_prompt_and_agent() -> None:
    rows = [
        _block_row(session_id="s1", prompt_id="p1", agent_id=None, reason_hash="hash-a"),
        _block_row(session_id="s1", prompt_id="p2", agent_id=None, reason_hash="hash-a"),
        _block_row(session_id="s2", prompt_id="p1", agent_id=None, reason_hash="hash-a"),
        _block_row(session_id="s1", prompt_id="p1", agent_id="agent-1", reason_hash="hash-a"),
    ]

    custom = _POLICY._replace(stop=_POLICY.stop._replace(max_blocks_per_prompt=2, ceiling=2))
    result = guard.check(rows, "s1", "p1", None, "hash-b", custom)

    assert result.allowed is True
    assert result.blocks_issued == 1  # only the first row matches the (s1, p1, None) key


def test_none_prompt_id_and_agent_id_are_valid_key_values() -> None:
    rows = [_block_row(prompt_id=None, agent_id=None, reason_hash="hash-a")]

    result = guard.check(rows, "s1", None, None, "hash-a", _POLICY)

    assert result.allowed is False
    assert result.reason == "duplicate_reason"


def test_non_block_action_rows_never_count_toward_the_budget() -> None:
    rows = [
        {
            "event": "action",
            "session_id": "s1",
            "prompt_id": "p1",
            "agent_id": None,
            "action": "pass",
        },
        {
            "event": "action",
            "session_id": "s1",
            "prompt_id": "p1",
            "agent_id": None,
            "action": "flag",
        },
        {
            "event": "action",
            "session_id": "s1",
            "prompt_id": "p1",
            "agent_id": None,
            "action": "gate_unavailable",
        },
    ]

    result = guard.check(rows, "s1", "p1", None, "hash-a", _POLICY)

    assert result.allowed is True
    assert result.blocks_issued == 0


def test_non_action_event_rows_are_ignored() -> None:
    rows: list[Any] = [
        {
            "event": "verdict",
            "session_id": "s1",
            "prompt_id": "p1",
            "agent_id": None,
            "action": "block",
        },
        "not a mapping",
        42,
    ]

    result = guard.check(rows, "s1", "p1", None, "hash-a", _POLICY)

    assert result.allowed is True
    assert result.blocks_issued == 0


def test_blocks_issued_matches_check_without_needing_a_reason_hash() -> None:
    rows = [_block_row(reason_hash="hash-a"), _block_row(reason_hash="hash-b")]

    count = guard.blocks_issued(rows, "s1", "p1", None)

    assert count == 2
