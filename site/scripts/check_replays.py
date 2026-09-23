"""Refuse any replay bundle outside the closed shape or carrying text the
redactor would change. Runs in `make check` and in the site's CI.

Every string reachable from a bundle is checked with a single call to
`redact.redact_detail` (agent_verdict.verdict_hot.redact): if it would
change the text, the bundle is rejected, whether or not the hit is
attributed to a named rule (a redaction-pipeline exception -- `redact_detail`
returning `("[redaction failed]", ())` -- also changes the text, and must
fail the check too, not silently pass because `rule_ids` is empty). This is
the one source of truth for "would the redactor touch this text": there is
no separate hand-rolled pattern scan here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agent_verdict.verdict_hot import redact

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_schema as rs  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "src" / "data" / "scenarios"
FIXTURES_DATA = Path(__file__).resolve().parents[1] / "src" / "data" / "fixtures"


def _strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [s for i, v in enumerate(value) for s in _strings(v, f"{path}[{i}]")]
    return []


def check_bundle(bundle: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if set(bundle) != set(rs.TOP_KEYS):
        problems.append(f"top-level keys {sorted(set(bundle) ^ set(rs.TOP_KEYS))}")
    for i, ev in enumerate(bundle.get("events", [])):
        if set(ev) != set(rs.EVENT_KEYS):
            problems.append(f"events[{i}] keys")
        if ev.get("kind") not in rs.EVENT_KINDS:
            problems.append(f"events[{i}] kind {ev.get('kind')!r}")
        if ev.get("tool") is not None and ev.get("tool") not in rs.TOOLS:
            problems.append(f"events[{i}] tool {ev.get('tool')!r}")
        if ev.get("seq") != i + 1:
            problems.append(f"events[{i}] seq")
    stamps = [ev.get("t_ms", 0) for ev in bundle.get("events", [])]
    if stamps != sorted(stamps):
        problems.append("t_ms not monotonic")
    for i, q in enumerate(bundle.get("questions", [])):
        if set(q) != set(rs.QUESTION_KEYS):
            problems.append(f"questions[{i}] keys")
    decision = bundle.get("decision", {})
    if set(decision) != set(rs.DECISION_KEYS) or decision.get("action") not in rs.ACTIONS:
        problems.append("decision shape")
    for field, cap in rs.CAPS.items():
        for path, text in _strings(bundle):
            if path.endswith("." + field) and len(text) > cap:
                problems.append(f"{path} exceeds {cap} chars")
    for path, text in _strings(bundle):
        cleaned, rule_ids = redact.redact_detail(text)
        if cleaned == text:
            continue
        if rule_ids:
            problems.append(f"{path}: matches redaction rule(s) {rule_ids}")
        else:
            problems.append(f"{path}: redact() would change it")
    return problems


def _bundle_paths() -> list[Path]:
    scenario_bundles = (
        sorted(p for p in DATA.glob("*.json") if p.name != "index.json") if DATA.exists() else []
    )
    fixture_bundles = sorted(FIXTURES_DATA.glob("*.json")) if FIXTURES_DATA.exists() else []
    return scenario_bundles + fixture_bundles


def main(argv: list[str] | None = None) -> int:
    bundles = _bundle_paths()
    if not bundles:
        print("check_replays: no bundles yet, skipping")
        return 0
    bad = 0
    for path in bundles:
        for problem in check_bundle(json.loads(path.read_text(encoding="utf-8"))):
            print(f"{path.name}: {problem}")
            bad += 1
    print(f"check_replays: {len(bundles)} bundle(s), {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
