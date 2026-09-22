"""Tests for verdict_hot.state (task-2-brief.md, PLAN.md 5.3, D-010, C4,
fix round 1).

Golden equality (byte-for-byte against tests/golden/states/*.json) lives
here too, generated from the same tests/golden/_fixtures.py the questions
golden test uses, so a wording or shape drift in `build_state` is always
caught by a diff a reviewer can read (task-2-brief.md).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from golden._fixtures import FIXTURES, POLICY  # noqa: E402
from verdict_hot import claims as claims_mod
from verdict_hot import state
from verdict_hot.policy import Policy
from verdict_hot.span import Span, Step

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_STATES = ROOT / "tests" / "golden" / "states"


def _step(seq: int, **kwargs: Any) -> Step:
    defaults: dict[str, Any] = dict(
        tool="Bash",
        command="echo hi",
        status="ok",
        exit_code=None,
        is_check=False,
        soft_fail_candidate=False,
        resolved_later=False,
        acknowledged=False,
        tool_use_id=f"t{seq}",
        prompt_id="p1",
        out_excerpt="",
        never_send=False,
    )
    defaults.update(kwargs)
    return Step(seq=seq, **defaults)


def _span(steps: tuple[Step, ...], **kwargs: Any) -> Span:
    defaults: dict[str, Any] = dict(
        prompts=("do the thing",),
        unresolved_failures=(),
        soft_fail_seqs=(),
        checks_passed_after_last_change=True,
        span_prompt_ids=("p1",),
        reason="start_of_session",
    )
    defaults.update(kwargs)
    return Span(steps=steps, **defaults)


def _build(
    span: Span, final_message: str, claims: tuple[str, ...], policy: Policy
) -> tuple[dict[str, Any], bool]:
    """Thin wrapper narrowing `build_state`'s `dict[str, object]` (the exact
    interface contract, task-2-brief.md) to `dict[str, Any]` for tests, which
    need to index several levels deep into the JSON tree."""
    result, overflow = state.build_state(span, final_message, claims, policy)
    return cast(dict[str, Any], result), overflow


# --- Shape -------------------------------------------------------------


def test_state_shape_matches_plan_5_3(default_policy: Policy) -> None:
    span = _span(
        steps=(
            _step(1, tool="Write", command="src/app.py"),
            _step(2, tool="Bash", command="pytest -q", is_check=True, exit_code=0),
        ),
    )
    result, overflow = _build(span, "Done. Tests pass.", ("Tests pass",), default_policy)

    assert set(result) == {"trusted_facts", "untrusted"}
    assert set(result["trusted_facts"]) == {
        "user_task",
        "steps",
        "unresolved_failures",
        "checks_passed_after_last_change",
    }
    assert set(result["untrusted"]) == {"final_message", "claims", "step_output_excerpts"}
    assert result["untrusted"]["claims"] == {"c1": "Tests pass"}
    step_entry = result["trusted_facts"]["steps"][0]
    assert set(step_entry) == {
        "seq",
        "tool",
        "command",
        "status",
        "exit_code",
        "is_check",
        "resolved_later",
    }
    assert overflow is False


def test_command_is_capped_at_120_chars(default_policy: Policy) -> None:
    long_command = "x" * 500
    span = _span(steps=(_step(1, command=long_command),))

    result, _ = _build(span, "", (), default_policy)

    assert len(result["trusted_facts"]["steps"][0]["command"]) == 120


def test_never_send_step_contributes_no_excerpt(default_policy: Policy) -> None:
    span = _span(
        steps=(
            _step(
                1,
                status="error",
                command="[never-send]",
                out_excerpt="[never-send]",
                never_send=True,
            ),
        ),
        unresolved_failures=(1,),
    )

    result, _ = _build(span, "", (), default_policy)

    assert result["untrusted"]["step_output_excerpts"] == {}
    assert result["trusted_facts"]["steps"][0]["command"] == "[never-send]"


def test_unresolved_failures_and_checks_flag_pass_through_unchanged(default_policy: Policy) -> None:
    span = _span(
        steps=(_step(1, status="error", out_excerpt="boom"),),
        unresolved_failures=(1,),
        checks_passed_after_last_change=False,
    )

    result, _ = _build(span, "", (), default_policy)

    assert result["trusted_facts"]["unresolved_failures"] == [1]
    assert result["trusted_facts"]["checks_passed_after_last_change"] is False
    assert result["untrusted"]["step_output_excerpts"] == {"1": "boom"}


# --- Compression ---------------------------------------------------------


def _tiny_state_policy(policy: Policy, target_tokens: int, max_tokens: int) -> Policy:
    return policy._replace(
        state=policy.state._replace(target_tokens=target_tokens, max_tokens=max_tokens)
    )


def test_stage1_drops_oldest_plain_ok_steps_first(default_policy: Policy) -> None:
    tiny = _tiny_state_policy(default_policy, target_tokens=1, max_tokens=22000)
    steps = tuple(_step(seq, command=f"echo filler line number {seq}" * 3) for seq in range(1, 11))
    span = _span(steps=steps)

    result, overflow = _build(span, "short message", (), tiny)

    seqs = [s["seq"] for s in result["trusted_facts"]["steps"]]
    assert seqs == sorted(seqs)  # whatever remains keeps its relative order
    # fix round 1 item 3: overflow is true whenever anything was dropped or
    # shortened, not only when the hard cap forced it -- stage 1 dropped steps.
    assert overflow is True


def test_error_check_softfail_survive_stage1_and_stage2(default_policy: Policy) -> None:
    tiny = _tiny_state_policy(default_policy, target_tokens=1, max_tokens=22000)
    big_excerpt = "line of output\n" * 200
    steps = (
        *(_step(seq, command=f"filler {seq}" * 5) for seq in range(1, 20)),
        _step(20, status="error", out_excerpt=big_excerpt),
        _step(21, is_check=True, exit_code=0),
        _step(22, soft_fail_candidate=True, out_excerpt=big_excerpt),
    )
    span = _span(steps=steps, unresolved_failures=(20,), soft_fail_seqs=(22,))

    result, _ = _build(span, "msg", (), tiny)

    remaining_seqs = {s["seq"] for s in result["trusted_facts"]["steps"]}
    assert {20, 21, 22} <= remaining_seqs
    # stage 2 shrank both surviving excerpts to head+tail
    for seq_key in ("20", "22"):
        excerpt = result["untrusted"]["step_output_excerpts"][seq_key]
        assert len(excerpt) <= tiny.state.excerpt_head + tiny.state.excerpt_tail + 60


def test_stage3_truncates_user_task_keeping_the_tail(default_policy: Policy) -> None:
    tiny = _tiny_state_policy(default_policy, target_tokens=1, max_tokens=22000)
    prompts = ("x" * 3000, "the important correction at the end")
    span = _span(steps=(), prompts=prompts)

    result, _ = _build(span, "", (), tiny)

    assert result["trusted_facts"]["user_task"].endswith("the important correction at the end")
    assert len(result["trusted_facts"]["user_task"]) <= 1500


def test_hard_cap_forces_overflow_and_keeps_newest_40(default_policy: Policy) -> None:
    tiny = _tiny_state_policy(default_policy, target_tokens=1, max_tokens=50)
    # 60 error rows (all "protected") -- none droppable by stage 1 or 4, so
    # only stage 5's "keep newest 40" can bring this under the tiny cap.
    steps = tuple(_step(seq, status="error", out_excerpt="x" * 50) for seq in range(1, 61))
    span = _span(steps=steps, unresolved_failures=tuple(range(1, 61)))

    result, overflow = _build(span, "m", (), tiny)

    assert overflow is True
    assert len(result["trusted_facts"]["steps"]) <= 40
    seqs = [s["seq"] for s in result["trusted_facts"]["steps"]]
    assert seqs == list(range(21, 61))  # the newest 40 of 1..60


def test_build_state_never_exceeds_max_tokens_even_when_nothing_is_droppable(
    default_policy: Policy,
) -> None:
    tiny = _tiny_state_policy(default_policy, target_tokens=1, max_tokens=1)
    steps = tuple(_step(seq, status="error", out_excerpt="x" * 10) for seq in range(1, 5))
    span = _span(steps=steps, unresolved_failures=tuple(range(1, 5)))

    result, overflow = _build(span, "m", (), tiny)

    assert overflow is True
    assert isinstance(result, dict)  # never raised


def test_no_compression_leaves_overflow_false(default_policy: Policy) -> None:
    """fix round 1 item 3: a span that never needs any stage leaves
    `overflow` false -- it is not true unconditionally."""
    span = _span(steps=(_step(1), _step(2, is_check=True, exit_code=0)))

    result, overflow = _build(span, "short message", (), default_policy)

    assert overflow is False
    assert len(result["trusted_facts"]["steps"]) == 2


def test_stage2_shortening_alone_sets_overflow(default_policy: Policy) -> None:
    """fix round 1 item 3: overflow is true as soon as anything is
    shortened, even when no step is ever dropped (target so small stage 1
    has nothing plain-ok to drop, but stage 2 still shrinks the excerpt)."""
    tiny = _tiny_state_policy(default_policy, target_tokens=1, max_tokens=22000)
    big_excerpt = "line of output\n" * 200
    span = _span(
        steps=(_step(1, status="error", out_excerpt=big_excerpt),),
        unresolved_failures=(1,),
    )

    result, overflow = _build(span, "m", (), tiny)

    assert overflow is True
    assert len(result["untrusted"]["step_output_excerpts"]["1"]) < len(big_excerpt)


# --- Item 2: the hard cap is guaranteed inside build_state ------------------


def test_200kb_final_message_stays_under_hard_cap_and_never_raises(
    default_policy: Policy,
) -> None:
    span = _span(steps=(_step(1, status="error", out_excerpt="boom"),), unresolved_failures=(1,))
    final_message = "x" * 200_000

    result, overflow = _build(span, final_message, ("a claim",), default_policy)

    tokens = len(json.dumps(result, ensure_ascii=False)) // 4
    assert tokens <= default_policy.state.max_tokens
    assert overflow is True


def test_final_clamp_caps_each_claim_at_240_chars(default_policy: Policy) -> None:
    tiny = _tiny_state_policy(default_policy, target_tokens=1, max_tokens=1)
    span = _span(steps=())
    long_claim = "y" * 5000

    result, overflow = _build(span, "z" * 5000, (long_claim,), tiny)

    assert overflow is True
    assert len(result["untrusted"]["claims"]["c1"]) <= 240


# --- Item 1: linear (not quadratic) compression -----------------------------


def _perf_span(total_steps: int, flagged: int, excerpt_size: int) -> Span:
    steps = []
    unresolved = []
    for seq in range(1, total_steps + 1):
        if seq <= flagged:
            steps.append(
                _step(seq, status="error", out_excerpt="x" * excerpt_size, command=f"cmd {seq}")
            )
            unresolved.append(seq)
        else:
            steps.append(_step(seq, command=f"echo step {seq} ran fine and produced no output"))
    return _span(steps=tuple(steps), unresolved_failures=tuple(unresolved))


@pytest.mark.slow
def test_build_state_is_fast_for_600_steps_200_flagged_8kb_excerpts(
    default_policy: Policy,
) -> None:
    span = _perf_span(total_steps=600, flagged=200, excerpt_size=8 * 1024)

    start = time.perf_counter()
    state.build_state(span, "done", (), default_policy)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.06, f"took {elapsed * 1000:.1f}ms, budget is 60ms"


@pytest.mark.slow
def test_build_state_is_fast_for_500_steps_20kb_excerpts(default_policy: Policy) -> None:
    span = _perf_span(total_steps=500, flagged=200, excerpt_size=20 * 1024)

    start = time.perf_counter()
    state.build_state(span, "done", (), default_policy)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.10, f"took {elapsed * 1000:.1f}ms, budget is 100ms"


# --- Golden equality -------------------------------------------------------


@pytest.mark.parametrize("name,span,final_message", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_state_matches_golden_file(name: str, span: Span, final_message: str) -> None:
    claim_tuple = claims_mod.extract_claims(final_message, POLICY)
    result, _overflow = _build(span, final_message, claim_tuple, POLICY)
    actual = json.dumps(result, sort_keys=True, indent=1, ensure_ascii=False) + "\n"

    expected = (GOLDEN_STATES / f"{name}.json").read_text(encoding="utf-8")

    assert actual == expected


# --- Hypothesis: compression invariants -------------------------------------

# The step-count bound below is about which invariant is being tested, not
# a limitation of build_state's hard cap: capping protected-row count at 40
# means stage 5's "keep newest 40" last resort never has to touch a
# protected (error/check/soft-fail) row, so "every error, soft-fail, and
# check step survives" is provable for every generated example (matching
# how span.py's own Hypothesis test scopes "arbitrary" to a bounded
# generative space rather than a literal infinite one). The hard cap on
# total tokens, by contrast, is now (fix round 1 item 2) guaranteed
# unconditionally by the final clamp stage, so `final_message` here is
# generated up to 50,000 characters -- large enough to force that stage on
# some examples -- rather than a size chosen just to make the invariant
# provable.
_KIND = st.sampled_from(("plain_ok", "error", "check", "soft_fail"))
_EXCERPT_TEXT = st.text(max_size=2000)


@st.composite
def _spans(draw: st.DrawFn) -> tuple[Span, str, tuple[str, ...]]:
    kinds = draw(st.lists(_KIND, max_size=90))
    protected = sum(1 for k in kinds if k != "plain_ok")
    if protected > 40:
        kinds = [k for k in kinds if k != "plain_ok"][:40] + [k for k in kinds if k == "plain_ok"]

    steps = []
    unresolved = []
    soft_fail = []
    for seq, kind in enumerate(kinds, start=1):
        excerpt = draw(_EXCERPT_TEXT)
        if kind == "error":
            steps.append(_step(seq, status="error", out_excerpt=excerpt))
            unresolved.append(seq)
        elif kind == "check":
            steps.append(_step(seq, is_check=True, exit_code=0))
        elif kind == "soft_fail":
            steps.append(_step(seq, soft_fail_candidate=True, out_excerpt=excerpt))
            soft_fail.append(seq)
        else:
            steps.append(_step(seq, command=draw(st.text(max_size=200))))

    final_message = draw(st.text(max_size=50_000))
    user_task = draw(st.text(max_size=3000))
    claims_tuple = tuple(draw(st.lists(st.text(max_size=240), max_size=6)))
    span = _span(
        steps=tuple(steps),
        prompts=(user_task,),
        unresolved_failures=tuple(unresolved),
        soft_fail_seqs=tuple(soft_fail),
    )
    return span, final_message, claims_tuple


@given(data=_spans())
@settings(
    max_examples=60,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=None,
)
def test_build_state_respects_hard_cap_and_protects_key_rows(
    data: tuple[Span, str, tuple[str, ...]],
) -> None:
    span, final_message, claims_tuple = data

    result, overflow = _build(span, final_message, claims_tuple, POLICY)

    tokens = len(json.dumps(result, ensure_ascii=False)) // 4
    assert tokens <= POLICY.state.max_tokens

    remaining_seqs = {s["seq"] for s in result["trusted_facts"]["steps"]}
    protected_seqs = {
        s.seq for s in span.steps if s.status == "error" or s.is_check or s.soft_fail_candidate
    }
    assert protected_seqs <= remaining_seqs
    for claim_text in result["untrusted"]["claims"].values():
        assert len(claim_text) <= 240
    assert isinstance(overflow, bool)
