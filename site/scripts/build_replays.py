"""ledger.jsonl -> site/src/data/scenarios/<name>.json (closed shape).

`build_from(scenario_dir)` reads `scenario.json`, `recording.json`, and
`ledger.jsonl` from one scenario directory and returns the closed-shape
bundle `replay_schema.py` defines. `main` builds every recorded scenario
under `site/scenarios/<name>/` into `site/src/data/scenarios/<name>.json`
plus an `index.json`; `--fixture` instead builds the one committed fixture
scenario under `tests/fixtures/site/unreported-failure/` into
`site/src/data/fixtures/unreported-failure.json`, so the demo site's hero
(Task 4) has something real to render before the billed recorder
(`scripts/record_site_scenarios.py`) has ever been run.

`_last_decision_group` finds the same "decision-time prefix" `stop.py`
itself used for the FINAL decision in the file, mirroring
`src/agent_verdict/replay.py`'s `_replay_session` (a later row is real
evidence for a later stop, never for this one): it keys rows by
`(prompt_id, agent_id)`, takes the LAST `action` row (a session can hold a
retry with two stops under one prompt), the last `stop` row sharing its
key, and that key's own `verdict` rows -- never merged across the whole
session. `_events`, by contrast, is built from every row in the file (all
retries, all stops), since the replay player shows the whole timeline; only
the `decision`/`questions`/`final_message`/`model_returned` fields are
scoped to the final decision group.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_verdict.verdict_hot import claims as claims_mod
from agent_verdict.verdict_hot import policy as policy_mod
from agent_verdict.verdict_hot import questions as questions_mod
from agent_verdict.verdict_hot import span as span_mod
from agent_verdict.verdict_hot import state as state_mod
from agent_verdict.verdict_hot import verdict_policy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_schema as rs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "site" / "scenarios"
OUT = ROOT / "site" / "src" / "data" / "scenarios"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "site" / "unreported-failure"
OUT_FIXTURES = ROOT / "site" / "src" / "data" / "fixtures"

# C1: these four tools carry an absolute path (Write/Edit/NotebookEdit) or a
# path-shaped structure (Read; _recorder_fields._raw_input_excerpt has no
# special case for it, so its input_excerpt is a JSON-compact dump of the
# whole tool_input) in input_excerpt -- never publish more than a basename.
_PATH_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "Read"})


def _cap(value: object, field: str) -> str:
    text = value if isinstance(value, str) else ""
    limit = rs.CAPS[field]
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    if name.startswith("mcp__"):
        return "mcp"
    return name if name in rs.TOOLS else None


def _command_for(tool_name: object, input_excerpt: object) -> str:
    text = input_excerpt if isinstance(input_excerpt, str) else ""
    if tool_name in _PATH_TOOLS:
        token = text.split()[0] if text.split() else ""
        text = os.path.basename(token) if token else ""
    return _cap(text, "command")


def _events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # I2: t0 is the earliest EMITTED row's timestamp. session_start (and any
    # other kind outside EVENT_KINDS) is never shown, so it must not shift
    # the baseline either -- otherwise a real recording's first visible
    # event would carry a dead lead-in instead of starting at t_ms 0.
    emitted = [r for r in rows if r.get("event") in rs.EVENT_KINDS]
    stamps = [float(r["ts"]) for r in emitted if isinstance(r.get("ts"), (int, float))]
    t0 = min(stamps) if stamps else 0.0
    out: list[dict[str, Any]] = []
    for r in emitted:
        kind = r["event"]
        ts = float(r["ts"]) if isinstance(r.get("ts"), (int, float)) else t0
        out.append(
            {
                "seq": len(out) + 1,
                "kind": kind,
                "t_ms": max(0, int((ts - t0) * 1000)),
                "tool": _tool(r.get("tool_name")),
                "command": _command_for(r.get("tool_name"), r.get("input_excerpt")),
                "status": r.get("status") if isinstance(r.get("status"), str) else None,
                "exit_code": r.get("exit_code") if isinstance(r.get("exit_code"), int) else None,
                # I1: "decision" is reserved for a PreToolUse rule's own
                # verdict (rules.RuleDecision.decision is "deny"/"ask"/None
                # -- never "allow", global-constraints.md); an "action"
                # row's pass/flag/block/gate_unavailable outcome belongs to
                # the top-level bundle["decision"] object (and this same
                # event's own "rule_id" field), not here.
                "decision": r.get("decision") if kind == "pre" else None,
                "rule_id": r.get("rule_id") if kind == "action" else None,
            }
        )
    return out


def _last_decision_group(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], int]:
    """(action_row, stop_row, verdict_rows, group_start) for the LAST
    `action` row's own decision group, keyed by `(prompt_id, agent_id)` --
    the same grouping `src/agent_verdict/replay.py`'s `_replay_session`
    uses. `group_start` is the index where this key's own `verdict` rows
    began; `rows[:group_start]` is the decision-time prefix the span must
    be built from (I3)."""
    pending_verdicts: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    verdict_start: dict[tuple[Any, Any], int] = {}
    last_stop_by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    last_action: dict[str, Any] | None = None
    last_action_key: tuple[Any, Any] | None = None
    last_action_verdicts: list[dict[str, Any]] = []
    last_action_group_start = 0

    for idx, row in enumerate(rows):
        event = row.get("event")
        key = (row.get("prompt_id"), row.get("agent_id"))
        if event == "stop":
            last_stop_by_key[key] = row
            continue
        if event == "verdict":
            if key not in pending_verdicts:
                verdict_start[key] = idx
            pending_verdicts.setdefault(key, []).append(row)
            continue
        if event != "action":
            continue
        last_action = row
        last_action_key = key
        last_action_verdicts = pending_verdicts.pop(key, [])
        last_action_group_start = verdict_start.pop(key, idx)

    if last_action is None or last_action_key is None:
        raise ValueError("ledger has no action row")
    stop_row = last_stop_by_key.get(last_action_key)
    if stop_row is None:
        raise ValueError("no stop row shares the final action row's (prompt_id, agent_id)")
    return last_action, stop_row, last_action_verdicts, last_action_group_start


def _rebuild(
    span_rows: list[dict[str, Any]], stop_row: dict[str, Any], policy: policy_mod.Policy
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """The span, state and questions exactly as stop.py built them for this
    stop: `span_rows` is the caller's decision-time prefix (I3)."""
    raw_prompt_id = stop_row.get("prompt_id")
    prompt_id = raw_prompt_id if isinstance(raw_prompt_id, str) else None
    span = span_mod.build_span(span_rows, prompt_id, policy)
    raw_final = stop_row.get("final_message_excerpt")
    final = raw_final if isinstance(raw_final, str) else ""
    claim_list = claims_mod.extract_claims(final, policy)
    state, _overflow = state_mod.build_state(span, final, claim_list, policy)
    return span, state, questions_mod.build_questions(state)


