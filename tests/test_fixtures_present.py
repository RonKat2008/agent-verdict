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


def _fixtures_by_stem() -> dict[str, dict[str, object]]:
    return {p.stem: json.loads(p.read_text()) for p in HOOKS.glob("*.json")}


def test_every_registered_event_has_a_fixture_with_provenance() -> None:
    seen = {p["hook_event_name"] for p in _payloads()}
    assert [e for e in EVENTS if e not in seen] == []
    assert "Captured" in (HOOKS / "PROVENANCE.md").read_text()


def test_failure_fixture_carries_an_exit_code_line() -> None:
    failures = [p for p in _payloads() if p["hook_event_name"] == "PostToolUseFailure"]
    assert any(str(p.get("error", "")).startswith("Exit code 3") for p in failures)


def test_fixtures_contain_no_real_home_path() -> None:
    home = str(Path.home())
    fixture_files = list(HOOKS.glob("*.json"))
    assert fixture_files, "expected at least one captured fixture file"
    assert all(home not in p.read_text() for p in fixture_files)


def test_pre_and_post_fixtures_for_the_same_tool_share_a_tool_use_id() -> None:
    fixtures = _fixtures_by_stem()
    pre_prefix = "pre_tool_use_"
    checked_any = False
    for name, payload in fixtures.items():
        if not name.startswith(pre_prefix):
            continue
        tool = name[len(pre_prefix) :]
        post_name = f"post_tool_use_{tool}"
        post_payload = fixtures.get(post_name)
        if post_payload is None:
            continue
        checked_any = True
        assert payload["tool_use_id"] == post_payload["tool_use_id"], (
            f"{name} and {post_name} should share one tool_use_id"
        )
    assert checked_any, "expected at least one pre/post fixture pair to compare"
