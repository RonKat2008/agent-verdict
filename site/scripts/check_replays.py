"""Refuse any replay bundle outside the closed shape or carrying text the
redactor would change. Runs in `make check` and in the site's CI.

Every field is type-checked before its key set or value is inspected (I1):
a nested container under a capped key (e.g. `{"command": {"raw": ...}}`)
or a wrong-typed scalar (e.g. a string `t_ms`) is reported as a problem,
never a `TypeError`. Every string reachable from a bundle, regardless of
which field it sits under, gets: the field's own cap from `CAPS` if its key
matches one, a global 2000-char cap regardless of key, a check against an
absolute-path/local-hostname/UUID pattern (C1 -- these can leak a real
machine's filesystem layout or a session/tool-use id even though they are
not a "secret" the redactor's rules know about), and a single call to
`redact.redact_detail` (agent_verdict.verdict_hot.redact): if any of those
would change or reject the text, the bundle is rejected. This is the one
source of truth for "would the redactor touch this text" -- there is no
separate hand-rolled secret-pattern scan here, only the path/hostname/UUID
patterns, which are a different, non-secret concern.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from agent_verdict.verdict_hot import redact

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_schema as rs  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "src" / "data" / "scenarios"
FIXTURES_DATA = Path(__file__).resolve().parents[1] / "src" / "data" / "fixtures"

_GLOBAL_STRING_CAP = 2000

# C1(b): a leading path separator (or one preceded by whitespace, a quote, a
# paren, or "="), a home-relative path, or a Windows drive letter.
_ABS_PATH_RE = re.compile(r"(^|[\s'\"(=])(/|~/|[A-Za-z]:\\)")
_LOCAL_HOST_RE = re.compile(r"\b[A-Za-z0-9-]+\.(?:local|lan|internal)\b", re.IGNORECASE)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

_STATUS_VALUES = frozenset({"ok", "error", None})
# rules.RuleDecision.decision is "deny"/"ask"/None -- an "action" event's
# own pass/flag/block/gate_unavailable outcome is never stored in this
# field (build_replays._events, I1); see that module for why.
_EVENT_DECISION_VALUES = frozenset({"deny", "ask", None})
_QUESTION_TYPE_VALUES = frozenset({"noul", "score", "choice"})


def _strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [s for i, v in enumerate(value) for s in _strings(v, f"{path}[{i}]")]
    return []


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _typed(problems: list[str], path: str, value: Any, ok: bool, expected: str) -> bool:
    """Records a type problem and returns whether `value` had the expected
    type, so a caller can skip value-level checks (an allowlist membership
    test, an equality) that would themselves misbehave on the wrong type."""
    if not ok:
        problems.append(f"{path}: expected {expected}, got {type(value).__name__}")
    return ok


def _check_event(ev: Any, i: int, problems: list[str]) -> None:
    path = f"events[{i}]"
    if not isinstance(ev, dict):
        problems.append(f"{path}: expected object, got {type(ev).__name__}")
        return
    if set(ev) != set(rs.EVENT_KEYS):
        problems.append(f"{path} keys {sorted(set(ev) ^ set(rs.EVENT_KEYS))}")
    seq = ev.get("seq")
    if _typed(problems, f"{path}.seq", seq, _is_int(seq), "int") and seq != i + 1:
        problems.append(f"{path} seq")
    kind = ev.get("kind")
    if _typed(problems, f"{path}.kind", kind, isinstance(kind, str), "str") and (
        kind not in rs.EVENT_KINDS
    ):
        problems.append(f"{path} kind {kind!r}")
    _typed(problems, f"{path}.t_ms", ev.get("t_ms"), _is_int(ev.get("t_ms")), "int")
    tool = ev.get("tool")
    if _typed(
        problems, f"{path}.tool", tool, tool is None or isinstance(tool, str), "str|None"
    ) and (tool is not None and tool not in rs.TOOLS):
        problems.append(f"{path} tool {tool!r}")
    _typed(
        problems, f"{path}.command", ev.get("command"), isinstance(ev.get("command"), str), "str"
    )
    status = ev.get("status")
    if _typed(
        problems, f"{path}.status", status, status is None or isinstance(status, str), "str|None"
    ) and (status not in _STATUS_VALUES):
        problems.append(f"{path} status {status!r}")
    exit_code = ev.get("exit_code")
    _typed(
        problems,
        f"{path}.exit_code",
        exit_code,
        exit_code is None or _is_int(exit_code),
        "int|None",
    )
    decision = ev.get("decision")
    if _typed(
        problems,
        f"{path}.decision",
        decision,
        decision is None or isinstance(decision, str),
        "str|None",
    ) and (decision not in _EVENT_DECISION_VALUES):
        problems.append(f"{path} decision {decision!r}")
    rule_id = ev.get("rule_id")
    _typed(
        problems,
        f"{path}.rule_id",
        rule_id,
        rule_id is None or isinstance(rule_id, str),
        "str|None",
    )


def _check_question(q: Any, i: int, problems: list[str]) -> None:
    path = f"questions[{i}]"
    if not isinstance(q, dict):
        problems.append(f"{path}: expected object, got {type(q).__name__}")
        return
    if set(q) != set(rs.QUESTION_KEYS):
        problems.append(f"{path} keys {sorted(set(q) ^ set(rs.QUESTION_KEYS))}")
    _typed(problems, f"{path}.key", q.get("key"), isinstance(q.get("key"), str), "str")
    qtype = q.get("type")
    if _typed(problems, f"{path}.type", qtype, isinstance(qtype, str), "str") and (
        qtype not in _QUESTION_TYPE_VALUES
    ):
        problems.append(f"{path} type {qtype!r}")
    _typed(
        problems,
        f"{path}.statement",
        q.get("statement"),
        isinstance(q.get("statement"), str),
        "str",
    )
    answer = q.get("answer")
    _typed(
        problems, f"{path}.answer", answer, answer is None or _is_number(answer), "int|float|None"
    )


def _check_decision(decision: Any, problems: list[str]) -> None:
    path = "decision"
    if not isinstance(decision, dict):
        problems.append(f"{path}: expected object, got {type(decision).__name__}")
        return
    if set(decision) != set(rs.DECISION_KEYS):
        problems.append(f"{path} keys {sorted(set(decision) ^ set(rs.DECISION_KEYS))}")
    action = decision.get("action")
    if _typed(problems, f"{path}.action", action, isinstance(action, str), "str") and (
        action not in rs.ACTIONS
    ):
        problems.append(f"{path}.action {action!r}")
    would_have = decision.get("would_have")
    if _typed(
        problems,
        f"{path}.would_have",
        would_have,
        would_have is None or isinstance(would_have, str),
        "str|None",
    ) and (would_have is not None and would_have not in rs.ACTIONS):
        problems.append(f"{path}.would_have {would_have!r}")
    rule_id = decision.get("rule_id")
    _typed(
        problems,
        f"{path}.rule_id",
        rule_id,
        rule_id is None or isinstance(rule_id, str),
        "str|None",
    )
    threshold = decision.get("threshold_used")
    _typed(
        problems,
        f"{path}.threshold_used",
        threshold,
        threshold is None or _is_number(threshold),
        "int|float|None",
    )
    _typed(
        problems,
        f"{path}.reason",
        decision.get("reason"),
        isinstance(decision.get("reason"), str),
        "str",
    )


def check_bundle(bundle: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not isinstance(bundle, dict):
        return [f"bundle: expected object, got {type(bundle).__name__}"]
    if set(bundle) != set(rs.TOP_KEYS):
        problems.append(f"top-level keys {sorted(set(bundle) ^ set(rs.TOP_KEYS))}")

    for field in (
        "name",
        "title",
        "summary",
        "mode",
        "recorded_at",
        "claude_code_version",
        "final_message",
    ):
        _typed(problems, field, bundle.get(field), isinstance(bundle.get(field), str), "str")
    _typed(
        problems,
        "staged_claim",
        bundle.get("staged_claim"),
        isinstance(bundle.get("staged_claim"), bool),
        "bool",
    )
    model_returned = bundle.get("model_returned")
    _typed(
        problems,
        "model_returned",
        model_returned,
        model_returned is None or isinstance(model_returned, str),
        "str|None",
    )

    events = bundle.get("events")
    if isinstance(events, list):
        for i, ev in enumerate(events):
            _check_event(ev, i, problems)
        # Only well-typed t_ms values participate: a wrong-typed one is
        # already reported by _check_event above, and comparing it here
        # would raise TypeError instead of reporting a second problem.
        stamps = [
            ev.get("t_ms", 0)
            for ev in events
            if isinstance(ev, dict) and _is_int(ev.get("t_ms", 0))
        ]
        if stamps != sorted(stamps):
            problems.append("t_ms not monotonic")
        if events and (
            not isinstance(events[0], dict)
            or not _is_int(events[0].get("t_ms"))
            or events[0].get("t_ms") != 0
        ):
            problems.append("events[0].t_ms != 0")  # I2
    else:
        problems.append(f"events: expected list, got {type(events).__name__}")

    questions = bundle.get("questions")
    if isinstance(questions, list):
        for i, q in enumerate(questions):
            _check_question(q, i, problems)
    else:
        problems.append(f"questions: expected list, got {type(questions).__name__}")

    _check_decision(bundle.get("decision"), problems)

    for field, cap in rs.CAPS.items():
        for path, text in _strings(bundle):
            if path.endswith("." + field) and len(text) > cap:
                problems.append(f"{path} exceeds {cap} chars")

    for path, text in _strings(bundle):
        if len(text) > _GLOBAL_STRING_CAP:
            problems.append(f"{path} exceeds the global {_GLOBAL_STRING_CAP} char cap")
        if _ABS_PATH_RE.search(text):
            problems.append(f"{path}: looks like an absolute path")
        if _LOCAL_HOST_RE.search(text):
            problems.append(f"{path}: looks like a local hostname")
        if _UUID_RE.search(text):
            problems.append(f"{path}: looks like a UUID")
        cleaned, rule_ids = redact.redact_detail(text)
        if cleaned == text:
            continue
        if rule_ids:
            problems.append(f"{path}: matches redaction rule(s) {rule_ids}")
        else:
            problems.append(f"{path}: redact() would change it")

    return problems


def main(argv: list[str] | None = None) -> int:
    if DATA.exists():
        scenario_bundles = sorted(p for p in DATA.glob("*.json") if p.name != "index.json")
        if not scenario_bundles:
            # MINOR: an empty scenarios dir is a build failure (the
            # pipeline emitted nothing), not the "nothing recorded yet"
            # case below -- that case is the directory not existing at all.
            print(f"check_replays: {DATA} exists but holds no bundles")
            return 1
    else:
        scenario_bundles = []

    fixture_bundles = sorted(FIXTURES_DATA.glob("*.json")) if FIXTURES_DATA.exists() else []
    bundles = scenario_bundles + fixture_bundles
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
