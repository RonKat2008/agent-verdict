import process_fixtures as pf


def test_sanitize_replaces_home_prefix_everywhere() -> None:
    raw = {"cwd": "/Users/alice/proj", "nested": ["/Users/alice/.claude/x.jsonl", 3]}
    assert pf.sanitize(raw, "/Users/alice") == {
        "cwd": "/Users/USER/proj",
        "nested": ["/Users/USER/.claude/x.jsonl", 3],
    }


def test_sanitize_truncates_long_strings_and_keeps_both_ends() -> None:
    text = "A" * 1500 + "MIDDLE" + "Z" * 1500
    out = pf.sanitize({"t": text}, "/nohome")
    assert isinstance(out, dict) and len(out["t"]) < 2200
    assert out["t"].startswith("AAA") and out["t"].endswith("ZZZ") and "truncated" in out["t"]


def test_sanitize_does_not_mutate_its_input() -> None:
    raw = {"cwd": "/Users/alice/p"}
    pf.sanitize(raw, "/Users/alice")
    assert raw == {"cwd": "/Users/alice/p"}


def test_fixture_name_uses_event_and_tool() -> None:
    payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}
    assert pf.fixture_name(payload) == "post_tool_use_failure_bash"
    assert pf.fixture_name({"hook_event_name": "Stop"}) == "stop"


def test_select_payloads_prefers_pre_paired_with_a_post() -> None:
    unpaired_pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t1"}
    paired_pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t2"}
    post = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "t2"}
    selected = pf.select_payloads([unpaired_pre, paired_pre, post])
    assert selected["pre_tool_use_bash"]["tool_use_id"] == "t2"
    assert selected["post_tool_use_bash"]["tool_use_id"] == "t2"


def test_select_payloads_falls_back_to_first_when_nothing_is_paired() -> None:
    first = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_use_id": "a"}
    second = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_use_id": "b"}
    selected = pf.select_payloads([first, second])
    assert selected["pre_tool_use_write"]["tool_use_id"] == "a"


def test_select_payloads_matches_pre_and_post_failure() -> None:
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "f1"}
    unpaired_pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "f0"}
    failure = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_use_id": "f1"}
    selected = pf.select_payloads([unpaired_pre, pre, failure])
    assert selected["pre_tool_use_bash"]["tool_use_id"] == "f1"
    assert selected["post_tool_use_failure_bash"]["tool_use_id"] == "f1"
