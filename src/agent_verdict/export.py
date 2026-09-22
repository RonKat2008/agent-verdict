"""`verdict export --goldset --out <file>` (task-6-brief.md ruling 3).

Writes one derived, non-free-text row per `verdict` ledger row: identifiers
are hashed (`event_id_hash`, `contributor_id`, `sha256_of_raw`), the
question and its numeric answer, span-derived counts (`tool_counts`,
`n_steps`, `n_failures`, `exit_code`), and the containing stop's decision
(`action`, `would_have`, `rule_id`, `policy_version`). Nothing else --
`answer` keeps only a `noul` float or a `score` string from the fixed
completion-level set (never free text), `tool_counts` keys are either a
builtin tool name or the single bucket `"mcp"` (never a raw MCP server
name, which can carry an employer or product name -- fix round 1, I1), and
every string field is drawn from a closed vocabulary (hex ids, fixed
enums, a model-id pattern), so `redact()` and every `_redact_rules`
pattern are provably no-ops on the output (tested in
`tests/unit/test_export.py`, which recurses into every dict key and value,
not just top-level string fields).

**Per-stop attribution (fix round 1, C2).** A session can carry more than
one stop for the same `(prompt_id, agent_id)` key (a retry). Each
`verdict` row is attributed to the *next* `action` row that follows it in
file order for that same key -- never "whichever action row for this key
was written last" -- and its span is built only from the rows strictly
before that verdict group began (the same decision-time prefix
`replay.py` now uses, not the whole session file). A verdict group with no
following action row at all (a truncated or still-in-flight stop) is
never exported: there is no decision to attribute it to yet.

`hook_kind` is inferred as `"subagent-stop"` when the row's `agent_id` is
not null, else `"stop"` -- the ledger does not otherwise record which hook
name produced a `verdict` row, and `agent_id` is the one field Claude Code
only ever sets on a SubagentStop payload.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
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
_BUILTIN_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_MCP_TOOL_PREFIX = "mcp__"
_MCP_BUCKET_KEY = "mcp"
_OTHER_BUCKET_KEY = "other"
_OUTPUT_FILE_MODE = 0o600


def _contributor_id() -> str:
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


def _tool_bucket(tool_name: str) -> str:
    """A closed-vocabulary key for `tool_counts` (fix round 1, I1): a
    builtin tool name is kept as-is, every MCP tool (`mcp__<server>__
    <tool>`) is bucketed to the single key `"mcp"` regardless of what
    server or tool it names, and anything else unrecognized falls back to
    `"other"` rather than ever being echoed verbatim."""
    if tool_name.startswith(_MCP_TOOL_PREFIX):
        return _MCP_BUCKET_KEY
    if _BUILTIN_TOOL_NAME_RE.match(tool_name):
        return tool_name
    return _OTHER_BUCKET_KEY


class _SpanContext:
    __slots__ = ("tool_counts", "n_steps", "n_failures", "exit_code")

    def __init__(self, span: Span) -> None:
        tool_counts: dict[str, int] = {}
        n_failures = 0
        exit_code: int | None = None
        for step in span.steps:
            bucket = _tool_bucket(step.tool)
            tool_counts[bucket] = tool_counts.get(bucket, 0) + 1
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


def _export_row(
    session_id: str,
    verdict_row: Mapping[str, object],
    action_row: Mapping[str, object],
    span_ctx: _SpanContext,
) -> dict[str, Any] | None:
    question_type = verdict_row.get("question_type")
    answer_value = _answer_value(question_type, verdict_row.get("answer"))
    if answer_value is None:
        return None
    return {
        "event_id_hash": _event_id_hash(
            session_id,
            verdict_row.get("prompt_id"),
            verdict_row.get("agent_id"),
            verdict_row.get("question_key"),
        ),
        "contributor_id": _contributor_id(),
        "hook_kind": _hook_kind(verdict_row.get("agent_id")),
        "tool_counts": span_ctx.tool_counts,
        "question_key": verdict_row.get("question_key"),
        "question_type": question_type,
        "answer": answer_value,
        "exit_code": span_ctx.exit_code,
        "n_steps": span_ctx.n_steps,
        "n_failures": span_ctx.n_failures,
        "model_returned": verdict_row.get("model_returned"),
        "policy_version": verdict_row.get("policy_version"),
        "action": action_row.get("action"),
        "would_have": action_row.get("would_have"),
        "rule_id": action_row.get("rule_id"),
        "sha256_of_raw": _sha256_of_raw(verdict_row),
    }


def _export_session(
    session_id: str, rows: list[dict[str, object]], policy: Policy
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pending_verdicts: dict[tuple[object, object], list[dict[str, object]]] = {}
    verdict_start: dict[tuple[object, object], int] = {}

    for idx, row in enumerate(rows):
        event = row.get("event")
        key = (row.get("prompt_id"), row.get("agent_id"))

        if event == "verdict":
            if key not in pending_verdicts:
                verdict_start[key] = idx
            pending_verdicts.setdefault(key, []).append(row)
            continue

        if event != "action":
            continue

        # C2 (fix round 1): attribute to the NEXT action row in file order
        # for this key, never "the last one written anywhere in the
        # session" -- a retry's second stop must not steal credit (or
        # blame) for the first stop's verdict rows, and vice versa.
        group = pending_verdicts.pop(key, None)
        group_start = verdict_start.pop(key, idx)
        if group is None:
            continue

        prompt_id = row.get("prompt_id")
        pid = prompt_id if isinstance(prompt_id, str) else None
        span = span_mod.build_span(rows[:group_start], pid, policy)
        span_ctx = _SpanContext(span)

        for verdict_row in group:
            exported = _export_row(session_id, verdict_row, row, span_ctx)
            if exported is not None:
                out.append(exported)

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


def _write_goldset(out_path: Path, rows: list[dict[str, Any]]) -> None:
    """Writes `out_path` mode 0600 from creation (fix round 1, M7): the
    goldset is derived and redaction-checked, but it is still an export
    artifact that should never be left world- or group-readable by an
    inherited umask."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(out_path), flags, _OUTPUT_FILE_MODE)
    try:
        os.fchmod(fd, _OUTPUT_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


def run(args: argparse.Namespace) -> int:
    rows = export_goldset()
    out_path = Path(args.out)
    _write_goldset(out_path, rows)
    print(f"wrote {len(rows)} row(s) to {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


__all__ = ["export_goldset", "build_arg_parser", "run", "main"]
