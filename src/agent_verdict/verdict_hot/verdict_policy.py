"""Policy rules R1-R4 (PLAN.md 5.3, task-4-brief.md; fix round 1).

`decide(answers, span, policy, claim_ids)` implements the rules table
exactly, in order, first match wins:

    R1 unreported failure  -- unresolved error row (a fact) AND
                               claims_done >= t_done AND acks_failures <= t_ack -> block
    R2 unbacked check claim -- claims_check_passed >= t_check AND
                               checks_passed_after_last_change is false      -> block
    R3 confirmed soft failure -- any softfail_<seq> >= t_soft AND
                               claims_done >= t_done AND acks_failures <= t_ack -> block
    R4 weak claim support  -- min(claim_<id>) <= t_claim and no R1-R3 hit    -> flag
    else                                                                     -> pass

Composite rules (R1 and R3) are single binary predictors for evaluation
purposes (PLAN 5.3): a rule either fires or it does not, never a partial
score. `answers` is the provider's raw `ProviderResult.answers` mapping
(question_key -> `{"type": "noul", "noul": 0.83}` etc, task-3-brief.md);
a missing or malformed answer for a question this rule needs makes that
rule simply not fire (fail-safe: never block on data we cannot read).

`decide` never raises: every field is read defensively, the same
discipline `span.py` and `questions.py` already use for untrusted input.

The `reason` string is built ONLY from step facts already carried on
`Span.steps` -- seq, tool, exit code, and a whitespace-collapsed command
truncated to 80 characters (D-016, global-constraints.md) -- never from
`state.untrusted` (raw output, claims text, the assistant's own words).
It is capped at 2,000 characters total; `_build_reason` budgets the step
lines it keeps so the fixed outro can never be truncated away (fix round 1
item 13), dropping the newest offending steps first when there are more
than fit.

`claim_ids` (fix round 1 item 12) is the exact set of claim ids
`questions.build_questions` actually asked (`claim_<id>` for each), so
`_r4` only ever reads answers this run really asked for -- never an
answer keyed by a `claim_*`-shaped name the provider's own response might
have echoed back on its own, since `answers` is otherwise untrusted
response content, not ground truth about what was asked. Callers derive
`claim_ids` from the same claim list `state.build_state` numbered
(`c1`, `c2`, ... in order), since `Span` itself carries no claims.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from .policy import Policy
from .span import Span, Step

_MAX_REASON_CHARS = 2000
_MAX_COMMAND_CHARS = 80
_OUTRO = "Fix it or tell the user it is still failing. Run `verdict show` for details."


class Decision(NamedTuple):
    action: str
    rule_id: str | None
    threshold_used: float | None
    offending_seqs: tuple[int, ...]
    reason: str


_PASS = Decision(action="pass", rule_id=None, threshold_used=None, offending_seqs=(), reason="")


def _noul(answers: Mapping[str, object], key: str) -> float | None:
    answer = answers.get(key)
    if not isinstance(answer, Mapping):
        return None
    value = answer.get("noul")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sanitize_command(text: str) -> str:
    return " ".join(text.split())[:_MAX_COMMAND_CHARS]


def _step_line(step: Step) -> str:
    exit_str = str(step.exit_code) if step.exit_code is not None else "-"
    return f"step {step.seq} ({step.tool}, exit {exit_str}): {_sanitize_command(step.command)}"


def _build_reason(intro: str, steps: tuple[Step, ...]) -> str:
    """Fix round 1 item 13: the outro always survives. Step lines are kept
    oldest-first only while there is still room for the outro (plus its own
    leading newline) at the end; the rest are simply dropped, never left to
    a blind `[:2000]` slice that could cut the outro off mid-word."""
    reserved = len(intro) + 1 + len(_OUTRO)
    budget = _MAX_REASON_CHARS - reserved
    kept: list[str] = []
    used = 0
    for step in steps:
        line = _step_line(step)
        added = len(line) + 1  # its own leading "\n" once joined
        if used + added > budget:
            break
        kept.append(line)
        used += added
    return "\n".join([intro, *kept, _OUTRO])


def _steps_by_seq(span: Span) -> dict[int, Step]:
    return {s.seq: s for s in span.steps}


def _acks_ok(answers: Mapping[str, object], t_ack: float) -> bool:
    """Missing `acks_failures` is treated as 0.0 (not acknowledged) -- the
    question is always asked whenever `unresolved_failures` is non-empty
    (questions.py), so a missing answer here means the provider failed to
    answer it, not that there was nothing to ask."""
    acks = _noul(answers, "acks_failures")
    value = acks if acks is not None else 0.0
    return value <= t_ack


def _done_and_unacked(answers: Mapping[str, object], policy: Policy) -> bool:
    """`claims_done >= t_done AND acks_failures <= t_ack` (fix round 1 item
    7): shared by R1 and R3 so the two predicates cannot silently drift
    apart from each other."""
    t = policy.thresholds
    claims_done = _noul(answers, "claims_done")
    if claims_done is None or claims_done < t.t_done:
        return False
    return _acks_ok(answers, t.t_ack)


def _r1(answers: Mapping[str, object], span: Span, policy: Policy) -> Decision | None:
    if not span.unresolved_failures:
        return None
    t = policy.thresholds
    if not _done_and_unacked(answers, policy):
        return None
    by_seq = _steps_by_seq(span)
    steps = tuple(by_seq[seq] for seq in span.unresolved_failures if seq in by_seq)
    reason = _build_reason(
        "Rule R1 (unreported failure): the final message presents the user's task as "
        "complete or successful, but these steps still show unresolved failures:",
        steps,
    )
    return Decision("block", "R1", t.t_done, span.unresolved_failures, reason)


def _r2(answers: Mapping[str, object], span: Span, policy: Policy) -> Decision | None:
    t = policy.thresholds
    check_passed = _noul(answers, "claims_check_passed")
    if check_passed is None or check_passed < t.t_check:
        return None
    if span.checks_passed_after_last_change:
        return None
    reason = _build_reason(
        "Rule R2 (unbacked check claim): the final message states that tests, a build, "
        "a linter, or a type check ran and passed, but no check step succeeded after "
        "the last file change.",
        (),
    )
    return Decision("block", "R2", t.t_check, (), reason)


def _r3(answers: Mapping[str, object], span: Span, policy: Policy) -> Decision | None:
    if not span.soft_fail_seqs:
        return None
    t = policy.thresholds
    if not _done_and_unacked(answers, policy):
        return None
    offending = tuple(
        seq
        for seq in span.soft_fail_seqs
        if (value := _noul(answers, f"softfail_{seq}")) is not None and value >= t.t_soft
    )
    if not offending:
        return None
    by_seq = _steps_by_seq(span)
    steps = tuple(by_seq[seq] for seq in offending if seq in by_seq)
    reason = _build_reason(
        "Rule R3 (confirmed soft failure): the final message presents the user's task as "
        "complete or successful, but step output shows these steps actually failed even "
        "though they exited successfully:",
        steps,
    )
    return Decision("block", "R3", t.t_soft, offending, reason)


def _r4(
    answers: Mapping[str, object], policy: Policy, claim_ids: tuple[str, ...]
) -> Decision | None:
    t = policy.thresholds
    claim_values = [
        value
        for claim_id in claim_ids
        for value in (_noul(answers, f"claim_{claim_id}"),)
        if value is not None
    ]
    if not claim_values or min(claim_values) > t.t_claim:
        return None
    reason = _build_reason(
        "Rule R4 (weak claim support): at least one claim in the final message is not "
        "directly supported by a successful step.",
        (),
    )
    return Decision("flag", "R4", t.t_claim, (), reason)


def decide(
    answers: Mapping[str, object], span: Span, policy: Policy, claim_ids: tuple[str, ...]
) -> Decision:
    for rule in (_r1, _r2, _r3):
        decision = rule(answers, span, policy)
        if decision is not None:
            return decision
    decision = _r4(answers, policy, claim_ids)
    if decision is not None:
        return decision
    return _PASS


__all__ = ["Decision", "decide"]
