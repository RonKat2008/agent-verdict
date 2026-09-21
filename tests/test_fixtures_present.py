import json
from pathlib import Path

HOOKS = Path(__file__).resolve().parent / "fixtures/hooks"
EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SubagentStop",
    "SessionEnd",
]


def _payloads() -> list[dict[str, object]]:
    return [json.loads(p.read_text()) for p in sorted(HOOKS.glob("*.json"))]


def test_every_registered_event_has_a_fixture_with_provenance() -> None:
    seen = {p["hook_event_name"] for p in _payloads()}
    assert [e for e in EVENTS if e not in seen] == []
    assert "Captured" in (HOOKS / "PROVENANCE.md").read_text()


def test_failure_fixture_carries_an_exit_code_line() -> None:
    failures = [p for p in _payloads() if p["hook_event_name"] == "PostToolUseFailure"]
    assert any(str(p.get("error", "")).startswith("Exit code 3") for p in failures)


def test_fixtures_contain_no_real_home_path() -> None:
    home = str(Path.home())
    assert all(home not in p.read_text() for p in HOOKS.glob("*.json"))
