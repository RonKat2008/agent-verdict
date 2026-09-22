"""Walk back through a session's prompts to bound one Stop's verification
span (split from `span.py`, task-7-brief.md cleanup item 5, to keep
`span.py` under the 400-line file cap).

`span.build_span` imports `_walk_back` lazily, inside the function body,
never at module level -- this module imports several private names back
from `span.py` at ITS OWN module level, so a module-level import the other
way (`span.py` importing this module at the top) would form a load-time
cycle. The lazy import defers this module's load until `span.py` is
already fully initialized, exactly like `span.py`'s own lazy
`from .gates import _strip_leading`.
"""

from __future__ import annotations

from collections.abc import Mapping

from .span import (
    _COUNTERFACTUAL_DECISIONS,
    _GUARD_GATE_PREFIX,
    STAND_DOWN_GATE_REASONS,
    _prompt_id_of,
)


def _is_clean_stop_row(row: Mapping[str, object]) -> bool:
    """A clean stop is a *verified* pass: the verifier ran (no stand-down or
    guard demotion), would not have blocked or flagged in enforce mode (fix
    rounds 2 and 3: shadow records `would_have` for every decision, and only
    a counterfactual block or flag disqualifies), and left no open
    failures."""
    if row.get("event") != "action" or row.get("action") != "pass":
        return False
    gate_reason = row.get("gate_reason")
    if gate_reason in STAND_DOWN_GATE_REASONS:
        return False
    if isinstance(gate_reason, str) and gate_reason.startswith(_GUARD_GATE_PREFIX):
        return False
    if row.get("would_have") in _COUNTERFACTUAL_DECISIONS:
        return False
    return not row.get("open_failures")


def _has_clean_stop(block_rows: list[Mapping[str, object]]) -> bool:
    return any(_is_clean_stop_row(row) for row in block_rows)


def _clear_between(
    clear_indices: list[int],
    last_idx: Mapping[str, int],
    first_idx: Mapping[str, int],
    older_prompt: str,
    newer_prompt: str,
) -> bool:
    if older_prompt not in last_idx or newer_prompt not in first_idx:
        return False
    lo, hi = last_idx[older_prompt], first_idx[newer_prompt]
    return any(lo < idx < hi for idx in clear_indices)


def _prompt_index(
    safe_rows: list[Mapping[str, object]],
) -> tuple[list[str], dict[str, int], dict[str, int], list[int]]:
    """(seen, first_idx, last_idx, clear_indices) for `_walk_back`."""
    seen: list[str] = []
    first_idx: dict[str, int] = {}
    last_idx: dict[str, int] = {}
    clear_indices: list[int] = []
    for idx, row in enumerate(safe_rows):
        pid = _prompt_id_of(row)
        if pid is not None:
            if pid not in first_idx:
                first_idx[pid] = idx
                seen.append(pid)
            last_idx[pid] = idx
        if row.get("event") == "session_start" and row.get("source") == "clear":
            clear_indices.append(idx)
    return seen, first_idx, last_idx, clear_indices


def _walk_back(
    safe_rows: list[Mapping[str, object]], current_prompt_id: str, max_prompts: int
) -> tuple[list[str], str] | None:
    """Returns (included prompt ids, most-recent-first, stop reason), or
    `None` when `current_prompt_id` names no row in `safe_rows` at all."""
    seen, first_idx, last_idx, clear_indices = _prompt_index(safe_rows)

    if current_prompt_id not in first_idx:
        return None

    included = [current_prompt_id]
    idx = seen.index(current_prompt_id)
    reason = "start_of_session"
    while True:
        if len(included) >= max_prompts:
            reason = "max_prompts"
            break
        if idx == 0:
            reason = "start_of_session"
            break
        prev_pid = seen[idx - 1]
        if _clear_between(clear_indices, last_idx, first_idx, prev_pid, included[-1]):
            reason = "clear"
            break
        # Fix round 1 (Critical): a clean stop is a BOUNDARY, not a member.
        # Prompts at or before the most recent clean stop were already
        # judged and passed, so the candidate older block is checked for a
        # clean stop *before* it is added -- if it has one, the walk stops
        # here and that block (and everything before it) is excluded
        # entirely, never re-litigated. The current prompt (`included[0]`)
        # is never subject to this check: it is always a member.
        candidate_rows = [r for r in safe_rows if _prompt_id_of(r) == prev_pid]
        if _has_clean_stop(candidate_rows):
            reason = "clean_stop"
            break
        included.append(prev_pid)
        idx -= 1

    return included, reason


__all__ = ["_walk_back"]
