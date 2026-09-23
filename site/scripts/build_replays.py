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

`_rebuild` reconstructs the span/state/questions exactly the way
`stop.py` built them for the stop being replayed -- from the ledger rows
before that stop's own `verdict` rows (see `src/agent_verdict/replay.py`'s
`_replay_session`, which does the same "decision-time prefix" walk for the
same reason: a later row is real evidence for a later stop, never for this
one).
"""

from __future__ import annotations

import argparse
import json
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


def _events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stamps = [float(r["ts"]) for r in rows if isinstance(r.get("ts"), (int, float))]
    t0 = min(stamps) if stamps else 0.0
    out: list[dict[str, Any]] = []
    for r in rows:
        kind = r.get("event")
        if kind not in rs.EVENT_KINDS:
            continue
        ts = float(r["ts"]) if isinstance(r.get("ts"), (int, float)) else t0
        out.append(
            {
                "seq": len(out) + 1,
                "kind": kind,
                "t_ms": max(0, int((ts - t0) * 1000)),
                "tool": _tool(r.get("tool_name")),
                "command": _cap(r.get("input_excerpt"), "command"),
                "status": r.get("status") if isinstance(r.get("status"), str) else None,
                "exit_code": r.get("exit_code") if isinstance(r.get("exit_code"), int) else None,
                "decision": r.get("action")
                if kind == "action"
                else (r.get("decision") if kind == "pre" else None),
                "rule_id": r.get("rule_id") if kind == "action" else None,
            }
        )
    return out


def _rebuild(
    rows: list[dict[str, Any]], stop_row: dict[str, Any], policy: policy_mod.Policy
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """The span, state and questions exactly as stop.py built them: from the
    rows before the first verdict row (see src/agent_verdict/replay.py)."""
    first_verdict = next((i for i, r in enumerate(rows) if r.get("event") == "verdict"), len(rows))
    raw_prompt_id = stop_row.get("prompt_id")
    prompt_id = raw_prompt_id if isinstance(raw_prompt_id, str) else None
    span = span_mod.build_span(rows[:first_verdict], prompt_id, policy)
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
    answers = {v["question_key"]: v.get("answer") for v in verdicts}
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
    stop_row = next(r for r in rows if r.get("event") == "stop")
    action = [r for r in rows if r.get("event") == "action"][-1]
    verdicts = [r for r in rows if r.get("event") == "verdict"]
    span, state, qs = _rebuild(rows, stop_row, policy)
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
