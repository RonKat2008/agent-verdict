"""`verdict export --goldset --out <file>` (task-6-brief.md ruling 3).

Writes one derived, non-free-text row per `verdict` ledger row: identifiers
are hashed (`event_id_hash`, `contributor_id`, `sha256_of_raw`), the
question and its numeric answer, span-derived counts (`tool_counts`,
`n_steps`, `n_failures`, `exit_code`), and the containing stop's decision
(`action`, `would_have`, `rule_id`, `policy_version`). Nothing else --
`answer` keeps only a `noul` float or a `score` string from the fixed
completion-level set (never free text), and every string field is drawn
from a closed vocabulary (hex ids, fixed enums, a model-id pattern), so
`redact()` and every `_redact_rules` pattern are provably no-ops on the
output (tested in `tests/unit/test_export.py`).

`hook_kind` is inferred as `"subagent-stop"` when the row's `agent_id` is
not null, else `"stop"` -- the ledger does not otherwise record which hook
name produced a `verdict` row, and `agent_id` is the one field Claude Code
only ever sets on a SubagentStop payload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_verdict.verdict_hot import ledger
from agent_verdict.verdict_hot import policy as policy_mod
from agent_verdict.verdict_hot import span as span_mod
from agent_verdict.verdict_hot.policy import Policy
from agent_verdict.verdict_hot.span import Span

_SCORE_SET = frozenset({"not_started", "partial", "mostly_complete", "complete"})
_CONTRIBUTOR_SALT_ENV = "VERDICT_CONTRIBUTOR_SALT"


def _contributor_id() -> str:
    salt = None
    import os

    salt = os.environ.get(_CONTRIBUTOR_SALT_ENV)
    if not salt:
        return "anonymous"
    hostname = socket.gethostname()
    digest = hashlib.sha256((hostname + salt).encode("utf-8")).hexdigest()
    return digest[:16]


def _event_id_hash(
    session_id: object, prompt_id: object, agent_id: object, question_key: object
) -> str:
    payload = f"{session_id}|{prompt_id}|{agent_id}|{question_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_of_raw(row: Mapping[str, object]) -> str:
    canonical = json.dumps(dict(row), separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _answer_value(question_type: object, answer: object) -> float | str | None:
    if not isinstance(answer, Mapping):
        return None
    if question_type == "noul":
        value = answer.get("noul")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)
    if question_type == "score":
        value = answer.get("score")
        return value if isinstance(value, str) and value in _SCORE_SET else None
    return None


class _SpanContext:
    __slots__ = ("tool_counts", "n_steps", "n_failures", "exit_code")

    def __init__(self, span: Span) -> None:
        tool_counts: dict[str, int] = {}
        n_failures = 0
        exit_code: int | None = None
        for step in span.steps:
            tool_counts[step.tool] = tool_counts.get(step.tool, 0) + 1
            if step.status == "error":
                n_failures += 1
                if exit_code is None:
                    exit_code = step.exit_code
        self.tool_counts = tool_counts
        self.n_steps = len(span.steps)
        self.n_failures = n_failures
        self.exit_code = exit_code


def _hook_kind(agent_id: object) -> str:
    return "subagent-stop" if isinstance(agent_id, str) and agent_id else "stop"


def _export_session(
    session_id: str, rows: list[dict[str, object]], policy: Policy
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    action_by_key: dict[tuple[object, object], dict[str, object]] = {}
    span_by_prompt: dict[str | None, _SpanContext] = {}

    for row in rows:
        if row.get("event") == "action":
            key = (row.get("prompt_id"), row.get("agent_id"))
            action_by_key[key] = row

    for row in rows:
        if row.get("event") != "verdict":
            continue
        key = (row.get("prompt_id"), row.get("agent_id"))
        prompt_id = row.get("prompt_id")
        pid = prompt_id if isinstance(prompt_id, str) else None
        if pid not in span_by_prompt:
            span_by_prompt[pid] = _SpanContext(span_mod.build_span(rows, pid, policy))
        span_ctx = span_by_prompt[pid]
        action_row = action_by_key.get(key, {})
        question_type = row.get("question_type")
        answer_value = _answer_value(question_type, row.get("answer"))
        if answer_value is None:
            continue
        out.append(
            {
                "event_id_hash": _event_id_hash(
                    session_id, row.get("prompt_id"), row.get("agent_id"), row.get("question_key")
                ),
                "contributor_id": _contributor_id(),
                "hook_kind": _hook_kind(row.get("agent_id")),
                "tool_counts": span_ctx.tool_counts if span_ctx else {},
                "question_key": row.get("question_key"),
                "question_type": question_type,
                "answer": answer_value,
                "exit_code": span_ctx.exit_code if span_ctx else None,
                "n_steps": span_ctx.n_steps if span_ctx else 0,
                "n_failures": span_ctx.n_failures if span_ctx else 0,
                "model_returned": row.get("model_returned"),
                "policy_version": row.get("policy_version"),
                "action": action_row.get("action"),
                "would_have": action_row.get("would_have"),
                "rule_id": action_row.get("rule_id"),
                "sha256_of_raw": _sha256_of_raw(row),
            }
        )
    return out


def export_goldset(policy: Policy | None = None) -> list[dict[str, Any]]:
    active_policy = policy or policy_mod.load_policy(policy_mod.default_policy_path())
    rows_out: list[dict[str, Any]] = []
    for session_path in ledger.iter_sessions():
        session_id = session_path.stem
        rows = ledger.read_session(session_id)
        rows_out.extend(_export_session(session_id, rows, active_policy))
    return rows_out


def build_arg_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verdict export",
        description="Export a derived, redaction-safe goldset",
        add_help=add_help,
    )
    parser.add_argument("--goldset", action="store_true", required=True, help="export the goldset")
    parser.add_argument("--out", required=True, help="output JSONL file path")
    return parser


def run(args: argparse.Namespace) -> int:
    rows = export_goldset()
    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} row(s) to {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


__all__ = ["export_goldset", "build_arg_parser", "run", "main"]
