"""Tests for site/scripts/{build_replays,check_replays}.py (task-2-brief.md).

`_make_fixture` seeds a temp `VERDICT_HOME` with a real session's rows --
`session_start`, `prompt`, and `post_fail` (the same shapes
`tests/unit/test_stop.py`'s `_failing_step()` uses), plus a `stop` row
(the shape `recorders._build_stop_fields` gives a real Stop hook) -- then
runs `stop.handle` in shadow mode with a fake, no-network transport
(`test_stop._answer_transport`) exactly the way `test_stop.py`'s own tests
do, so `verdict`/`action` rows are produced by the real production code
path, never hand-rolled. `time.time()` is frozen to a fixed, 0.5s-per-call
sequence for the whole seed+handle sequence, so every row's `ts` is
realistic (not bunched into the same millisecond the way a real, fast
local test run would leave them) and the fixture is reproducible
byte-for-byte (mod `hook_ms`, which reads the wall clock `stop.handle`
itself measures its own duration against, never frozen here) across
regenerations.

No test here makes a network call: `_answer_transport` is the same 4-arg
fake `provider.py` documents, never a real socket.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "site" / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

import build_replays  # noqa: E402
import check_replays  # noqa: E402
import replay_schema  # noqa: E402
from test_stop import _POLICY, _answer_transport, _failing_step, _payload, _seed  # noqa: E402
from verdict_hot import _stop_rows, stop, verdict_policy  # noqa: E402
from verdict_hot import claims as claims_mod  # noqa: E402
from verdict_hot import span as span_mod  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "site" / "unreported-failure"
SCENARIO_SPEC = ROOT / "site" / "scenarios" / "unreported-failure" / "scenario.json"

_SESSION_ID = "site-fixture-unreported-failure"
_PROMPT_ID = "p1"
_TOOL_USE_ID = "f1"
_FINAL_MESSAGE = "Done: I ran the tests and all tests pass, the task is complete."
_BASE_TS = 1790200000.0  # fixed anchor: the fixture regenerates byte-identical (mod hook_ms)
_ENV_KEYS = (
    "VERDICT_HOME",
    "CLAUDE_PLUGIN_OPTION_MODE",
    "CLAUDE_PLUGIN_OPTION_PROVIDER",
    "VERDICT_CASSETTE_DIR",
)


@contextlib.contextmanager
def _isolated_verdict_home():  # type: ignore[no-untyped-def]
    """A temp `VERDICT_HOME`, restored/cleaned up on exit -- shared by
    `_make_fixture` and the I3 retry-fixture helper below."""
    home_dir = Path(tempfile.mkdtemp(prefix="verdict-site-test-home-"))
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        os.environ["VERDICT_HOME"] = str(home_dir)
        for key in _ENV_KEYS[1:]:
            os.environ.pop(key, None)
        yield home_dir
    finally:
        shutil.rmtree(home_dir, ignore_errors=True)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _stop_row(prompt_id: str, last_message: str) -> dict[str, Any]:
    return {
        "event": "stop",
        "prompt_id": prompt_id,
        "stop_hook_active": False,
        "final_message_excerpt": last_message,
        "claims": list(claims_mod.extract_claims(last_message, _POLICY)),
        "background_tasks_n": 0,
    }


def _run_stop(
    session_id: str, prompt_id: str, last_message: str, overrides: dict[str, float]
) -> None:
    """Mirrors the real hook order (`recorders.record` writes the `stop`
    row before `stop.handle` runs) -- reused by the I3 retry fixture."""
    _seed(session_id, [_stop_row(prompt_id, last_message)])
    stop.handle(
        _payload(session_id=session_id, prompt_id=prompt_id, last_message=last_message),
        _POLICY,
        time.monotonic(),
        "key",
        transport=_answer_transport(overrides),
    )


def _make_fixture(dir: Path) -> None:
    home_dir = Path(tempfile.mkdtemp(prefix="verdict-site-fixture-home-"))
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    counter = {"n": 2}

    def _next_ts() -> float:
        counter["n"] += 1
        return _BASE_TS + counter["n"] * 0.5

    original_time = time.time
    try:
        os.environ["VERDICT_HOME"] = str(home_dir)
        for key in _ENV_KEYS[1:]:
            os.environ.pop(key, None)

        claim_list = list(claims_mod.extract_claims(_FINAL_MESSAGE, _POLICY))

        failing = _failing_step(_PROMPT_ID, _TOOL_USE_ID)
        rows: list[dict[str, Any]] = [
            # I2: session_start is 3s before the prompt -- a real dead
            # lead-in that must NOT shift t_ms's zero point, since
            # session_start is never itself an emitted event.
            {
                "event": "session_start",
                "prompt_id": None,
                "ts": _BASE_TS - 3.0,
                "source": "startup",
            },
            {**failing[0], "ts": _BASE_TS},  # prompt: t_ms 0
            {**failing[1], "ts": _BASE_TS + 0.5},  # post_fail
            {
                "event": "stop",
                "prompt_id": _PROMPT_ID,
                "ts": _BASE_TS + 1.0,
                "stop_hook_active": False,
                "final_message_excerpt": _FINAL_MESSAGE,
                "claims": claim_list,
                "background_tasks_n": 0,
            },
        ]
        _seed(_SESSION_ID, rows)

        time.time = _next_ts  # freeze the clock stop.handle's own row writes use
        try:
            outcome = stop.handle(
                _payload(
                    session_id=_SESSION_ID,
                    prompt_id=_PROMPT_ID,
                    last_message=_FINAL_MESSAGE,
                ),
                _POLICY,
                time.monotonic(),
                "key",
                transport=_answer_transport({"claims_done": 0.92, "acks_failures": 0.05}),
            )
        finally:
            time.time = original_time

        assert outcome.rows[-1]["action"] == "pass"
        assert outcome.rows[-1]["would_have"] == "block"
        assert outcome.rows[-1]["rule_id"] == "R1"

        dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(home_dir / "events" / f"{_SESSION_ID}.jsonl", dir / "ledger.jsonl")

        spec = json.loads(SCENARIO_SPEC.read_text(encoding="utf-8"))
        (dir / "scenario.json").write_text(
            json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        recording = {
            "recorded_at": "2026-09-23T00:00:00+00:00",
            "claude_code_version": "2.1.278",
            "model": "haiku",
            "mode": "shadow",
        }
        (dir / "recording.json").write_text(
            json.dumps(recording, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        shutil.rmtree(home_dir, ignore_errors=True)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def tmp_scenario(tmp_path: Path) -> Path:
    dest = tmp_path / "unreported-failure"
    _make_fixture(dest)
    return dest


def test_build_produces_the_closed_shape(tmp_scenario: Path) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    assert set(bundle) == set(replay_schema.TOP_KEYS)
    for ev in bundle["events"]:
        assert set(ev) == set(replay_schema.EVENT_KEYS)
        assert ev["kind"] in replay_schema.EVENT_KINDS
    assert [ev["seq"] for ev in bundle["events"]] == list(range(1, len(bundle["events"]) + 1))
    assert bundle["events"][0]["t_ms"] == 0
    assert bundle["decision"]["would_have"] == "block" and bundle["decision"]["rule_id"] == "R1"
    assert {q["key"] for q in bundle["questions"]} >= {"claims_done", "acks_failures"}
    assert all(
        len(q["statement"]) <= 300 and not q["statement"].startswith("Text under")
        for q in bundle["questions"]
    )

    # MINOR: a prefix check on the reason text is weak -- independently
    # recompute the decision from the raw ledger (the same inputs
    # build_replays._rebuild uses) and assert build_replays reproduced the
    # exact same reason_hash stop.handle itself stored on the action row,
    # not just a string that happens to start with "Rule R1".
    rows = [
        json.loads(line)
        for line in (tmp_scenario / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    action_row = next(r for r in rows if r.get("event") == "action")
    stop_row = next(r for r in rows if r.get("event") == "stop")
    verdict_rows = [r for r in rows if r.get("event") == "verdict"]
    answers = {
        v["question_key"]: v.get("answer")
        for v in verdict_rows
        if isinstance(v.get("question_key"), str)
    }
    first_verdict_idx = next(i for i, r in enumerate(rows) if r.get("event") == "verdict")
    span = span_mod.build_span(rows[:first_verdict_idx], stop_row.get("prompt_id"), _POLICY)
    claim_list = list(claims_mod.extract_claims(stop_row["final_message_excerpt"], _POLICY))
    claim_ids = tuple(f"c{i}" for i in range(1, len(claim_list) + 1))
    decision = verdict_policy.decide(answers, span, _POLICY, claim_ids)
    assert decision.rule_id is not None
    assert (
        _stop_rows.reason_hash(decision.rule_id, decision.offending_seqs)
        == action_row["reason_hash"]
    )


def test_bundle_never_carries_ledger_text(tmp_scenario: Path) -> None:
    blob = json.dumps(build_replays.build_from(tmp_scenario))
    for forbidden in (
        "out_head",
        "out_tail",
        "error_excerpt",
        "prompt_excerpt",
        "session_id",
        "transcript_path",
        "/Users/",
        "/private/",
        "1 failed",
    ):
        assert forbidden not in blob


def test_check_rejects_an_extra_key_and_an_overlong_string(tmp_scenario: Path) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    bundle["extra"] = 1
    bundle["final_message"] = "x" * 601
    problems = check_replays.check_bundle(bundle)
    assert any("extra" in p for p in problems) and any("final_message" in p for p in problems)


def test_check_rejects_text_the_redactor_would_change(tmp_scenario: Path) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    bundle["final_message"] = "key sk-or-v1-" + "f" * 64
    assert any("redact" in p for p in check_replays.check_bundle(bundle))


# --- C1: paths, hostnames, and Read/Write/Edit/NotebookEdit basenames --------


def test_events_publish_only_the_basename_for_path_tools(tmp_path: Path) -> None:
    abs_path = str(tmp_path / "work" / "app.py")
    rows = [
        {
            "event": "post",
            "ts": 100.0,
            "tool_name": "Edit",
            "input_excerpt": abs_path,
            "status": "ok",
        }
    ]
    events = build_replays._events(rows)
    assert events[0]["command"] == "app.py"


def _set_command(bundle: dict[str, Any], value: str) -> None:
    bundle["events"][0]["command"] = value


def _set_title(bundle: dict[str, Any], value: str) -> None:
    bundle["title"] = value


def _set_statement(bundle: dict[str, Any], value: str) -> None:
    bundle["questions"][0]["statement"] = value


@pytest.mark.parametrize(
    "forbidden",
    (
        "cat /Users/x/.env",
        "host.local run",
        "a1b2c3d4-e5f6-47a8-89ab-1234567890ab",
    ),
)
@pytest.mark.parametrize("setter", (_set_command, _set_title, _set_statement))
def test_check_rejects_paths_hostnames_and_uuids(
    tmp_scenario: Path, setter: Any, forbidden: str
) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    setter(bundle, forbidden)
    assert check_replays.check_bundle(bundle)


# --- I1: every field is typed, and nothing evades its cap by nesting --------


def test_check_rejects_a_nested_container_under_a_capped_key(tmp_scenario: Path) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    bundle["events"][0]["command"] = {"raw": "x" * 999}
    problems = check_replays.check_bundle(bundle)
    assert any("command" in p for p in problems)


def test_check_rejects_a_string_typed_t_ms_instead_of_raising(tmp_scenario: Path) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    bundle["events"][0]["t_ms"] = "500"
    problems = check_replays.check_bundle(bundle)  # must not raise TypeError
    assert any("t_ms" in p for p in problems)


def test_check_rejects_bad_enum_values(tmp_scenario: Path) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    bundle["decision"]["would_have"] = "maybe"
    bundle["events"][0]["status"] = "pending"
    bundle["questions"][0]["type"] = "essay"
    problems = check_replays.check_bundle(bundle)
    assert any("would_have" in p for p in problems)
    assert any("status" in p for p in problems)
    assert any("type" in p for p in problems)


# --- I2: t0 ignores rows outside EVENT_KINDS ---------------------------------


def test_check_rejects_when_first_event_is_not_at_t_ms_zero(tmp_scenario: Path) -> None:
    bundle = build_replays.build_from(tmp_scenario)
    bundle["events"][0]["t_ms"] = 500
    problems = check_replays.check_bundle(bundle)
    assert any("t_ms" in p for p in problems)


# --- I3: the LAST action row's own decision group, never the first stop or --
# --- verdicts merged across a retry ------------------------------------------


def _write_retry_scenario(dir: Path) -> None:
    """One session, one prompt_id, two stops (test_replay.py's
    `_build_retry_fixture` shape): the first sees an unresolved failure and
    blocks (R1); a retry between the two stops resolves it; the second
    stop, now with no evidence to gather, passes with no verdict rows at
    all. `build_from` must reflect the SECOND stop's decision, not the
    first's, and never a merge of both."""
    with _isolated_verdict_home() as home_dir:
        session_id = "site-retry-fixture"
        prompt_id = "p1"

        _seed(session_id, _failing_step(prompt_id, "f1"))
        _run_stop(
            session_id,
            prompt_id,
            "All tests pass now.",
            {"claims_done": 0.9, "acks_failures": 0.0},
        )

        _seed(
            session_id,
            [
                {
                    "event": "post",
                    "prompt_id": prompt_id,
                    "tool_name": "Bash",
                    "tool_use_id": "f2",
                    "input_excerpt": "npm test",
                    "status": "ok",
                    "is_check": False,
                    "soft_fail_candidate": False,
                    "never_send": False,
                    "out_head": "",
                    "out_tail": "",
                }
            ],
        )
        _run_stop(session_id, prompt_id, "Retried the build; still checking.", {})

        dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(home_dir / "events" / f"{session_id}.jsonl", dir / "ledger.jsonl")
        (dir / "scenario.json").write_text(
            json.dumps(
                {"name": "retry", "title": "Retry", "summary": "s", "staged_claim": False},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (dir / "recording.json").write_text(
            json.dumps(
                {
                    "recorded_at": "2026-09-23T00:00:00+00:00",
                    "claude_code_version": "2.1.278",
                    "model": "haiku",
                    "mode": "shadow",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def test_build_from_uses_the_last_stops_own_decision_group(tmp_path: Path) -> None:
    dest = tmp_path / "retry"
    _write_retry_scenario(dest)

    bundle = build_replays.build_from(dest)

    # The buggy "first stop" behavior would report stop 1's own message and
    # rule; the correct behavior reflects stop 2, which saw the retry as
    # real evidence and passed with nothing left to gather.
    assert bundle["final_message"] == "Retried the build; still checking."
    assert bundle["decision"]["rule_id"] is None
    assert bundle["decision"]["action"] == "pass"
    # The bug (stop 1's stop_row/span reused for stop 2) would still ask
    # "acks_failures" -- stop 1 has an unresolved failure that question is
    # built for; stop 2's own span, after the retry, has none.
    assert "acks_failures" not in {q["key"] for q in bundle["questions"]}


# --- MINOR: an empty (not absent) scenarios dir is a hard failure -----------


def test_check_main_fails_when_scenarios_dir_exists_but_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_scenarios = tmp_path / "scenarios"
    empty_scenarios.mkdir()
    monkeypatch.setattr(check_replays, "DATA", empty_scenarios)
    monkeypatch.setattr(check_replays, "FIXTURES_DATA", tmp_path / "fixtures-missing")
    assert check_replays.main([]) == 1


def test_check_main_skips_when_neither_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_replays, "DATA", tmp_path / "scenarios-missing")
    monkeypatch.setattr(check_replays, "FIXTURES_DATA", tmp_path / "fixtures-missing")
    assert check_replays.main([]) == 0


def test_check_main_passes_on_committed_bundles() -> None:
    assert check_replays.main([]) == 0


def test_committed_fixture_matches_a_fresh_build_modulo_ts(tmp_path: Path) -> None:
    """The committed `tests/fixtures/site/unreported-failure/` dir is real
    hook output, captured once (`REGEN_SITE_FIXTURE=1`, see
    `test_regen_committed_fixture` below) rather than hand-written. This
    guards against it silently drifting from what `_make_fixture` actually
    produces today."""
    fresh = tmp_path / "fresh"
    _make_fixture(fresh)

    for name in ("scenario.json", "recording.json"):
        assert (FIXTURE_DIR / name).read_text(encoding="utf-8") == (fresh / name).read_text(
            encoding="utf-8"
        )

    committed_rows = [
        json.loads(line)
        for line in (FIXTURE_DIR / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fresh_rows = [
        json.loads(line)
        for line in (fresh / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(committed_rows) == len(fresh_rows)
    ignored = {"ts", "hook_ms"}  # hook_ms reads the real wall clock, never frozen
    for committed, refreshed in zip(committed_rows, fresh_rows, strict=True):
        c = {k: v for k, v in committed.items() if k not in ignored}
        f = {k: v for k, v in refreshed.items() if k not in ignored}
        assert c == f


@pytest.mark.skipif(
    not os.environ.get("REGEN_SITE_FIXTURE"), reason="set REGEN_SITE_FIXTURE=1 to regenerate"
)
def test_regen_committed_fixture() -> None:
    """Not part of the normal suite: `REGEN_SITE_FIXTURE=1 uv run pytest
    tests/test_site_replays.py::test_regen_committed_fixture -v` regenerates
    the committed fixture from the real code path above, to be reviewed and
    committed like any other change."""
    _make_fixture(FIXTURE_DIR)
