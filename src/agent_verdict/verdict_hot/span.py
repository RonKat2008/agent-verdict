"""Verification span construction from ledger rows (PLAN.md 5.3, D-020).

`build_span` is pure: no I/O, no clock, and it never mutates `rows` or
`policy`. It turns one session's ledger rows (plain dicts in file order,
as `ledger.append_row` wrote them) into a `Span` -- the evidence Stop's
completion verifier reasons about (state.py, Task 2, shapes this further
into the provider request).

Row shapes consumed. Four of the six M1 event types
(schemas/ledger-v1.json) matter here, plus two Task 4 introduces -- this
module already understands their documented shape (task-1-brief.md,
docs/PLAN.md 5.3's Task 4 interface) so Task 4's recorder writes exactly
these fields and nothing else:

- `post` / `post_fail` (plugin/hooks/verdict_hot/recorders.py): `tool_name`,
  `tool_use_id`, `input_excerpt`, `status` (`"ok"` on every `post` row,
  `"error"` on every `post_fail` row), `is_check`, `soft_fail_candidate`
  (`post` only -- always false-equivalent on `post_fail`), `never_send`,
  `out_head` + `out_tail` (`post`), `error_excerpt` (`post_fail`),
  `exit_code` (`post_fail`), `prompt_id`.
- `prompt`: `prompt_id`, `prompt_excerpt`.
- `session_start`: `prompt_id` (always null in practice), `source`.
- `action` (Task 4, guard.py): `prompt_id`, `action` (e.g. `"pass"`),
  `open_failures` -- a list of `tool_use_id`s unresolved at that stop.
- `verdict` (Task 4, provider.py/verdict_policy.py): `prompt_id`,
  `question_key`, `answer` (a dict with a numeric `noul` field, 0..1),
  `listed_failures` -- a list of `tool_use_id`s the question was asked
  about.

Every field is read with `.get()` plus a type check, never assumed present
or well-typed: `build_span` must never raise on a malformed, truncated, or
adversarial row list (Hypothesis-tested in tests/unit/test_span.py).

Normalization for "same command" (task-1-brief.md controller notes):
leading `VAR=value` assignments and known wrapper commands are stripped
the same way `gates.py`'s G-CHECK tagging does, by reusing its private
`_strip_leading` helper (lazy import: `gates.py` has no import-time side
effects -- it only imports `os`, `re`, `functools.cache`, and `.policy`,
same as this hot-path module's own lazy-import convention). Whitespace is
then collapsed and the comparison is case-sensitive. For a file tool the
comparison is just the (whitespace-collapsed) `input_excerpt`, i.e. the
path, per the brief -- no wrapper-stripping applies there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from .policy import Policy

_MUTATING_FILE_TOOLS = ("Write", "Edit", "NotebookEdit")
_BASH_TOOL = "Bash"
_STEP_EVENTS = ("post", "post_fail")


class Step(NamedTuple):
    seq: int
    tool: str
    command: str
    status: str
    exit_code: int | None
    is_check: bool
    soft_fail_candidate: bool
    resolved_later: bool
    acknowledged: bool
    tool_use_id: str
    prompt_id: str | None
    out_excerpt: str
    never_send: bool


class Span(NamedTuple):
    prompts: tuple[str, ...]
    steps: tuple[Step, ...]
    unresolved_failures: tuple[int, ...]
    soft_fail_seqs: tuple[int, ...]
    checks_passed_after_last_change: bool
    span_prompt_ids: tuple[str | None, ...]
    reason: str


# --- Defensive field readers (Hypothesis: arbitrary rows never raise) ------


def _str_or(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _bool_or(value: object, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _num_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _safe_rows(rows: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [r for r in rows if isinstance(r, Mapping)]


def _prompt_id_of(row: Mapping[str, object]) -> str | None:
    value = row.get("prompt_id")
    return value if isinstance(value, str) else None


# --- Command/path normalization ---------------------------------------------


def _strip_bash_wrappers(command: str) -> str:
    from .gates import _strip_leading

    return _strip_leading(command)


def _normalize_key(tool: str, raw_command: str) -> str:
    text = _strip_bash_wrappers(raw_command) if tool == _BASH_TOOL else raw_command
    return " ".join(text.split())


# --- Step construction -------------------------------------------------------


def _build_step(row: Mapping[str, object], seq: int) -> Step:
    if row.get("event") == "post":
        out_excerpt = _str_or(row.get("out_head")) + _str_or(row.get("out_tail"))
    else:
        out_excerpt = _str_or(row.get("error_excerpt"))
    return Step(
        seq=seq,
        tool=_str_or(row.get("tool_name")),
        command=_str_or(row.get("input_excerpt")),
        status=_str_or(row.get("status")),
        exit_code=_int_or_none(row.get("exit_code")),
        is_check=_bool_or(row.get("is_check")),
        soft_fail_candidate=_bool_or(row.get("soft_fail_candidate")),
        resolved_later=False,
        acknowledged=False,
        tool_use_id=_str_or(row.get("tool_use_id")),
        prompt_id=_prompt_id_of(row),
        out_excerpt=out_excerpt,
        never_send=_bool_or(row.get("never_send")),
    )


def _acknowledged_tool_use_ids(span_rows: list[Mapping[str, object]], t_ack_hi: float) -> set[str]:
    """D-020: a stop's `acks_failures` verdict at or above `t_ack_hi` acks
    every `tool_use_id` it listed. Any `verdict` row anywhere in the span
    counts -- verdicts are only ever written at a prior Stop, so they are
    always earlier than the current one being evaluated."""
    acked: set[str] = set()
    for row in span_rows:
        if row.get("event") != "verdict" or row.get("question_key") != "acks_failures":
            continue
        answer = row.get("answer")
        noul = _num_or_none(answer.get("noul")) if isinstance(answer, Mapping) else None
        if noul is None or noul < t_ack_hi:
            continue
        listed = row.get("listed_failures")
        if isinstance(listed, (list, tuple)):
            acked.update(v for v in listed if isinstance(v, str))
    return acked


def _resolve_steps(steps: list[Step], acked: set[str]) -> list[Step]:
    """Second pass: fill in `resolved_later` and `acknowledged` per step.

    A failure (`status == "error"` or `soft_fail_candidate`) is resolved by
    a later step, same tool, same normalized command/path, `status == "ok"`
    (PLAN.md 5.3 / D-020).
    """
    resolved: list[Step] = []
    for i, step in enumerate(steps):
        is_failure = step.status == "error" or step.soft_fail_candidate
        resolved_later = False
        if is_failure:
            key = _normalize_key(step.tool, step.command)
            for later in steps[i + 1 :]:
                if (
                    later.status == "ok"
                    and later.tool == step.tool
                    and _normalize_key(later.tool, later.command) == key
                ):
                    resolved_later = True
                    break
        acknowledged = bool(step.tool_use_id) and step.tool_use_id in acked
        resolved.append(step._replace(resolved_later=resolved_later, acknowledged=acknowledged))
    return resolved


def _checks_passed_after_last_change(steps: tuple[Step, ...]) -> bool:
    """True when nothing mutating has happened, or the last mutating step
    (a Write/Edit/NotebookEdit `post`, or a non-check Bash step) is
    followed by an `is_check` step with `status == "ok"`."""
    last_mutation_seq = 0
    for step in steps:
        is_mutation = step.tool in _MUTATING_FILE_TOOLS or (
            step.tool == _BASH_TOOL and not step.is_check
        )
        if is_mutation:
            last_mutation_seq = step.seq
    if last_mutation_seq == 0:
        return True
    return any(
        step.is_check and step.status == "ok" and step.seq > last_mutation_seq for step in steps
    )


def _prompt_texts(span_rows: list[Mapping[str, object]]) -> tuple[str, ...]:
    prompts: list[str] = []
    seen: set[str] = set()
    for row in span_rows:
        if row.get("event") != "prompt":
            continue
        pid = _prompt_id_of(row)
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        prompts.append(_str_or(row.get("prompt_excerpt")))
    return tuple(prompts)


def _build_from_rows(
    span_rows: list[Mapping[str, object]],
    span_prompt_ids: tuple[str | None, ...],
    reason: str,
    t_ack_hi: float,
) -> Span:
    step_rows = [r for r in span_rows if r.get("event") in _STEP_EVENTS]
    raw_steps = [_build_step(row, i + 1) for i, row in enumerate(step_rows)]
    acked = _acknowledged_tool_use_ids(span_rows, t_ack_hi)
    steps = tuple(_resolve_steps(raw_steps, acked))

    unresolved = tuple(
        s.seq for s in steps if s.status == "error" and not s.resolved_later and not s.acknowledged
    )
    soft_fail = tuple(
        s.seq
        for s in steps
        if s.soft_fail_candidate and not s.resolved_later and not s.acknowledged
    )

    return Span(
        prompts=_prompt_texts(span_rows),
        steps=steps,
        unresolved_failures=unresolved,
        soft_fail_seqs=soft_fail,
        checks_passed_after_last_change=_checks_passed_after_last_change(steps),
        span_prompt_ids=span_prompt_ids,
        reason=reason,
    )


# --- Walk-back over prompts ---------------------------------------------------


def _has_clean_stop(block_rows: list[Mapping[str, object]]) -> bool:
    for row in block_rows:
        if (
            row.get("event") == "action"
            and row.get("action") == "pass"
            and not row.get("open_failures")
        ):
            return True
    return False


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


def _walk_back(
    safe_rows: list[Mapping[str, object]], current_prompt_id: str, max_prompts: int
) -> tuple[list[str], str] | None:
    """Returns (included prompt ids, most-recent-first, stop reason), or
    `None` when `current_prompt_id` names no row in `safe_rows` at all."""
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

    if current_prompt_id not in first_idx:
        return None

    included = [current_prompt_id]
    idx = seen.index(current_prompt_id)
    reason = "start_of_session"
    while True:
        block_rows = [r for r in safe_rows if _prompt_id_of(r) == included[-1]]
        if _has_clean_stop(block_rows):
            reason = "clean_stop"
            break
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
        included.append(prev_pid)
        idx -= 1

    return included, reason


def build_span(
    rows: Sequence[Mapping[str, object]], current_prompt_id: str | None, policy: Policy
) -> Span:
    """Build the verification span for `current_prompt_id` from `rows`.

    `rows` is one session's ledger rows in file order. With a null
    `current_prompt_id` the span is just the rows sharing that null id
    (D-015 note) -- no walking back through other prompts.
    """
    safe_rows = _safe_rows(rows)
    t_ack_hi = policy.thresholds.t_ack_hi

    if current_prompt_id is None:
        bucket = [r for r in safe_rows if r.get("prompt_id") is None]
        span_prompt_ids: tuple[str | None, ...] = (None,) if bucket else ()
        return _build_from_rows(bucket, span_prompt_ids, "null_prompt_id", t_ack_hi)

    walked = _walk_back(safe_rows, current_prompt_id, policy.span.max_prompts)
    if walked is None:
        return _build_from_rows([], (), "prompt_not_found", t_ack_hi)

    included, reason = walked
    included_set = set(included)
    span_rows = [r for r in safe_rows if _prompt_id_of(r) in included_set]
    span_prompt_ids = tuple(reversed(included))
    return _build_from_rows(span_rows, span_prompt_ids, reason, t_ack_hi)


__all__ = ["Step", "Span", "build_span"]
