"""Plugin and marketplace manifests match the M1 registration constraints (Task 5)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M1_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
}
POST_MATCHER = "^(Bash|Write|Edit|NotebookEdit|WebFetch|Agent|mcp__.*)$"


def test_hooks_json_registers_exactly_the_m1_events_in_exec_form() -> None:
    hooks = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())["hooks"]
    assert set(hooks) == M1_EVENTS
    for event, entries in hooks.items():
        assert len(entries) == 1
        entry = entries[0]
        for handler in entry["hooks"]:
            assert handler["type"] == "command"
            assert handler["command"] == "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh"
            assert isinstance(handler["args"], list) and len(handler["args"]) == 1
            assert (handler.get("timeout") is None) == (event == "SessionEnd")
    assert hooks["PostToolUse"][0]["matcher"] == POST_MATCHER
    assert hooks["PostToolUseFailure"][0]["matcher"] == "*"
    for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        assert "matcher" not in hooks[event][0]


def test_plugin_manifest_fields() -> None:
    manifest = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "agent-verdict"
    assert manifest["version"] == "0.1.0"
    assert set(manifest["userConfig"]) == {"mode"}
    mode = manifest["userConfig"]["mode"]
    assert mode["options"] == ["shadow", "enforce", "off"] and mode["default"] == "shadow"
    assert not (ROOT / "plugin/bin").exists()


def test_marketplace_points_at_the_plugin_directory() -> None:
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert market["name"] == "agent-verdict"
    (plugin,) = market["plugins"]
    assert plugin["name"] == "agent-verdict" and plugin["source"] == "./plugin"
