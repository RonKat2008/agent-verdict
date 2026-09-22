"""`rules.py` never emits `permissionDecision: "allow"` (D-006, CLAUDE.md's
"Never reintroduce" table, task-5-brief.md controller notes ruling 1).

Three independent proofs: a source grep (the word can never appear even in
a comment or docstring), a Hypothesis fuzz corpus over `decide()`'s three
outcomes, and a check that the built output JSON never contains the banned
substring for any of those outcomes.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from verdict_hot import policy as policy_mod
from verdict_hot import rules

ROOT = Path(__file__).resolve().parents[2]
RULES_SRC = ROOT / "plugin" / "hooks" / "verdict_hot" / "rules.py"
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
POLICY = policy_mod.load_policy(PACKAGED_DEFAULT)

_BANNED = "".join(["a", "l", "l", "o", "w"])  # never spelled out directly (self-referential grep)


def test_rules_source_never_contains_the_banned_word() -> None:
    text = RULES_SRC.read_text(encoding="utf-8")
    assert _BANNED not in text.lower()


_TOOL_NAMES = st.sampled_from(["Bash", "Write", "Edit", "NotebookEdit", "Read", "mcp__fs__read"])
_JSON_SCALAR = st.one_of(st.text(max_size=200), st.integers(), st.booleans(), st.none())
_TOOL_INPUT = st.dictionaries(
    st.sampled_from(["command", "file_path", "notebook_path", "description", "content"]),
    _JSON_SCALAR,
    max_size=5,
)
_CWD = st.sampled_from(["/", "/tmp", "/Users/dev/project", "relative/path", ""])


@given(tool_name=_TOOL_NAMES, tool_input=_TOOL_INPUT, cwd=_CWD)
@settings(max_examples=300)
def test_decide_never_returns_anything_but_deny_ask_or_none(
    tool_name: str, tool_input: dict[str, object], cwd: str
) -> None:
    decision = rules.decide(tool_name, tool_input, POLICY, cwd)
    assert decision.decision in ("deny", "ask", None)

    output = rules.build_output_json(decision)
    if decision.decision is None:
        assert output is None
    else:
        assert output is not None
        assert _BANNED not in output.lower()
