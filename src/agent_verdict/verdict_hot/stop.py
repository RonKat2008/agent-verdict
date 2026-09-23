"""Stop and SubagentStop orchestration (PLAN.md 5.3, 5.4; task-4-brief.md).

`handle(payload, policy, now, api_key, transport=None)` never raises: every
exception becomes an `action` row with `action="gate_unavailable"` and empty
stdout. `now` is the MONOTONIC start captured at hook entry, so the 2.5 s
Stop budget is spent from hook entry, not from `stop.handle` entry.

Order: stand-down checks (`_stand_down`) -> verification span -> G-STOP
gate (skip the provider unless evidence or `always_verify`) -> claims,
state, questions, provider selection (`_gate_and_prepare`) -> bounded,
retried provider call, `verdict` rows, `decide`, loop guard, one `action`
row (`_call_and_decide` / `_finalize_decision`).

Output contract (D-016, A8, A16): block `{"decision":"block","reason":...}`,
flag `{"systemMessage":"Verdict: ..."}`, pass nothing. Shadow mode and a
SubagentStop without `subagent_block` resolve to `pass` with `would_have`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import NamedTuple

from . import _stop_provider as sp
from . import _stop_rows as rows_mod
from . import claims as claims_mod
from . import guard as guard_mod
from . import ledger, verdict_policy
from . import span as span_mod
from .cassettes import MissingCassette
from .policy import Policy
from .provider import Preset, ProviderResult
from .questions import build_questions
from .span import Span, build_span
from .state import build_state
from .verdict_policy import Decision

_VALID_MODES = ("shadow", "enforce")


class StopOutcome(NamedTuple):
    stdout_json: str | None
    rows: tuple[dict[str, object], ...]
    outcome: str


class _Resolved(NamedTuple):
    action: str
    would_have: str | None
    stdout_json: str | None
    reason_hash: str | None
    guard_gate_reason: str | None


# --- Defensive payload readers (never raise on malformed input) ------------


def _str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _opt_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    return value if isinstance(value, bool) else False


def _list_len(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else 0


def _resolve_mode(policy: Policy) -> str:
    raw = os.environ.get("CLAUDE_PLUGIN_OPTION_MODE", "").strip().lower()
    if raw in _VALID_MODES:
        return raw
    configured = policy.mode.strip().lower()
    return configured if configured in _VALID_MODES else "shadow"


def _has_evidence(span: Span, claim_list: tuple[str, ...]) -> bool:
    return bool(
        span.unresolved_failures
        or span.soft_fail_seqs
        or claim_list
        or not span.checks_passed_after_last_change
    )


def _resolve_action(
    decision: Decision, ctx: _Ctx, policy: Policy, rows_so_far: list[dict[str, object]]
) -> _Resolved:
    reason_hash = (
        rows_mod.reason_hash(decision.rule_id, decision.offending_seqs)
        if decision.rule_id
        else None
    )

    if (ctx.is_subagent and not policy.stop.subagent_block) or ctx.mode != "enforce":
        return _Resolved("pass", decision.action, None, reason_hash, None)

    if decision.action == "block":
        assert reason_hash is not None
        guard_result = guard_mod.check(
            rows_so_far, ctx.session_id, ctx.prompt_id, ctx.agent_id, reason_hash, policy
        )
        if guard_result.allowed:
            return _Resolved(
                "block", None, rows_mod.block_stdout(decision.reason), reason_hash, None
            )
        return _Resolved("pass", "block", None, reason_hash, f"guard_{guard_result.reason}")

    if decision.action == "flag":
        return _Resolved("flag", None, rows_mod.flag_stdout(decision.reason), reason_hash, None)

    return _Resolved("pass", None, None, reason_hash, None)


def _finish(
    session_id: str,
    prompt_id: str | None,
    agent_id: str | None,
    mode: str,
    start: float,
    *,
    action: str,
    gate_reason: str | None,
    open_failures: list[str] | None = None,
) -> StopOutcome:
    hook_ms = (time.monotonic() - start) * 1000.0
    row = rows_mod.action_row(
        session_id,
        prompt_id,
        agent_id,
        action=action,
        would_have=None,
        rule_id=None,
        threshold_used=None,
        mode=mode,
        reason_hash=None,
        gate_reason=gate_reason,
        hook_ms=hook_ms,
        open_failures_list=open_failures or [],
    )
    ledger.append_row(row)
    return StopOutcome(None, (row,), action)


class _Ctx(NamedTuple):
    session_id: str
    prompt_id: str | None
    agent_id: str | None
    mode: str
    is_subagent: bool
    last_message: str
    start: float


class _Ready(NamedTuple):
    span: Span
    open_failures: list[str]
    claim_list: tuple[str, ...]
    gate_reason: str
    provider_name: str
    preset: Preset
    api_key: str
    state: object
    questions: Mapping[str, object]
    overflow: bool


def _sanitized_final_message(payload: Mapping[str, object]) -> str:
    """G2.5: the final message reaches us raw from the Stop payload; it must
    go through the same normalize+redact pipeline as the ledger row before
    claims are extracted or the state is built for the provider."""
    from . import textnorm

    return textnorm.sanitize(_str(payload, "last_assistant_message")).text


def _stand_down(
    payload: Mapping[str, object], policy: Policy, start: float
) -> tuple[StopOutcome | None, _Ctx, list[dict[str, object]]]:
    """The three pre-evidence stand-down checks (permission mode, background
    tasks, guard budget already spent) plus everything later phases need."""
    ctx = _Ctx(
        session_id=_str(payload, "session_id"),
        prompt_id=_opt_str(payload, "prompt_id"),
        agent_id=_opt_str(payload, "agent_id"),
        mode=_resolve_mode(policy),
        is_subagent=_str(payload, "hook_event_name") == "SubagentStop",
        last_message=_sanitized_final_message(payload),
        start=start,
    )
    args = (ctx.session_id, ctx.prompt_id, ctx.agent_id, ctx.mode, ctx.start)

    if _opt_str(payload, "permission_mode") == "plan":
        pass_row = _finish(*args, action="pass", gate_reason=span_mod.GATE_REASON_SKIPPED_PLAN_MODE)
        return pass_row, ctx, []
    if _list_len(payload, "background_tasks") > 0:
        pass_row = _finish(
            *args, action="pass", gate_reason=span_mod.GATE_REASON_SKIPPED_BACKGROUND
        )
        return pass_row, ctx, []

    rows_so_far = ledger.read_session(ctx.session_id)
    if _bool(payload, "stop_hook_active"):
        issued = guard_mod.blocks_issued(rows_so_far, ctx.session_id, ctx.prompt_id, ctx.agent_id)
        if issued >= guard_mod.limit(policy):
            pass_row = _finish(*args, action="pass", gate_reason=span_mod.GATE_REASON_SKIPPED_GUARD)
            return pass_row, ctx, rows_so_far
    return None, ctx, rows_so_far


def _gate_and_prepare(
    ctx: _Ctx, rows_so_far: list[dict[str, object]], policy: Policy, api_key_arg: str | None
) -> StopOutcome | _Ready:
    """G-STOP gate, then provider selection/key resolution, then state and
    questions -- everything needed before the provider is actually called."""
    args = (ctx.session_id, ctx.prompt_id, ctx.agent_id, ctx.mode, ctx.start)
    span = build_span(rows_so_far, ctx.prompt_id, policy)
    open_failures = rows_mod.open_failures(span)
    claim_list = claims_mod.extract_claims(ctx.last_message, policy)
    has_evidence = _has_evidence(span, claim_list)
    if not has_evidence and not policy.stop.always_verify:
        return _finish(*args, action="pass", gate_reason="no_evidence", open_failures=open_failures)
    gate_reason = "evidence" if has_evidence else "always_verify"

    provider_name = sp.resolve_provider_name(policy)
    if provider_name == "local-only":
        local_only_gate = span_mod.GATE_REASON_LOCAL_ONLY
        return _finish(
            *args, action="pass", gate_reason=local_only_gate, open_failures=open_failures
        )
    preset = sp.resolve_preset(provider_name)
    api_key = sp.resolve_api_key(preset, api_key_arg)
    if not api_key:
        no_key_gate = span_mod.GATE_REASON_NO_KEY
        return _finish(
            *args, action="gate_unavailable", gate_reason=no_key_gate, open_failures=open_failures
        )

    state, overflow = build_state(span, ctx.last_message, claim_list, policy)
    questions = build_questions(state)
    return _Ready(
        span=span,
        open_failures=open_failures,
        claim_list=claim_list,
        gate_reason=gate_reason,
        provider_name=provider_name,
        preset=preset,
        api_key=api_key,
        state=state,
        questions=questions,
        overflow=overflow,
    )


def _call_and_decide(
    ctx: _Ctx,
    ready: _Ready,
    rows_so_far: list[dict[str, object]],
    policy: Policy,
    transport: object,
) -> StopOutcome:
    args = (ctx.session_id, ctx.prompt_id, ctx.agent_id, ctx.mode, ctx.start)
    call_transport, transport_kind = sp.resolve_transport(transport)

    result = sp.call_provider(
        ready.state, ready.questions, ready.preset, ready.api_key, policy, ctx.start, call_transport
    )
    if not result.ok:
        return _finish(
            *args,
            action="gate_unavailable",
            gate_reason=f"provider_{result.error}",
            open_failures=ready.open_failures,
        )

    verdict_rows = rows_mod.verdict_rows(
        ctx.session_id,
        ctx.prompt_id,
        ctx.agent_id,
        ready.questions,
        result,
        ready.provider_name,
        policy,
        ready.span,
        transport_kind,
    )
    for row in verdict_rows:
        ledger.append_row(row)

    return _finalize_decision(ctx, ready, rows_so_far, policy, result, verdict_rows)


def _finalize_decision(
    ctx: _Ctx,
    ready: _Ready,
    rows_so_far: list[dict[str, object]],
    policy: Policy,
    result: ProviderResult,
    verdict_rows: list[dict[str, object]],
) -> StopOutcome:
    # Fix round 1 item 12: exact ids `state.build_state` numbered (c1, c2,
    # ...); `decide` only reads `claim_<id>` answers actually asked about.
    claim_ids = tuple(f"c{i}" for i in range(1, len(ready.claim_list) + 1))
    decision = verdict_policy.decide(result.answers, ready.span, policy, claim_ids)
    resolved = _resolve_action(decision, ctx, policy, rows_so_far)
    hook_ms = (time.monotonic() - ctx.start) * 1000.0
    action_row = rows_mod.action_row(
        ctx.session_id,
        ctx.prompt_id,
        ctx.agent_id,
        action=resolved.action,
        would_have=resolved.would_have,
        rule_id=decision.rule_id,
        threshold_used=decision.threshold_used,
        mode=ctx.mode,
        reason_hash=resolved.reason_hash,
        gate_reason=resolved.guard_gate_reason or ready.gate_reason,
        hook_ms=hook_ms,
        open_failures_list=ready.open_failures,
        compression_overflow=ready.overflow,
    )
    ledger.append_row(action_row)
    return StopOutcome(resolved.stdout_json, (*verdict_rows, action_row), resolved.action)


def _handle(
    payload: Mapping[str, object],
    policy: Policy,
    start: float,
    api_key_arg: str | None,
    transport: object,
) -> StopOutcome:
    outcome, ctx, rows_so_far = _stand_down(payload, policy, start)
    if outcome is not None:
        return outcome

    ready = _gate_and_prepare(ctx, rows_so_far, policy, api_key_arg)
    if isinstance(ready, StopOutcome):
        return ready

    args = (ctx.session_id, ctx.prompt_id, ctx.agent_id, ctx.mode, ctx.start)
    try:
        return _call_and_decide(ctx, ready, rows_so_far, policy, transport)
    except MissingCassette:
        cassette_gate = span_mod.GATE_REASON_CASSETTE_MISSING
        return _finish(
            *args,
            action="gate_unavailable",
            gate_reason=cassette_gate,
            open_failures=ready.open_failures,
        )


def _safe_gate_unavailable(payload: object, policy: Policy, start: float) -> StopOutcome:
    try:
        safe_payload: Mapping[str, object] = payload if isinstance(payload, Mapping) else {}
        session_id = _str(safe_payload, "session_id")
        prompt_id = _opt_str(safe_payload, "prompt_id")
        agent_id = _opt_str(safe_payload, "agent_id")
        mode = _resolve_mode(policy)
        return _finish(
            session_id,
            prompt_id,
            agent_id,
            mode,
            start,
            action="gate_unavailable",
            gate_reason="exception",
        )
    except Exception:  # noqa: BLE001 - the fallback itself must never raise
        return StopOutcome(None, (), "gate_unavailable")


def handle(
    payload: Mapping[str, object],
    policy: Policy,
    now: float,
    api_key: str | None,
    transport: object = None,
) -> StopOutcome:
    """Never raises (module docstring). `now` is a `time.monotonic()` value
    captured at hook entry, used only for budget math."""
    try:
        return _handle(payload, policy, now, api_key, transport)
    except Exception:  # noqa: BLE001 - fail-open contract (global-constraints.md)
        return _safe_gate_unavailable(payload, policy, now)


__all__ = ["StopOutcome", "handle"]
