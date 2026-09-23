"""The closed shape of a replay bundle. Anything not listed here is a bug."""

from __future__ import annotations

CAPS = {
    "command": 80,
    "final_message": 600,
    "reason": 2000,
    "summary": 240,
    "title": 60,
    "statement": 300,
}
TOP_KEYS = frozenset(
    {
        "name",
        "title",
        "summary",
        "staged_claim",
        "mode",
        "recorded_at",
        "claude_code_version",
        "model_returned",
        "events",
        "final_message",
        "questions",
        "decision",
    }
)
EVENT_KEYS = frozenset(
    {"seq", "kind", "t_ms", "tool", "command", "status", "exit_code", "decision", "rule_id"}
)
EVENT_KINDS = frozenset({"prompt", "pre", "post", "post_fail", "stop", "verdict", "action"})
QUESTION_KEYS = frozenset({"key", "type", "statement", "answer"})
DECISION_KEYS = frozenset({"action", "would_have", "rule_id", "threshold_used", "reason"})
ACTIONS = frozenset({"pass", "flag", "block", "gate_unavailable"})
TOOLS = frozenset({"Bash", "Read", "Write", "Edit", "NotebookEdit", "WebFetch", "Agent", "mcp"})
SCENARIO_ORDER = (
    "unreported-failure",
    "unbacked-check",
    "honest-failure",
    "soft-failure",
    "clean-pass",
)
