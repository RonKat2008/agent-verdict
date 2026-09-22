"""Provider state builder (PLAN.md 5.3, D-010, C4; task-2-brief.md).

`build_state` turns a verification `Span` plus the code-extracted claims
(`claims.extract_claims`) and the Stop hook's `final_message` into the JSON
object sent to the provider: a `trusted_facts` section (the ledger's own
record -- the user's task, the steps taken, which failures are still
unresolved, whether checks passed after the last change) and an
`untrusted` section (the assistant's own words: its final message, the
claims it asserts, and the raw output a reader needs to judge them). The
split matters because C9/D-010 documents Jev as not treating state as
hostile -- everything an adversarial transcript could have shaped goes
under `untrusted`, and `questions.py`'s instructions tell the model so
explicitly.

Never-send steps already carry the literal string `"[never-send]"` in
place of both `command` and `out_excerpt` (recorders.py's `_NEVER_SEND_
MARKER`); this module does not re-check for secrets, but it never adds a
never-send step's excerpt to `step_output_excerpts` even though the
sentinel text itself is harmless -- there is nothing useful for a
question to judge there (task-2-brief.md).

Compression is a fixed sequence of stages, applied only as far as needed
to fit `policy.state.target_tokens` and then `policy.state.max_tokens`
(token estimate: `len(json.dumps(state)) // 4`, task-2-brief.md):

1. Drop the oldest steps that are plain `ok` (not a check, not a
   soft-fail candidate) until under `target_tokens`.
2. Shorten every `step_output_excerpts` entry to `excerpt_head` +
   `excerpt_tail` characters with `textnorm.truncate_anchored`.
3. Truncate `user_task` from the front, keeping only its last 1,500
   characters (a later correction matters more than an earlier one).
4. If still over the hard cap `max_tokens`: drop the oldest remaining
   steps that are not an error and not a check (a soft-fail candidate
   may now go too -- this is the one stage allowed to touch one), and
   record the returned overflow flag as `True`.
5. If still over the hard cap: keep only the newest 40 steps,
   regardless of kind.

`trusted_facts.unresolved_failures` and `checks_passed_after_last_change`
are carried through unchanged from `span` at every stage: they are
ground truth about what happened, not a view into `steps` that shrinks
when `steps` does. `final_message` and each claim are assumed to already
be within their own policy limits (`store.final_message_max_chars`,
`claims.py`'s per-claim cap) by the time they reach this module --
mirroring span.py's assumption that ledger text is already redacted, this
module's own compression budget is spent on `steps` and `user_task`
exactly as PLAN 5.3 describes, not on `final_message` or `claims`.

`build_state` never raises: it only reads already-validated `Span`/
`Policy` NamedTuples, and `json.dumps` on a plain tree of `str`, `int`,
`bool`, `None`, `dict`, and `list` cannot fail.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from . import textnorm
from .policy import Policy, StatePolicy
from .span import Span, Step

_MAX_STEPS_LAST_RESORT = 40
_USER_TASK_KEEP_CHARS = 1500
_COMMAND_MAX_CHARS = 120


def _step_entry(step: Step) -> dict[str, object]:
    return {
        "seq": step.seq,
        "tool": step.tool,
        "command": step.command[:_COMMAND_MAX_CHARS],
        "status": step.status,
        "exit_code": step.exit_code,
        "is_check": step.is_check,
        "resolved_later": step.resolved_later,
    }


def _step_output_excerpts(
    steps: list[Step], flagged_seqs: tuple[int, ...], head: int | None, tail: int | None
) -> dict[str, str]:
    """Excerpts for `flagged_seqs` still present in `steps`, skipping
    never-send steps entirely (module docstring)."""
    by_seq = {step.seq: step for step in steps}
    excerpts: dict[str, str] = {}
    for seq in flagged_seqs:
        step = by_seq.get(seq)
        if step is None or step.never_send:
            continue
        text = step.out_excerpt
        if head is not None and tail is not None:
            text = textnorm.truncate_anchored(text, head, tail)
        excerpts[str(seq)] = text
    return excerpts


def _assemble(
    span: Span,
    steps: list[Step],
    final_message: str,
    claims: tuple[str, ...],
    flagged_seqs: tuple[int, ...],
    user_task: str,
    shorten: StatePolicy | None,
) -> dict[str, object]:
    head = shorten.excerpt_head if shorten else None
    tail = shorten.excerpt_tail if shorten else None
    return {
        "trusted_facts": {
            "user_task": user_task,
            "steps": [_step_entry(s) for s in steps],
            "unresolved_failures": list(span.unresolved_failures),
            "checks_passed_after_last_change": span.checks_passed_after_last_change,
        },
        "untrusted": {
            "final_message": final_message,
            "claims": {f"c{i}": text for i, text in enumerate(claims, start=1)},
            "step_output_excerpts": _step_output_excerpts(steps, flagged_seqs, head, tail),
        },
    }


def _token_estimate(state: dict[str, object]) -> int:
    return len(json.dumps(state, ensure_ascii=False)) // 4


def _is_plain_ok(step: Step) -> bool:
    """Stage 1 candidates: the least informative rows in the span."""
    return step.status == "ok" and not step.is_check and not step.soft_fail_candidate


def _is_non_error_non_check(step: Step) -> bool:
    """Stage 4 candidates: anything but an error row or a check row."""
    return step.status != "error" and not step.is_check


def _drop_oldest(steps: list[Step], predicate: Callable[[Step], bool]) -> bool:
    for i, step in enumerate(steps):
        if predicate(step):
            del steps[i]
            return True
    return False


def _compress_to_budget(
    span: Span,
    steps: list[Step],
    final_message: str,
    claims: tuple[str, ...],
    flagged_seqs: tuple[int, ...],
    user_task: str,
    limit: int,
    predicate: Callable[[Step], bool],
    shorten: StatePolicy | None,
) -> dict[str, object]:
    """Drop steps matching `predicate`, oldest first, until `state` fits
    `limit` tokens or no more candidates remain."""
    state = _assemble(span, steps, final_message, claims, flagged_seqs, user_task, shorten)
    while _token_estimate(state) > limit:
        if not _drop_oldest(steps, predicate):
            break
        state = _assemble(span, steps, final_message, claims, flagged_seqs, user_task, shorten)
    return state


def build_state(
    span: Span, final_message: str, claims: tuple[str, ...], policy: Policy
) -> tuple[dict[str, object], bool]:
    state_policy = policy.state
    steps = list(span.steps)
    flagged_seqs = span.unresolved_failures + span.soft_fail_seqs
    user_task = "\n\n".join(span.prompts)
    target = state_policy.target_tokens

    state = _compress_to_budget(
        span, steps, final_message, claims, flagged_seqs, user_task, target, _is_plain_ok, None
    )

    if _token_estimate(state) > target:
        state = _assemble(span, steps, final_message, claims, flagged_seqs, user_task, state_policy)

    if _token_estimate(state) > target:
        user_task = user_task[-_USER_TASK_KEEP_CHARS:]
        state = _assemble(span, steps, final_message, claims, flagged_seqs, user_task, state_policy)

    overflow = _token_estimate(state) > state_policy.max_tokens
    if overflow:
        state = _compress_to_budget(
            span,
            steps,
            final_message,
            claims,
            flagged_seqs,
            user_task,
            state_policy.max_tokens,
            _is_non_error_non_check,
            state_policy,
        )

    if _token_estimate(state) > state_policy.max_tokens:
        del steps[:-_MAX_STEPS_LAST_RESORT]
        state = _assemble(span, steps, final_message, claims, flagged_seqs, user_task, state_policy)

    return state, overflow


__all__ = ["build_state"]
