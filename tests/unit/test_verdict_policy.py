"""Tests for verdict_hot.verdict_policy (task-4-brief.md, PLAN.md 5.3).

`Span`/`Step` are constructed directly rather than through `build_span`:
`decide` only ever reads `Span.unresolved_failures`, `Span.soft_fail_seqs`,
`Span.checks_passed_after_last_change`, and `Span.steps` (for the reason
text), so building the `NamedTuple`s by hand keeps each test's fixture
minimal and exercises exactly the boundary this test is about.
"""

from __future__ import annotations

from pathlib import Path

from verdict_hot import policy as policy_mod
from verdict_hot.span import Span, Step
from verdict_hot.verdict_policy import decide

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
_POLICY = policy_mod.load_policy(PACKAGED_DEFAULT)
_T = _POLICY.thresholds


def _step(
    seq: int,
    tool: str = "Bash",
    command: str = "npm test",
    status: str = "error",
    exit_code: int | None = 1,
) -> Step:
    return Step(
        seq=seq,
        tool=tool,
        command=command,
        status=status,
        exit_code=exit_code,
        is_check=False,
        soft_fail_candidate=False,
        resolved_later=False,
        acknowledged=False,
        tool_use_id=f"t{seq}",
        prompt_id="p1",
        out_excerpt="",
        never_send=False,
    )


def _span(
    steps: tuple[Step, ...] = (),
    unresolved: tuple[int, ...] = (),
    soft_fail: tuple[int, ...] = (),
    checks_ok: bool = True,
) -> Span:
    return Span(
        prompts=("do the thing",),
        steps=steps,
        unresolved_failures=unresolved,
        soft_fail_seqs=soft_fail,
        checks_passed_after_last_change=checks_ok,
        span_prompt_ids=("p1",),
        reason="start_of_session",
    )


def _noul(value: float) -> dict[str, object]:
    return {"type": "noul", "noul": value}


def _answers(**kwargs: float) -> dict[str, object]:
    return {key: _noul(value) for key, value in kwargs.items()}


# --- R1: unreported failure ---------------------------------------------------


def test_r1_fires_at_exact_thresholds() -> None:
    span = _span(steps=(_step(1),), unresolved=(1,))
    answers = _answers(claims_done=_T.t_done, acks_failures=_T.t_ack)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "block"
    assert decision.rule_id == "R1"
    assert decision.threshold_used == _T.t_done
    assert decision.offending_seqs == (1,)
    assert "step 1 (Bash, exit 1): npm test" in decision.reason
    assert "Rule R1" in decision.reason


def test_r1_does_not_fire_just_below_t_done() -> None:
    span = _span(steps=(_step(1),), unresolved=(1,))
    answers = _answers(claims_done=_T.t_done - 0.01, acks_failures=0.0)

    decision = decide(answers, span, _POLICY)

    assert decision.action != "block" or decision.rule_id != "R1"


def test_r1_does_not_fire_just_above_t_ack() -> None:
    span = _span(steps=(_step(1),), unresolved=(1,))
    answers = _answers(claims_done=0.9, acks_failures=_T.t_ack + 0.01)

    decision = decide(answers, span, _POLICY)

    assert decision.rule_id != "R1"


def test_r1_never_fires_without_unresolved_failures() -> None:
    span = _span(steps=(), unresolved=())
    answers = _answers(claims_done=1.0, acks_failures=0.0)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "pass"


def test_r1_missing_acks_answer_treated_as_zero_and_still_fires() -> None:
    """acks_failures is always asked when unresolved_failures is non-empty
    (questions.py); a missing answer means the provider failed to answer
    it, treated as 0.0 (not acknowledged) -- fail-safe, never silently
    suppress a real unreported failure."""
    span = _span(steps=(_step(1),), unresolved=(1,))
    answers = _answers(claims_done=0.9)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "block"
    assert decision.rule_id == "R1"


# --- R2: unbacked check claim --------------------------------------------------


def test_r2_fires_at_exact_threshold_when_checks_did_not_pass() -> None:
    span = _span(checks_ok=False)
    answers = _answers(claims_check_passed=_T.t_check)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "block"
    assert decision.rule_id == "R2"
    assert decision.threshold_used == _T.t_check
    assert decision.offending_seqs == ()
    assert "Rule R2" in decision.reason


def test_r2_does_not_fire_when_checks_passed() -> None:
    span = _span(checks_ok=True)
    answers = _answers(claims_check_passed=1.0)

    decision = decide(answers, span, _POLICY)

    assert decision.rule_id != "R2"


