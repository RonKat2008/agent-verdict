"""Loop guard over `action` rows (PLAN.md 5.4, D-015, task-4-brief.md).

State is derived entirely from `action` rows already on the ledger for one
`(session_id, prompt_id, agent_id)` key (`None` is a valid value for
`prompt_id`/`agent_id`): how many blocks have been issued, and which
`reason_hash` values have already been used. At most
`min(policy.stop.max_blocks_per_prompt, policy.stop.ceiling)` blocks, and
never the same `reason_hash` twice -- this stays far below Claude Code's
own 8-consecutive-block turn cap (A8).

`blocks_issued` is the cheap pre-evidence count `stop.py`'s stand-down
check uses (D-015: "stop_hook_active and the budget is spent") -- it runs
before a `reason_hash` exists at all, since that requires a rule to have
already fired. `check` is the authoritative, later gate: it also refuses a
repeated `reason_hash` regardless of the raw count.

Both functions are pure: no I/O, no clock. `stop.py` supplies `action_rows`
(already read from the ledger) and never mutates them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from .policy import Policy


class GuardResult(NamedTuple):
    allowed: bool
    blocks_issued: int
    reason: str


def _matches_key(
    row: Mapping[str, object], session_id: str, prompt_id: str | None, agent_id: str | None
) -> bool:
    return (
        row.get("event") == "action"
        and row.get("action") == "block"
        and row.get("session_id") == session_id
        and row.get("prompt_id") == prompt_id
        and row.get("agent_id") == agent_id
    )


def _count(
    action_rows: Sequence[Mapping[str, object]],
    session_id: str,
    prompt_id: str | None,
    agent_id: str | None,
) -> tuple[int, frozenset[str]]:
    blocks = 0
    hashes: set[str] = set()
    for row in action_rows:
        if not isinstance(row, Mapping) or not _matches_key(row, session_id, prompt_id, agent_id):
            continue
        blocks += 1
        reason_hash = row.get("reason_hash")
        if isinstance(reason_hash, str):
            hashes.add(reason_hash)
    return blocks, frozenset(hashes)


def limit(policy: Policy) -> int:
    """The effective block budget (fix round 1 item 8): `stop.py`'s
    pre-evidence stand-down check and `check`'s authoritative admission
    decision must agree on this number, so both call this instead of each
    computing `min(max_blocks_per_prompt, ceiling)` on its own."""
    return min(policy.stop.max_blocks_per_prompt, policy.stop.ceiling)


def blocks_issued(
    action_rows: Sequence[Mapping[str, object]],
    session_id: str,
    prompt_id: str | None,
    agent_id: str | None,
) -> int:
    """Count blocks already issued for this key. Used for the cheap
    stand-down check that runs before any evidence has been gathered."""
    count, _ = _count(action_rows, session_id, prompt_id, agent_id)
    return count


def check(
    action_rows: Sequence[Mapping[str, object]],
    session_id: str,
    prompt_id: str | None,
    agent_id: str | None,
    reason_hash: str,
    policy: Policy,
) -> GuardResult:
    """Authoritative admission check for a real block about to be issued."""
    count, hashes = _count(action_rows, session_id, prompt_id, agent_id)
    if reason_hash in hashes:
        return GuardResult(False, count, "duplicate_reason")
    if count >= limit(policy):
        return GuardResult(False, count, "budget_spent")
    return GuardResult(True, count, "ok")


__all__ = ["GuardResult", "blocks_issued", "check", "limit"]
