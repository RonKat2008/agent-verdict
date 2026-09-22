"""Plugin and marketplace manifests match the M1/M2 registration constraints
(Task 5; task-4-brief.md, controller notes rulings 12-13 for the M2 additions).
"""

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
M2_EVENTS = M1_EVENTS | {"SubagentStop"}
POST_MATCHER = "^(Bash|Write|Edit|NotebookEdit|WebFetch|Agent|mcp__.*)$"


def test_hooks_json_registers_exactly_the_m1_and_m2_events_in_exec_form() -> None:
    hooks = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())["hooks"]
    assert set(hooks) == M2_EVENTS
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
    assert hooks["SubagentStop"][0]["matcher"] == "*"
    for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        assert "matcher" not in hooks[event][0]
    # task-4-brief.md: Stop and SubagentStop both get a 15s timeout.
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 15
    assert hooks["SubagentStop"][0]["hooks"][0]["timeout"] == 15


def test_plugin_manifest_fields() -> None:
    manifest = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "agent-verdict"
    assert manifest["version"] == "0.1.0"
    assert set(manifest["userConfig"]) == {"mode", "provider", "api_key"}
    mode = manifest["userConfig"]["mode"]
    assert mode["options"] == ["shadow", "enforce", "off"] and mode["default"] == "shadow"
    provider = manifest["userConfig"]["provider"]
    assert provider["options"] == ["openrouter", "typesafe", "local-only"]
    assert provider["default"] == "openrouter"
    api_key = manifest["userConfig"]["api_key"]
    assert api_key["sensitive"] is True
    assert not (ROOT / "plugin/bin").exists()


def test_marketplace_points_at_the_plugin_directory() -> None:
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert market["name"] == "agent-verdict"
    (plugin,) = market["plugins"]
    assert plugin["name"] == "agent-verdict" and plugin["source"] == "./plugin"