def _questions(qs: dict[str, Any], verdicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answers = {
        v["question_key"]: v.get("answer")
        for v in verdicts
        if isinstance(v.get("question_key"), str)
    }
    out = []
    for key, q in qs.items():
        statement = str(q.get("instructions", "")).split("\n\n")[0]
        ans = answers.get(key) or {}
        value = ans.get("noul", ans.get("score", ans.get("choice")))
        out.append(
            {
                "key": key,
                "type": q.get("type"),
                "statement": _cap(statement, "statement"),
                "answer": value,
            }
        )
    return out


def _reason(
    action: dict[str, Any],
    span: Any,
    verdicts: list[dict[str, Any]],
    policy: policy_mod.Policy,
    n_claims: int,
) -> str:
    if action.get("rule_id") is None:
        return ""
    answers = {
        v["question_key"]: v.get("answer")
        for v in verdicts
        if isinstance(v.get("question_key"), str)
    }
    claim_ids = tuple(f"c{i}" for i in range(1, n_claims + 1))
    decision = verdict_policy.decide(answers, span, policy, claim_ids)
    return _cap(decision.reason, "reason")


def build_from(scenario_dir: Path) -> dict[str, Any]:
    spec = json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))
    rec = json.loads((scenario_dir / "recording.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (scenario_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    policy = policy_mod.load_policy(policy_mod.default_policy_path())
    action, stop_row, verdicts, group_start = _last_decision_group(rows)
    span, state, qs = _rebuild(rows[:group_start], stop_row, policy)
    n_claims = len(state.get("untrusted", {}).get("claims", {}))
    return {
        "name": spec["name"],
        "title": _cap(spec["title"], "title"),
        "summary": _cap(spec["summary"], "summary"),
        "staged_claim": bool(spec["staged_claim"]),
        "mode": rec["mode"],
        "recorded_at": rec["recorded_at"],
        "claude_code_version": rec["claude_code_version"],
        "model_returned": next((v.get("model_returned") for v in verdicts), None),
        "events": _events(rows),
        "final_message": _cap(stop_row.get("final_message_excerpt"), "final_message"),
        "questions": _questions(qs, verdicts) if verdicts else [],
        "decision": {
            "action": action.get("action"),
            "would_have": action.get("would_have"),
            "rule_id": action.get("rule_id"),
            "threshold_used": action.get("threshold_used"),
            "reason": _reason(action, span, verdicts, policy, n_claims),
        },
    }


def _build_fixture() -> None:
    OUT_FIXTURES.mkdir(parents=True, exist_ok=True)
    bundle = build_from(FIXTURE_DIR)
    (OUT_FIXTURES / f"{bundle['name']}.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"built fixture {bundle['name']}")


def _build_scenarios() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    names = [n for n in rs.SCENARIO_ORDER if (SCENARIOS / n / "ledger.jsonl").exists()]
    for name in names:
        bundle = build_from(SCENARIOS / name)
        (OUT / f"{name}.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"built {name}")
    (OUT / "index.json").write_text(json.dumps(names) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build replay bundles for the demo site")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="build the committed tests/fixtures/site/unreported-failure fixture "
        "into site/src/data/fixtures/, instead of the recorded scenarios",
    )
    args = parser.parse_args(argv)

    if args.fixture:
        _build_fixture()
        return 0

    _build_scenarios()
    return 0


if __name__ == "__main__":
    sys.exit(main())
