"""Tests for verdict_hot.span (task-1-brief.md, PLAN.md 5.3, D-020).

Row builders below construct plain dicts with only the fields span.py
reads (never a full ledger row) -- exercising the exact field names the
module docstring documents for `action`/`verdict` rows (Task 4's
interface), since this module must already understand them correctly.

The `_POLICY` constant loads the real packaged default (not the
`default_policy`/`isolated_verdict_home` fixtures) so the Hypothesis test
below never touches a function-scoped fixture -- Hypothesis's own health
check flags that pattern, and there is no need for env isolation here
since `load_policy(path=...)` never reads `~/.verdict/policy.json`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from verdict_hot import policy as policy_mod
from verdict_hot import span

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
_POLICY = policy_mod.load_policy(PACKAGED_DEFAULT)


# --- Row builders -----------------------------------------------------------


def _prompt(prompt_id: str, text: str = "do the thing") -> dict[str, Any]:
    return {"event": "prompt", "prompt_id": prompt_id, "prompt_excerpt": text}


def _post(
    prompt_id: str | None,
    tool: str = "Bash",
    command: str = "npm test",
    tool_use_id: str = "t1",
    status: str = "ok",
    is_check: bool = False,
    soft_fail: bool = False,
    never_send: bool = False,
    out_head: str = "",
    out_tail: str = "",
) -> dict[str, Any]:
    return {
        "event": "post",
        "prompt_id": prompt_id,
        "tool_name": tool,
        "tool_use_id": tool_use_id,
        "input_excerpt": command,
        "status": status,
        "is_check": is_check,
        "soft_fail_candidate": soft_fail,
        "never_send": never_send,
        "out_head": out_head,
        "out_tail": out_tail,
    }


def _post_fail(
    prompt_id: str | None,
    tool: str = "Bash",
    command: str = "npm test",
    tool_use_id: str = "f1",
    exit_code: int | None = 1,
    error: str = "boom",
) -> dict[str, Any]:
    return {
        "event": "post_fail",
        "prompt_id": prompt_id,
        "tool_name": tool,
        "tool_use_id": tool_use_id,
        "input_excerpt": command,
        "status": "error",
        "exit_code": exit_code,
        "is_check": False,
        "never_send": False,
        "error_excerpt": error,
    }


def _action(
    prompt_id: str | None,
    action_value: str = "pass",
    open_failures: list[str] | None = None,
    gate_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "event": "action",
        "prompt_id": prompt_id,
        "action": action_value,
        "open_failures": [] if open_failures is None else open_failures,
        "gate_reason": gate_reason,
    }


def _verdict(
    prompt_id: str | None,
    question_key: str = "acks_failures",
    noul: float = 0.9,
    listed: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "event": "verdict",
        "prompt_id": prompt_id,
        "question_key": question_key,
        "answer": {"noul": noul},
        "listed_failures": [] if listed is None else listed,
    }


def _session_start(source: str = "startup") -> dict[str, Any]:
    return {"event": "session_start", "prompt_id": None, "source": source}


# --- Span walk-back boundaries -----------------------------------------------


def test_span_stops_at_a_clean_stop() -> None:
    """Fix round 1 (Critical): a clean stop is a BOUNDARY, not a member.

    p2's own clean stop means p2 (and everything before it) was already
    judged and passed -- the span for p3 is p3 alone.
    """
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f_old"),
        _prompt("p2"),
        _post("p2", command="echo hi", tool_use_id="e1"),
        _action("p2", action_value="pass", open_failures=[]),
        _prompt("p3"),
        _post("p3", command="echo done", tool_use_id="e2"),
    ]

    result = span.build_span(rows, "p3", _POLICY)

    assert result.span_prompt_ids == ("p3",)
    assert result.reason == "clean_stop"
    assert all(step.prompt_id not in ("p1", "p2") for step in result.steps)
    assert result.unresolved_failures == ()


def test_prompt_with_its_own_clean_stop_and_a_failure_is_excluded() -> None:
    """Reviewer's sharper reproduction (fix round 1): prompt A holds both a
    failure and its own clean-stop `action` row. A is a boundary the walk
    stops at, not a member -- its failure must never be re-litigated from
    B's span."""
    rows = [
        _prompt("A"),
        _post_fail("A", command="pytest", tool_use_id="fA"),
        _action("A", action_value="pass", open_failures=[]),
        _prompt("B"),
        _post("B", command="echo hi", tool_use_id="eB"),
    ]

    result = span.build_span(rows, "B", _POLICY)

    assert result.span_prompt_ids == ("B",)
    assert result.reason == "clean_stop"
    assert all(step.prompt_id != "A" for step in result.steps)
    assert result.unresolved_failures == ()


