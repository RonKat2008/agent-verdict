"""`verdict replay --policy <file> [--since <date>] [--json]`
(task-6-brief.md ruling 1; PLAN.md 5.5, the replay contract).

For each stored `action` row that carries a judgement (`_render.is_judged_action`: a real
provider evaluation, whether gated by evidence, forced by `always_verify`,
or demoted by the loop guard; stand-downs, `gate_unavailable`, and
exceptions are skipped and counted),
`replay` reconstructs the same inputs `stop.py` used to call
`verdict_policy.decide` and recomputes it under a given policy:

- **answers**: the `verdict` rows written immediately before this action
  row for the same `(session_id, prompt_id, agent_id)` key.
- **span**: rebuilt by `span.build_span`, but only over the rows that
  existed *before* this stop's own `verdict` rows began (`rows[:
  verdict_start]`) -- never the whole session file (fix round 1, C1). A
  later retry, or a later stop's `acks_failures` verdict, is real evidence
  for a *later* stop, but it did not exist yet when this stop actually
  ran, so it must never retroactively resolve a failure this stop saw as
  open. This is `stop.py`'s own `rows_so_far` (read once, before that
  stop's provider call), reconstructed from the ledger rather than
  re-derived by hand.
- **claim_ids**: `c1..cN` where `N` is the length of the `claims` list on
  the most recent `stop` row seen so far for this key -- the same list
  `state.build_state` numbered when the row was first written.

"Old" is the row's `would_have` when present (shadow mode, or an
enforce-mode decision resolved to something other than what actually
happened), else its `action`. `moved_by` is the first threshold, in the
fixed order `t_done, t_ack, t_check, t_soft, t_claim`, whose value differs
between the *shipped* packaged policy and the replay policy AND whose
relevant answer probability for this row falls between the two values;
`"-"` when nothing moved.

The "shipped" policy compared against is always the packaged default
(`policy.default_policy_path()`) -- this milestone ships exactly one
policy version, so there is no registry of historical shipped policies to
look a row's own `policy_version` up in. When a row's own recorded
`policy_version` (carried on its `verdict` rows) does not match the
packaged default's `policy_version`, `moved_by` is printed as `"?"`
instead of guessing at a threshold comparison against a policy that was
never actually shipped for that row (fix round 1, I3).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_verdict._render import is_judged_action
from agent_verdict.verdict_hot import ledger, verdict_policy
from agent_verdict.verdict_hot import policy as policy_mod
from agent_verdict.verdict_hot import span as span_mod
from agent_verdict.verdict_hot.policy import Policy
from agent_verdict.verdict_hot.span import Span

_THRESHOLD_ORDER = ("t_done", "t_ack", "t_check", "t_soft", "t_claim")
_QUESTION_KEY_FOR = {
    "t_done": "claims_done",
    "t_ack": "acks_failures",
    "t_check": "claims_check_passed",
}


def _noul_value(answers: Mapping[str, object], key: str) -> float | None:
    answer = answers.get(key)
    if not isinstance(answer, Mapping):
        return None
    value = answer.get("noul")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _probes_for(
    name: str, answers: Mapping[str, object], span: Span, claim_ids: tuple[str, ...]
) -> list[float]:
    fixed_key = _QUESTION_KEY_FOR.get(name)
    if fixed_key is not None:
        value = _noul_value(answers, fixed_key)
        return [value] if value is not None else []
    if name == "t_soft":
        keys = [f"softfail_{seq}" for seq in span.soft_fail_seqs]
    elif name == "t_claim":
        keys = [f"claim_{claim_id}" for claim_id in claim_ids]
    else:
        return []
    return [v for v in (_noul_value(answers, k) for k in keys) if v is not None]


def _moved_by(
    shipped: Policy,
    replay_policy: Policy,
    answers: Mapping[str, object],
    span: Span,
    claim_ids: tuple[str, ...],
) -> str:
    for name in _THRESHOLD_ORDER:
        old_value = getattr(shipped.thresholds, name)
        new_value = getattr(replay_policy.thresholds, name)
        if old_value == new_value:
            continue
        lo, hi = (old_value, new_value) if old_value <= new_value else (new_value, old_value)
        probes = _probes_for(name, answers, span, claim_ids)
        if any(lo <= probe <= hi for probe in probes):
            return name
    return "-"


def _old_action(row: Mapping[str, object]) -> object:
    would_have = row.get("would_have")
    return would_have if would_have is not None else row.get("action")


class SinceError(ValueError):
    """Raised for a malformed `--since` value."""


def _since_cutoff(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SinceError(f"--since must be an ISO date or datetime, got {raw!r}") from exc
    # A naive value (no tzinfo) is treated as already-UTC; an offset-aware
    # value is converted to UTC rather than having its own offset silently
    # discarded (fix round 1, M2).
    aware = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return aware.astimezone(UTC).timestamp()


class _ReplayRow:
    __slots__ = (
        "session_id",
        "prompt_id",
        "agent_id",
        "old",
        "new",
        "moved_by",
        "rule_id_old",
        "rule_id_new",
    )

    def __init__(
        self,
        session_id: str,
        prompt_id: str | None,
        agent_id: str | None,
        old: object,
        new: str,
        moved_by: str,
        rule_id_old: object,
        rule_id_new: str | None,
    ) -> None:
        self.session_id = session_id
        self.prompt_id = prompt_id
        self.agent_id = agent_id
        self.old = old
        self.new = new
        self.moved_by = moved_by
        self.rule_id_old = rule_id_old
        self.rule_id_new = rule_id_new

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "prompt_id": self.prompt_id,
            "agent_id": self.agent_id,
            "old": self.old,
            "new": self.new,
            "moved_by": self.moved_by,
            "rule_id_old": self.rule_id_old,
            "rule_id_new": self.rule_id_new,
        }

    def to_line(self) -> str:
        return (
            f"{self.session_id} {self.prompt_id} old={self.old} new={self.new} "
            f"moved_by={self.moved_by}"
        )


def _row_policy_version(verdict_rows: list[dict[str, object]]) -> str | None:
    for row in verdict_rows:
        version = row.get("policy_version")
        if isinstance(version, str):
            return version
    return None


def _replay_session(
    session_id: str,
    rows: list[dict[str, object]],
    shipped: Policy,
    replay_policy: Policy,
    since: float | None,
) -> tuple[list[_ReplayRow], int]:
    pending_verdicts: dict[tuple[object, object], list[dict[str, object]]] = {}
    verdict_start: dict[tuple[object, object], int] = {}
    stop_claim_counts: dict[tuple[object, object], int] = {}
    out: list[_ReplayRow] = []
    skipped = 0

    for idx, row in enumerate(rows):
        event = row.get("event")
        prompt_id = row.get("prompt_id")
        agent_id = row.get("agent_id")
        key = (prompt_id, agent_id)

        if event == "stop":
            claims = row.get("claims")
            stop_claim_counts[key] = len(claims) if isinstance(claims, list) else 0
            continue

        if event == "verdict":
            if key not in pending_verdicts:
                verdict_start[key] = idx
            pending_verdicts.setdefault(key, []).append(row)
            continue

        if event != "action":
            continue

        verdict_rows = pending_verdicts.pop(key, [])
        # C1 (fix round 1): the decision-time prefix. `idx` here is the
        # action row's own position, which would already include this
        # stop's OWN verdict rows (and any later stop's rows too, once we
        # get further down the file) -- only rows strictly before this
        # stop's verdict group began were visible when the real Stop hook
        # actually ran. Falls back to `idx` when there were no verdict
        # rows at all (a stand-down/gate_unavailable row), which is
        # filtered out below before the span is ever built.
        group_start = verdict_start.pop(key, idx)
        ts = row.get("ts")
        if since is not None and isinstance(ts, (int, float)) and ts < since:
            skipped += 1
            continue
        if not is_judged_action(row):
            skipped += 1
            continue

        answers: dict[str, object] = {
            key_str: r.get("answer")
            for r in verdict_rows
            if isinstance(key_str := r.get("question_key"), str)
        }
        claim_ids = tuple(f"c{i}" for i in range(1, stop_claim_counts.get(key, 0) + 1))
        pid_for_span = prompt_id if isinstance(prompt_id, str) else None
        span = span_mod.build_span(rows[:group_start], pid_for_span, replay_policy)
        decision = verdict_policy.decide(answers, span, replay_policy, claim_ids)

        row_policy_version = _row_policy_version(verdict_rows)
        if row_policy_version is not None and row_policy_version != shipped.policy_version:
            moved_by = "?"
        else:
            moved_by = _moved_by(shipped, replay_policy, answers, span, claim_ids)

        out.append(
            _ReplayRow(
                session_id=session_id,
                prompt_id=prompt_id if isinstance(prompt_id, str) or prompt_id is None else None,
                agent_id=agent_id if isinstance(agent_id, str) or agent_id is None else None,
                old=_old_action(row),
                new=decision.action,
                moved_by=moved_by,
                rule_id_old=row.get("rule_id"),
                rule_id_new=decision.rule_id,
            )
        )

    return out, skipped


def replay(replay_policy: Policy, since: float | None = None) -> tuple[list[_ReplayRow], int]:
    shipped = policy_mod.load_policy(policy_mod.default_policy_path())
    rows_out: list[_ReplayRow] = []
    skipped_total = 0
    for session_path in ledger.iter_sessions():
        session_id = session_path.stem
        rows = ledger.read_session(session_id)
        replayed, skipped = _replay_session(session_id, rows, shipped, replay_policy, since)
        rows_out.extend(replayed)
        skipped_total += skipped
    return rows_out, skipped_total


def build_arg_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verdict replay",
        description="Re-evaluate stored verdicts under a policy",
        add_help=add_help,
    )
    parser.add_argument("--policy", required=True, help="path to the policy file to replay under")
    parser.add_argument("--since", default=None, help="only rows at or after this ISO date")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per line")
    return parser


def run(args: argparse.Namespace) -> int:
    replay_policy = policy_mod.load_policy(Path(args.policy))
    try:
        since = _since_cutoff(args.since)
    except SinceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rows, skipped = replay(replay_policy, since)

    for row in rows:
        print(json.dumps(row.to_dict(), sort_keys=True) if args.json else row.to_line())
    print(f"skipped={skipped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


__all__ = ["replay", "build_arg_parser", "run", "main", "SinceError"]
