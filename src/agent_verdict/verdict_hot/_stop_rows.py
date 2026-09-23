"""`verdict`/`action` ledger row construction for stop.py (split out to stay
under the 400-line file cap, task-4-brief.md).

Both row shapes are added to `schemas/ledger-v1.json`. A `verdict` row's
`answer` field keeps only the field its own `question_type` defines
(`noul`, `score`, or `choice`) -- never a provider's extra metadata
(`legend`, `probabilities`, `confidence`, seen in real System One
responses), so nothing beyond the judged numeric value is ever persisted.
An `action` row's `reason` text is never stored here: only `rule_id` and
`threshold_used` (facts about *which* rule fired), never the formatted
block/flag text itself, which is emitted on stdout only (D-016) and is
reconstructable later from the ledger's own step facts plus `rule_id`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping

from . import PLUGIN_VERSION, SCHEMA_V
from . import provider as provider_mod
from .policy import Policy
from .span import Span


def common_row(
    event: str, session_id: str, prompt_id: str | None, agent_id: str | None
) -> dict[str, object]:
    return {
        "schema_v": SCHEMA_V,
        "ts": time.time(),
        "event": event,
        "session_id": session_id,
        "prompt_id": prompt_id,
        "agent_id": agent_id,
        "plugin_version": PLUGIN_VERSION,
    }


def open_failures(span: Span) -> list[str]:
    return _tool_ids(span, span.unresolved_failures)


def _tool_ids(span: Span, seqs: tuple[int, ...]) -> list[str]:
    by_seq = {s.seq: s for s in span.steps}
    return [by_seq[seq].tool_use_id for seq in seqs if seq in by_seq and by_seq[seq].tool_use_id]


def listed_failures(span: Span) -> list[str]:
    """Tool ids of only the seqs the `acks_failures` question named (final
    review I6), so a high answer acknowledges nothing the model never saw."""
    from .questions import listed_failure_seqs

    return _tool_ids(span, listed_failure_seqs(span.unresolved_failures))


def action_row(
    session_id: str,
    prompt_id: str | None,
    agent_id: str | None,
    *,
    action: str,
    would_have: str | None,
    rule_id: str | None,
    threshold_used: float | None,
    mode: str,
    reason_hash: str | None,
    gate_reason: str | None,
    hook_ms: float,
    open_failures_list: list[str],
    compression_overflow: bool | None = None,
) -> dict[str, object]:
    row = common_row("action", session_id, prompt_id, agent_id)
    row.update(
        {
            "action": action,
            "would_have": would_have,
            "rule_id": rule_id,
            "threshold_used": threshold_used,
            "mode": mode,
            "reason_hash": reason_hash,
            "gate_reason": gate_reason,
            "hook_ms": hook_ms,
            "open_failures": open_failures_list,
            "compression_overflow": compression_overflow,
        }
    )
    return row


def _answer_only(question: object, answer: object) -> dict[str, object]:
    qtype = question.get("type") if isinstance(question, Mapping) else None
    if not isinstance(answer, Mapping):
        return {"type": qtype}
    if qtype == "noul":
        return {"type": "noul", "noul": answer.get("noul")}
    if qtype == "score":
        return {"type": "score", "score": answer.get("score")}
    if qtype == "choice":
        return {"type": "choice", "choice": answer.get("choice")}
    return {"type": qtype}


def verdict_rows(
    session_id: str,
    prompt_id: str | None,
    agent_id: str | None,
    questions: Mapping[str, object],
    result: provider_mod.ProviderResult,
    provider_name: str,
    policy: Policy,
    span: Span,
    transport_kind: str = "live",
) -> list[dict[str, object]]:
    listed = listed_failures(span)
    rows: list[dict[str, object]] = []
    for key, question in questions.items():
        row = common_row("verdict", session_id, prompt_id, agent_id)
        row.update(
            {
                "question_key": key,
                "question_type": question.get("type") if isinstance(question, Mapping) else None,
                "answer": _answer_only(question, result.answers.get(key)),
                "provider": provider_name,
                "model_returned": result.model_returned,
                "input_tokens": result.input_tokens,
                "conn_ms": result.conn_ms,
                "infer_ms": result.infer_ms,
                "policy_version": policy.policy_version,
                "transport": transport_kind,
            }
        )
        if key == "acks_failures":
            row["listed_failures"] = listed
        rows.append(row)
    return rows


def reason_hash(rule_id: str, offending_seqs: tuple[int, ...]) -> str:
    payload = f"{rule_id}:{','.join(str(s) for s in sorted(offending_seqs))}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def block_stdout(reason: str) -> str:
    return json.dumps({"decision": "block", "reason": reason}, separators=(",", ":")) + "\n"


def flag_stdout(reason: str) -> str:
    return json.dumps({"systemMessage": f"Verdict: {reason}"}, separators=(",", ":")) + "\n"


__all__ = [
    "action_row",
    "block_stdout",
    "common_row",
    "flag_stdout",
    "open_failures",
    "reason_hash",
    "verdict_rows",
]