def test_a_stand_down_action_row_is_never_a_clean_stop() -> None:
    """Fix round 1 item 2 (Critical): a plan-mode stand-down's `action="pass"`
    row must not be read as a clean stop -- the verifier never actually ran,
    so an earlier failure must still carry forward into the next span."""
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f1"),
        _action("p1", action_value="pass", gate_reason=span.GATE_REASON_SKIPPED_PLAN_MODE),
        _prompt("p2"),
        _post("p2", command="echo hi", tool_use_id="e1"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    assert result.reason != "clean_stop"
    assert result.span_prompt_ids == ("p1", "p2")
    assert result.unresolved_failures != ()


def test_a_real_no_evidence_pass_is_still_a_clean_stop() -> None:
    """Fix round 1 item 2: a real evaluated pass (the verifier ran, found
    nothing to judge or nothing wrong) IS a legitimate clean-stop boundary
    -- only the stand-down `gate_reason` values are excluded."""
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f1"),
        _action("p1", action_value="pass", gate_reason="no_evidence_needed"),
        _prompt("p2"),
        _post("p2", command="echo hi", tool_use_id="e1"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    assert result.reason == "clean_stop"
    assert result.span_prompt_ids == ("p2",)
    assert result.unresolved_failures == ()


def test_span_stops_at_the_five_prompt_limit() -> None:
    rows: list[dict[str, Any]] = []
    for n in range(1, 7):
        pid = f"p{n}"
        rows.append(_prompt(pid))
        rows.append(_post(pid, command=f"echo {pid}", tool_use_id=f"e{n}"))

    result = span.build_span(rows, "p6", _POLICY)

    assert result.span_prompt_ids == ("p2", "p3", "p4", "p5", "p6")
    assert result.reason == "max_prompts"
    assert all(step.prompt_id != "p1" for step in result.steps)


def test_span_stops_at_a_clear() -> None:
    rows = [
        _prompt("p1"),
        _post("p1", command="echo p1", tool_use_id="e1"),
        _session_start(source="clear"),
        _prompt("p2"),
        _post("p2", command="echo p2", tool_use_id="e2"),
        _prompt("p3"),
        _post("p3", command="echo p3", tool_use_id="e3"),
    ]

    result = span.build_span(rows, "p3", _POLICY)

    assert result.span_prompt_ids == ("p2", "p3")
    assert result.reason == "clear"
    assert all(step.prompt_id != "p1" for step in result.steps)


def test_prompt_not_in_ledger_yields_empty_span() -> None:
    result = span.build_span([], "ghost", _POLICY)

    assert result.span_prompt_ids == ()
    assert result.steps == ()
    assert result.reason == "prompt_not_found"


# --- Resolution, acknowledgement, and carry-forward --------------------------


def test_failure_before_a_correction_prompt_is_still_open() -> None:
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f1"),
        _prompt("p2"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    failing = next(s for s in result.steps if s.tool_use_id == "f1")
    assert failing.resolved_later is False
    assert failing.acknowledged is False
    assert failing.seq in result.unresolved_failures


def test_same_command_later_succeeding_resolves_it() -> None:
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f1"),
        _prompt("p2"),
        _post("p2", command="pytest", tool_use_id="f2", status="ok"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    failing = next(s for s in result.steps if s.tool_use_id == "f1")
    assert failing.resolved_later is True
    assert failing.seq not in result.unresolved_failures


def test_resolution_strips_leading_var_assignment_and_wrapper() -> None:
    """gates.py-style normalization: `FOO=1 npx pytest` resolves a plain `pytest`."""
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f1"),
        _prompt("p2"),
        _post("p2", command="FOO=1 npx pytest", tool_use_id="f2", status="ok"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    failing = next(s for s in result.steps if s.tool_use_id == "f1")
    assert failing.resolved_later is True


def test_file_tool_resolution_compares_input_excerpt_path() -> None:
    rows = [
        _prompt("p1"),
        _post_fail("p1", tool="Write", command="/repo/broken.py", tool_use_id="f1"),
        _prompt("p2"),
        _post("p2", tool="Write", command="/repo/broken.py", tool_use_id="f2", status="ok"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    failing = next(s for s in result.steps if s.tool_use_id == "f1")
    assert failing.resolved_later is True


def test_acknowledged_failure_does_not_reappear() -> None:
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f1"),
        _verdict("p1", question_key="acks_failures", noul=0.9, listed=["f1"]),
        _prompt("p2"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    failing = next(s for s in result.steps if s.tool_use_id == "f1")
    assert failing.acknowledged is True
    assert failing.seq not in result.unresolved_failures


def test_ack_score_below_threshold_does_not_acknowledge() -> None:
    rows = [
        _prompt("p1"),
        _post_fail("p1", command="pytest", tool_use_id="f1"),
        _verdict("p1", question_key="acks_failures", noul=0.5, listed=["f1"]),
        _prompt("p2"),
    ]

    result = span.build_span(rows, "p2", _POLICY)

    failing = next(s for s in result.steps if s.tool_use_id == "f1")
    assert failing.acknowledged is False
    assert failing.seq in result.unresolved_failures


def test_unresolved_soft_fail_candidate_is_tracked_separately() -> None:
    rows = [
        _prompt("p1"),
        _post("p1", command="npm test", tool_use_id="s1", status="ok", soft_fail=True),
    ]

    result = span.build_span(rows, "p1", _POLICY)

    soft_step = next(s for s in result.steps if s.tool_use_id == "s1")
    assert soft_step.seq in result.soft_fail_seqs
    assert soft_step.seq not in result.unresolved_failures


# --- checks_passed_after_last_change -----------------------------------------


def test_checks_passed_after_last_change_true() -> None:
    rows = [
        _prompt("p1"),
        _post("p1", tool="Write", command="/f.py", tool_use_id="w1"),
        _post("p1", command="pytest -q", tool_use_id="c1", is_check=True, status="ok"),
    ]

    result = span.build_span(rows, "p1", _POLICY)

    assert result.checks_passed_after_last_change is True


def test_checks_passed_after_last_change_false() -> None:
    rows = [
        _prompt("p1"),
        _post("p1", tool="Write", command="/f.py", tool_use_id="w1"),
    ]

    result = span.build_span(rows, "p1", _POLICY)

    assert result.checks_passed_after_last_change is False


def test_checks_passed_defaults_true_with_no_mutation() -> None:
    rows = [
        _prompt("p1"),
        _post("p1", command="pytest -q", tool_use_id="c1", is_check=True, status="ok"),
    ]

    result = span.build_span(rows, "p1", _POLICY)

    assert result.checks_passed_after_last_change is True


def test_checks_passed_false_when_check_precedes_later_mutation() -> None:
    rows = [
        _prompt("p1"),
        _post("p1", command="pytest -q", tool_use_id="c1", is_check=True, status="ok"),
        _post("p1", tool="Write", command="/f.py", tool_use_id="w1"),
    ]

    result = span.build_span(rows, "p1", _POLICY)

    assert result.checks_passed_after_last_change is False


# --- Null prompt_id bucket ----------------------------------------------------


def test_null_prompt_id_forms_its_own_bucket() -> None:
    rows = [
        _session_start(),
        _post(None, command="echo hi", tool_use_id="x1"),
        _prompt("p1"),
        _post("p1", command="echo bye", tool_use_id="x2"),
    ]

    result = span.build_span(rows, None, _POLICY)

    assert result.span_prompt_ids == (None,)
    assert [s.tool_use_id for s in result.steps] == ["x1"]
    assert result.reason == "null_prompt_id"


def test_null_prompt_id_with_no_matching_rows_is_empty() -> None:
    rows = [_prompt("p1"), _post("p1", tool_use_id="x2")]

    result = span.build_span(rows, None, _POLICY)

    assert result.span_prompt_ids == ()
    assert result.steps == ()


# --- Performance and robustness ----------------------------------------------


@pytest.mark.slow
def test_builds_500_row_session_under_15ms() -> None:
    rows: list[dict[str, Any]] = []
    for n in range(1, 101):
        pid = f"p{n}"
        rows.append(_prompt(pid))
        rows.append(_post(pid, command=f"echo {n}", tool_use_id=f"e{n}a"))
        rows.append(_post(pid, command=f"cat {n}", tool_use_id=f"e{n}b"))
        rows.append(_post_fail(pid, command=f"boom {n}", tool_use_id=f"e{n}c"))
        rows.append(_action(pid, action_value="continue"))
    assert len(rows) == 500

    start = time.perf_counter()
    span.build_span(rows, "p100", _POLICY)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.015, f"build_span took {elapsed * 1000:.2f}ms, budget is 15ms"


_ROW_VALUE = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False),
    st.text(max_size=30),
    st.lists(st.text(max_size=10), max_size=5),
    st.dictionaries(st.text(max_size=10), st.text(max_size=10), max_size=3),
)
_ROW_STRATEGY = st.dictionaries(st.text(max_size=15), _ROW_VALUE, max_size=8)


@given(
    rows=st.lists(_ROW_STRATEGY, max_size=25),
    current_prompt_id=st.one_of(st.none(), st.text(max_size=15)),
)
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
def test_build_span_never_raises_on_arbitrary_rows(
    rows: list[dict[str, Any]], current_prompt_id: str | None
) -> None:
    span.build_span(rows, current_prompt_id, _POLICY)