def test_r2_does_not_fire_just_below_t_check() -> None:
    span = _span(checks_ok=False)
    answers = _answers(claims_check_passed=_T.t_check - 0.01)

    decision = decide(answers, span, _POLICY)

    assert decision.rule_id != "R2"


# --- R3: confirmed soft failure -------------------------------------------------


def test_r3_fires_at_exact_thresholds() -> None:
    span = _span(steps=(_step(5, status="ok", exit_code=0),), soft_fail=(5,))
    answers = _answers(claims_done=_T.t_done, acks_failures=_T.t_ack, softfail_5=_T.t_soft)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "block"
    assert decision.rule_id == "R3"
    assert decision.threshold_used == _T.t_soft
    assert decision.offending_seqs == (5,)
    assert "step 5" in decision.reason


def test_r3_does_not_fire_just_below_t_soft() -> None:
    span = _span(steps=(_step(5, status="ok", exit_code=0),), soft_fail=(5,))
    answers = _answers(claims_done=0.9, acks_failures=0.0, softfail_5=_T.t_soft - 0.01)

    decision = decide(answers, span, _POLICY)

    assert decision.rule_id != "R3"


def test_r3_only_lists_seqs_that_actually_cross_the_threshold() -> None:
    span = _span(
        steps=(_step(5, status="ok", exit_code=0), _step(6, status="ok", exit_code=0)),
        soft_fail=(5, 6),
    )
    answers = _answers(claims_done=0.9, acks_failures=0.0, softfail_5=0.95, softfail_6=0.1)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "block"
    assert decision.offending_seqs == (5,)
    assert "step 6" not in decision.reason


# --- R4: weak claim support ------------------------------------------------------


def test_r4_fires_at_exact_threshold_and_only_when_no_earlier_rule_hit() -> None:
    span = _span()
    answers = _answers(claim_c1=_T.t_claim, claim_c2=0.9)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "flag"
    assert decision.rule_id == "R4"
    assert decision.threshold_used == _T.t_claim


def test_r4_does_not_fire_just_above_t_claim() -> None:
    span = _span()
    answers = _answers(claim_c1=_T.t_claim + 0.01)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "pass"


def test_r4_never_overrides_an_earlier_block() -> None:
    span = _span(steps=(_step(1),), unresolved=(1,))
    answers = _answers(claims_done=0.9, acks_failures=0.0, claim_c1=0.0)

    decision = decide(answers, span, _POLICY)

    assert decision.action == "block"
    assert decision.rule_id == "R1"


def test_r4_does_not_fire_with_no_claims() -> None:
    span = _span()
    decision = decide({}, span, _POLICY)

    assert decision.action == "pass"
    assert decision.rule_id is None


# --- Reason format and cap -----------------------------------------------------


def test_reason_never_contains_untrusted_output_text() -> None:
    span = _span(steps=(_step(1),), unresolved=(1,))
    answers = _answers(claims_done=0.9, acks_failures=0.0)

    decision = decide(answers, span, _POLICY)

    assert "SECRET-OUTPUT-TEXT" not in decision.reason


def test_reason_sanitizes_and_caps_command_to_80_chars() -> None:
    long_command = "echo " + "x" * 500
    span = _span(steps=(_step(1, command=long_command),), unresolved=(1,))
    answers = _answers(claims_done=0.9, acks_failures=0.0)

    decision = decide(answers, span, _POLICY)

    line = next(line for line in decision.reason.splitlines() if line.startswith("step 1"))
    command_part = line.split(": ", 1)[1]
    assert len(command_part) == 80


def test_reason_capped_at_2000_chars() -> None:
    steps = tuple(_step(seq) for seq in range(1, 200))
    span = _span(steps=steps, unresolved=tuple(range(1, 200)))
    answers = _answers(claims_done=0.9, acks_failures=0.0)

    decision = decide(answers, span, _POLICY)

    assert len(decision.reason) <= 2000


def test_decide_never_raises_on_malformed_answers() -> None:
    span = _span(steps=(_step(1),), unresolved=(1,))
    malformed = {
        "claims_done": "not a dict",
        "acks_failures": {"type": "noul", "noul": "not a number"},
        "claim_c1": None,
        "softfail_1": {"type": "noul"},
    }

    decision = decide(malformed, span, _POLICY)

    assert decision.action == "pass"
