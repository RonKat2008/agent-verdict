"""Provider state builder (PLAN.md 5.3, D-010, C4; task-2-brief.md, fix round 1).

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

`trusted_facts.steps` carries an `is_check` field alongside PLAN 5.3's own
worked example (`seq`, `tool`, `command`, `status`, `exit_code`,
`resolved_later`): this is a deliberate addition beyond that illustrative
example, per task-2-brief.md's controller notes, which listed `is_check`
explicitly as one of the fields to carry. Noted here per fix round 1 item
5's request to say which way this went and why.

Performance (fix round 1 item 1): the original implementation re-ran
`json.dumps` on the whole assembled state after every single dropped step
-- O(n) work times up to n drops per stage, O(n^2) overall (measured 2.66s
for 600 steps against a 2.5s total Stop budget). This version serializes
each step entry and each excerpt's `"key": value` pair exactly once
(`entry_len`), and a small number of times per call it serializes the
whole state to get an exact byte count (`fl()`, at most once per stage
boundary -- a fixed count, not one per drop). Deciding which steps survive
a stage is then pure arithmetic: `_collection_len` reproduces `json.dumps`'
exact byte count for a JSON array/object from the sum of its already-known
item lengths and item count (`2` for brackets, `+2` per separator between
items -- verified against `json.dumps`'s actual `", "`/`": "` separators),
so dropping one step is an O(1) subtraction, not a re-serialization.
`_drop_steps_linear`/`_drop_steps_and_excerpts_linear` each make one pass
over their candidates, O(n) total per stage, O(n) overall across every
stage -- not O(n^2).

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
   may now go too -- this is the one stage allowed to touch one),
   dropping that step's excerpt along with it.
5. If still over the hard cap: keep only the newest 40 steps (and their
   excerpts), regardless of kind.
6. (fix round 1 item 2) If STILL over the hard cap -- this can only
   happen when `final_message` itself is large, since nothing above ever
   shrinks it -- cap every claim at 240 characters and shrink
   `final_message` with `textnorm.truncate_anchored`, retrying with a
   smaller head/tail a bounded number of times, down to an empty string
   in the worst case. This stage is the guarantee: `build_state` never
   returns more than `max_tokens`, however large `final_message` is.

`overflow` (fix round 1 item 3, PLAN 5.3's `compression_overflow`) is
`True` whenever ANY of the above actually changed something -- a step
dropped, an excerpt shortened, `user_task` truncated, a claim capped, or
`final_message` shrunk -- not only when the hard cap forced stage 4-6.
Merely running a stage that finds nothing to do leaves it `False`.

`trusted_facts.unresolved_failures` and `checks_passed_after_last_change`
are carried through unchanged from `span` at every stage: they are
ground truth about what happened, not a view into `steps` that shrinks
when `steps` does.

`build_state` never raises: it only reads already-validated `Span`/
`Policy` NamedTuples, and `json.dumps` on a plain tree of `str`, `int`,
`bool`, `None`, `dict`, and `list` cannot fail.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from .policy import Policy
from .span import Span, Step

_MAX_STEPS_LAST_RESORT = 40
_USER_TASK_KEEP_CHARS = 1500
_COMMAND_MAX_CHARS = 120
_CLAIM_MAX_CHARS = 240
_FINAL_CLAMP_ATTEMPTS = 8
_FINAL_CLAMP_SAFETY_MARGIN = 80


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


def _serialized_len(obj: object) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


def _collection_len(item_total: int, count: int) -> int:
    """Exact length `json.dumps` gives a JSON array/object, from the sum of
    each element's own serialized length (for an object, fold `"key": `
    into that element's length first) and the element count. Default
    `json.dumps` separators are `", "` between elements and `": "` after a
    key, so `2` (brackets) `+ item_total + 2*(count-1)` (separators)
    matches `json.dumps` byte-for-byte without re-serializing anything."""
    if count == 0:
        return 2
    return 2 + item_total + 2 * (count - 1)


def _excerpts_for(steps: list[Step], flagged_seqs: tuple[int, ...]) -> dict[str, str]:
    by_seq = {step.seq: step for step in steps}
    excerpts: dict[str, str] = {}
    for seq in flagged_seqs:
        step = by_seq.get(seq)
        if step is None or step.never_send:
            continue
        excerpts[str(seq)] = step.out_excerpt
    return excerpts


def _kv_item_len(key: str, value: str) -> int:
    return _serialized_len(key) + 2 + _serialized_len(value)


def _assemble(
    span: Span,
    steps: list[Step],
    final_message: str,
    claims: dict[str, str],
    excerpts: dict[str, str],
    user_task: str,
) -> dict[str, object]:
    return {
        "trusted_facts": {
            "user_task": user_task,
            "steps": [_step_entry(s) for s in steps],
            "unresolved_failures": list(span.unresolved_failures),
            "checks_passed_after_last_change": span.checks_passed_after_last_change,
        },
        "untrusted": {
            "final_message": final_message,
            "claims": claims,
            "step_output_excerpts": excerpts,
        },
    }


def _is_plain_ok(step: Step) -> bool:
    """Stage 1 candidates: the least informative rows in the span."""
    return step.status == "ok" and not step.is_check and not step.soft_fail_candidate


def _is_non_error_non_check(step: Step) -> bool:
    """Stage 4 candidates: anything but an error row or a check row."""
    return step.status != "error" and not step.is_check


def _drop_steps_linear(
    steps: list[Step],
    predicate: Callable[[Step], bool],
    limit_tokens: int,
    fl: int,
    entry_len: dict[int, int],
) -> tuple[list[Step], bool]:
    """Single pass, oldest first: drop steps matching `predicate` while the
    running byte estimate (seeded from the real `fl`, the current full
    serialized length) is still over `limit_tokens`. `entry_len` is each
    step's own serialized length, computed once by the caller -- dropping
    one is an O(1) subtraction via `_collection_len`, not a re-serialization."""
    step_sum = sum(entry_len[s.seq] for s in steps)
    count = len(steps)
    base = fl - _collection_len(step_sum, count)
    kept: list[Step] = []
    changed = False
    for step in steps:
        tokens = (base + _collection_len(step_sum, count)) // 4
        if tokens <= limit_tokens or not predicate(step):
            kept.append(step)
            continue
        changed = True
        step_sum -= entry_len[step.seq]
        count -= 1
    return kept, changed


def _drop_steps_and_excerpts_linear(
    steps: list[Step],
    excerpts: dict[str, str],
    limit_tokens: int,
    fl: int,
    entry_len: dict[int, int],
) -> tuple[list[Step], dict[str, str], bool]:
    """Stage 4: like `_drop_steps_linear`, but dropping a step also drops
    its flagged excerpt (if any), so both collections' running totals move
    together -- still O(1) per step."""
    excerpt_len = {k: _kv_item_len(k, v) for k, v in excerpts.items()}
    step_sum = sum(entry_len[s.seq] for s in steps)
    step_count = len(steps)
    exc_sum = sum(excerpt_len.values())
    exc_count = len(excerpt_len)
    base = fl - _collection_len(step_sum, step_count) - _collection_len(exc_sum, exc_count)
    kept_steps: list[Step] = []
    kept_excerpts = dict(excerpts)
    changed = False
    for step in steps:
        tokens = (
            base + _collection_len(step_sum, step_count) + _collection_len(exc_sum, exc_count)
        ) // 4
        if tokens <= limit_tokens or not _is_non_error_non_check(step):
            kept_steps.append(step)
            continue
        changed = True
        step_sum -= entry_len[step.seq]
        step_count -= 1
        seq_str = str(step.seq)
        if seq_str in kept_excerpts:
            exc_sum -= excerpt_len[seq_str]
            exc_count -= 1
            del kept_excerpts[seq_str]
    return kept_steps, kept_excerpts, changed


def _shorten_excerpts(
    excerpts: dict[str, str], head: int, tail: int
) -> tuple[dict[str, str], bool]:
    from . import textnorm

    changed = False
    shortened: dict[str, str] = {}
    for seq_str, text in excerpts.items():
        new_text = textnorm.truncate_anchored(text, head, tail)
        changed = changed or new_text != text
        shortened[seq_str] = new_text
    return shortened, changed


def _keep_newest(
    steps: list[Step], excerpts: dict[str, str], limit: int
) -> tuple[list[Step], dict[str, str]]:
    if len(steps) <= limit:
        return steps, excerpts
    dropped_seqs = {s.seq for s in steps[:-limit]}
    kept_excerpts = {k: v for k, v in excerpts.items() if int(k) not in dropped_seqs}
    return steps[-limit:], kept_excerpts


def _fit_final_message(text: str, other_len: int, max_tokens: int) -> tuple[str, bool]:
    """Last resort (stage 6): shrink `text` with `textnorm.truncate_anchored`
    until `other_len + len(json.dumps(text))` fits `max_tokens*4` chars, or
    give up to an empty string. Bounded retries (each shrinking head/tail
    by the measured overshoot) -- this never loops unboundedly and never
    raises."""
    from . import textnorm

    budget_chars = max_tokens * 4
    if other_len + _serialized_len(text) <= budget_chars:
        return text, False
    candidate = text
    for _ in range(_FINAL_CLAMP_ATTEMPTS):
        remaining = max(0, budget_chars - other_len - _FINAL_CLAMP_SAFETY_MARGIN)
        head = remaining // 5
        tail = remaining - head
        candidate = textnorm.truncate_anchored(text, head, tail)
        if other_len + _serialized_len(candidate) <= budget_chars:
            return candidate, True
    return "", True


def _final_clamp(
    final_message: str, claims: dict[str, str], other_len: int, max_tokens: int
) -> tuple[str, dict[str, str], bool]:
    capped_claims = {k: v[:_CLAIM_MAX_CHARS] for k, v in claims.items()}
    claims_changed = capped_claims != claims
    old_claims_len = _collection_len(
        sum(_kv_item_len(k, v) for k, v in claims.items()), len(claims)
    )
    new_claims_len = _collection_len(
        sum(_kv_item_len(k, v) for k, v in capped_claims.items()), len(capped_claims)
    )
    adjusted_other_len = other_len + (new_claims_len - old_claims_len)
    message, msg_changed = _fit_final_message(final_message, adjusted_other_len, max_tokens)
    return message, capped_claims, claims_changed or msg_changed


def build_state(
    span: Span, final_message: str, claims: tuple[str, ...], policy: Policy
) -> tuple[dict[str, object], bool]:
    sp = policy.state
    steps = list(span.steps)
    flagged_seqs = span.unresolved_failures + span.soft_fail_seqs
    user_task = "\n\n".join(span.prompts)
    claims_dict = {f"c{i}": text for i, text in enumerate(claims, start=1)}
    excerpts = _excerpts_for(steps, flagged_seqs)
    entry_len = {s.seq: _serialized_len(_step_entry(s)) for s in steps}
    overflow = False

    def fl() -> int:
        return _serialized_len(
            _assemble(span, steps, final_message, claims_dict, excerpts, user_task)
        )

    current = fl()
    if current // 4 > sp.target_tokens:
        steps, changed = _drop_steps_linear(
            steps, _is_plain_ok, sp.target_tokens, current, entry_len
        )
        overflow = overflow or changed
        current = fl()

    if current // 4 > sp.target_tokens:
        excerpts, changed = _shorten_excerpts(excerpts, sp.excerpt_head, sp.excerpt_tail)
        overflow = overflow or changed
        current = fl()

    if current // 4 > sp.target_tokens and len(user_task) > _USER_TASK_KEEP_CHARS:
        user_task = user_task[-_USER_TASK_KEEP_CHARS:]
        overflow = True
        current = fl()

    if current // 4 > sp.max_tokens:
        steps, excerpts, changed = _drop_steps_and_excerpts_linear(
            steps, excerpts, sp.max_tokens, current, entry_len
        )
        overflow = overflow or changed
        current = fl()

    if current // 4 > sp.max_tokens and len(steps) > _MAX_STEPS_LAST_RESORT:
        steps, excerpts = _keep_newest(steps, excerpts, _MAX_STEPS_LAST_RESORT)
        overflow = True
        current = fl()

    if current // 4 > sp.max_tokens:
        other_len = current - _serialized_len(final_message)
        final_message, claims_dict, changed = _final_clamp(
            final_message, claims_dict, other_len, sp.max_tokens
        )
        overflow = overflow or changed

    return _assemble(span, steps, final_message, claims_dict, excerpts, user_task), overflow


__all__ = ["build_state"]
