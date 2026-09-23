"""Tests for `verdict replay` (task-6-brief.md ruling 1; PLAN.md 5.5).

Builds a fixture ledger with `stop.handle` and a fake transport (reusing
`test_stop.py`'s helpers, per the controller notes), covering a stand-down,
a clean pass, and R1/R2/R3 blocks plus an R4 flag -- all in shadow mode, so
every one of them writes a `would_have` the replay contract can check
byte-for-byte. A `stop` ledger row is appended before each `stop.handle`
call (mirroring the real hook order: `recorders.record` runs before
`stop.handle`) so `replay` can reconstruct `claim_ids` the same way
`stop.py` does.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from test_stop import _answer_transport, _failing_step, _payload, _seed  # noqa: E402
from verdict_hot import claims as claims_mod
from verdict_hot import ledger
from verdict_hot import policy as policy_mod
from verdict_hot import stop as stop_mod

from agent_verdict import replay

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
_POLICY = policy_mod.load_policy(PACKAGED_DEFAULT)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    monkeypatch.delenv("VERDICT_CASSETTE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_PROVIDER", raising=False)
    return tmp_path


def _seed_stop_row(session_id: str, prompt_id: str, last_message: str) -> None:
    claim_list = list(claims_mod.extract_claims(last_message, _POLICY))
    ledger.append_row(
        {
            "schema_v": 1,
            "session_id": session_id,
            "event": "stop",
            "prompt_id": prompt_id,
            "agent_id": None,
            "stop_hook_active": False,
            "final_message_excerpt": last_message,
            "claims": claim_list,
            "background_tasks_n": 0,
        }
    )


def _run_stop(
    session_id: str, prompt_id: str, last_message: str, overrides: dict[str, float]
) -> None:
    _seed_stop_row(session_id, prompt_id, last_message)
    stop_mod.handle(
        _payload(session_id=session_id, prompt_id=prompt_id, last_message=last_message),
        _POLICY,
        time.monotonic(),
        "key",
        transport=_answer_transport(overrides),
    )


def _build_fixture_ledger() -> None:
    # 1. stand-down (plan mode): no evidence gathered, gate_reason is not
    # "evidence" at all.
    stop_mod.handle(
        _payload(session_id="s-standdown", prompt_id="p1", permission_mode="plan"),
        _POLICY,
        time.monotonic(),
        "key",
    )

    # 2. clean pass: a well-supported claim, no failures.
    _run_stop("s-pass", "p1", "Implemented the endpoint successfully.", {"claim_c1": 0.9})

    # 3. R1 block: an unresolved failure the message claims is done.
    _seed("s-r1", _failing_step(prompt_id="p1"))
    _run_stop("s-r1", "p1", "All tests pass now.", {"claims_done": 0.9, "acks_failures": 0.0})

    # 4. R2 block: a mutation with no passing check after it.
    _seed(
        "s-r2",
        [
            {
                "event": "post",
                "prompt_id": "p1",
                "tool_name": "Write",
                "tool_use_id": "w1",
                "input_excerpt": "edit file.py",
                "status": "ok",
                "is_check": False,
                "soft_fail_candidate": False,
                "never_send": False,
                "out_head": "",
                "out_tail": "",
            }
        ],
    )
    _run_stop("s-r2", "p1", "Let me know if you need anything else.", {"claims_check_passed": 0.9})

    # 5. R3 block: a confirmed soft failure (exit 0, masked failure output).
    _seed(
        "s-r3",
        [
            {
                "event": "post",
                "prompt_id": "p1",
                "tool_name": "Bash",
                "tool_use_id": "b1",
                "input_excerpt": "npm run build 2>/dev/null",
                "status": "ok",
                "is_check": False,
                "soft_fail_candidate": True,
                "never_send": False,
                "out_head": "FAIL",
                "out_tail": "",
            }
        ],
    )
    _run_stop(
        "s-r3",
        "p1",
        "Build finished, all good.",
        {"claims_done": 0.95, "acks_failures": 0.0, "softfail_1": 0.9},
    )

    # 6. R4 flag: a weak claim, no other evidence.
    _run_stop("s-r4", "p1", "I implemented the health check endpoint.", {"claim_c1": 0.1})


def _jsonl_lines(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip().startswith("{")]


def _skipped_count(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("skipped="):
            return int(line.split("=", 1)[1])
    raise AssertionError(f"no skipped= trailer in output:\n{output}")


def test_replaying_the_shipped_policy_reproduces_recorded_decisions(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _build_fixture_ledger()

    code = replay.main(["--policy", str(PACKAGED_DEFAULT), "--json"])

    assert code == 0
    output = capsys.readouterr().out
    rows = _jsonl_lines(output)
    by_session = {row["session_id"]: row for row in rows}

    assert by_session["s-pass"]["old"] == "pass"
    assert by_session["s-pass"]["new"] == "pass"
    assert by_session["s-r1"]["old"] == "block"
    assert by_session["s-r1"]["new"] == "block"
    assert by_session["s-r1"]["rule_id_old"] == "R1"
    assert by_session["s-r1"]["rule_id_new"] == "R1"
    assert by_session["s-r2"]["old"] == "block"
    assert by_session["s-r2"]["new"] == "block"
    assert by_session["s-r2"]["rule_id_old"] == "R2"
    assert by_session["s-r3"]["old"] == "block"
    assert by_session["s-r3"]["new"] == "block"
    assert by_session["s-r3"]["rule_id_old"] == "R3"
    assert by_session["s-r4"]["old"] == "flag"
    assert by_session["s-r4"]["new"] == "flag"
    assert by_session["s-r4"]["rule_id_old"] == "R4"

    for row in rows:
        assert row["moved_by"] == "-"

    # The stand-down never reaches the replay output at all.
    assert "s-standdown" not in by_session
    assert _skipped_count(output) == 1


def test_raising_t_done_flips_exactly_the_straddling_row(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _build_fixture_ledger()
    raised_policy = json.loads(PACKAGED_DEFAULT.read_text(encoding="utf-8"))
    raised_policy["thresholds"]["t_done"] = 0.92  # above R1's 0.9, below R3's 0.95
    policy_path = tmp_path / "raised.json"
    policy_path.write_text(json.dumps(raised_policy), encoding="utf-8")

    code = replay.main(["--policy", str(policy_path), "--json"])

    assert code == 0
    rows = _jsonl_lines(capsys.readouterr().out)
    by_session = {row["session_id"]: row for row in rows}

    changed = [
        row for row in rows if row["old"] != row["new"] or row["rule_id_old"] != row["rule_id_new"]
    ]
    assert [row["session_id"] for row in changed] == ["s-r1"]
    assert by_session["s-r1"]["moved_by"] == "t_done"
    assert by_session["s-r1"]["old"] == "block"
    assert by_session["s-r1"]["new"] != "block"

    # Every other row is untouched by the t_done change.
    assert by_session["s-pass"]["moved_by"] == "-"
    assert by_session["s-r2"]["moved_by"] == "-"
    assert by_session["s-r3"]["moved_by"] == "-"
    assert by_session["s-r4"]["moved_by"] == "-"


def test_text_mode_prints_one_line_per_row_plus_a_skipped_trailer(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _build_fixture_ledger()

    code = replay.main(["--policy", str(PACKAGED_DEFAULT)])

    assert code == 0
    output = capsys.readouterr().out
    assert "s-r1" in output
    assert "old=block" in output
    assert "new=block" in output
    assert "moved_by=" in output
    assert _skipped_count(output) == 1


# --- C1: span must be built from the decision-time prefix, not the whole file ---


def _build_retry_fixture() -> None:
    """One session, one prompt_id, two stops: the first sees an unresolved
    failure and blocks (R1); a retry between the two stops resolves it;
    the second stop, now with nothing unresolved, passes. Replaying the
    UNCHANGED shipped policy must reproduce both recorded decisions --
    building either stop's span from the *whole* session file would let
    the retry (which is real evidence for stop 2) leak backwards and
    retroactively "resolve" stop 1's failure too.
    """
    _seed("s-retry", _failing_step(prompt_id="p1", tool_use_id="f1"))
    _run_stop("s-retry", "p1", "All tests pass now.", {"claims_done": 0.9, "acks_failures": 0.0})

    _seed(
        "s-retry",
        [
            {
                "event": "post",
                "prompt_id": "p1",
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
    _run_stop("s-retry", "p1", "Retried the build; still checking.", {})


def test_replay_reproduces_both_stops_of_a_retry_within_one_prompt(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _build_retry_fixture()

    code = replay.main(["--policy", str(PACKAGED_DEFAULT), "--json"])

    assert code == 0
    rows = _jsonl_lines(capsys.readouterr().out)
    assert len(rows) == 2
    first, second = rows

    # Stop 1: the failure was still unresolved at decision time -> R1 block,
    # even though it looks resolved by the time the WHOLE file is read.
    assert first["old"] == "block"
    assert first["new"] == "block"
    assert first["rule_id_old"] == "R1"
    assert first["rule_id_new"] == "R1"

    # Stop 2: by its own decision time the retry really had happened, so it
    # legitimately sees the failure as resolved.
    assert second["old"] == "pass"
    assert second["new"] == "pass"


# --- I3: a row from a different policy_version reports moved_by "?" ------------


def test_moved_by_is_question_mark_for_a_foreign_policy_version(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed("s-r1", _failing_step(prompt_id="p1"))
    _run_stop("s-r1", "p1", "All tests pass now.", {"claims_done": 0.9, "acks_failures": 0.0})

    # Rewrite every verdict row's policy_version to something the packaged
    # default never shipped.
    from verdict_hot import paths as paths_mod

    rows = ledger.read_session("s-r1")
    session_path = paths_mod.session_file("s-r1")
    rewritten = []
    for row in rows:
        if row.get("event") == "verdict":
            row = {**row, "policy_version": "1999.01.1"}
        rewritten.append(row)
    session_path.write_text("\n".join(json.dumps(r) for r in rewritten) + "\n", encoding="utf-8")

    code = replay.main(["--policy", str(PACKAGED_DEFAULT), "--json"])

    assert code == 0
    rows_out = _jsonl_lines(capsys.readouterr().out)
    assert len(rows_out) == 1
    assert rows_out[0]["moved_by"] == "?"


# --- M2: a malformed --since value exits 2 with one message, never raises -----


def test_since_with_a_malformed_date_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = replay.main(["--policy", str(PACKAGED_DEFAULT), "--since", "not-a-date"])

    assert code == 2
    assert capsys.readouterr().err.strip() != ""


# --- Final review I1: research-mode and guard-demoted stops are replayed too


def test_always_verify_and_guard_demoted_stops_are_replayed(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Research mode: a stop with no evidence still calls the provider and
    # records gate_reason "always_verify" with real verdict rows.
    research = _POLICY._replace(stop=_POLICY.stop._replace(always_verify=True))
    _seed("s-av", [{"event": "prompt", "prompt_id": "p1", "prompt_excerpt": "say hi"}])
    _seed_stop_row("s-av", "p1", "Done.")
    stop_mod.handle(
        _payload(session_id="s-av", prompt_id="p1", last_message="Done."),
        research,
        time.monotonic(),
        "key",
        transport=_answer_transport({}),
    )

    # Enforce mode, two blocks under one prompt: the guard demotes the
    # second (gate_reason "guard_..."), but its verdict rows are real.
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MODE", "enforce")
    _seed("s-guard", _failing_step(prompt_id="p1", tool_use_id="f1"))
    _run_stop("s-guard", "p1", "All tests pass now.", {"claims_done": 0.9, "acks_failures": 0.0})
    _run_stop("s-guard", "p1", "All tests pass now.", {"claims_done": 0.9, "acks_failures": 0.0})
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_MODE")

    recorded = [r for r in ledger.read_session("s-guard") if r.get("event") == "action"]
    assert [r["gate_reason"] for r in recorded][0] == "evidence"
    assert str([r["gate_reason"] for r in recorded][1]).startswith("guard_")

    code = replay.main(["--policy", str(PACKAGED_DEFAULT), "--json"])

    assert code == 0
    out = capsys.readouterr().out
    rows = _jsonl_lines(out)
    assert len(rows) == 3, out
    assert _skipped_count(out) == 0
    by_session = {(r["session_id"], i): r for i, r in enumerate(rows)}
    assert any(k[0] == "s-av" for k in by_session)
    guard_rows = [r for r in rows if r["session_id"] == "s-guard"]
    # "old" reports would_have when present: the demoted row was recorded
    # as action=pass, would_have=block, and replay must see it.
    assert recorded[1]["action"] == "pass" and recorded[1]["would_have"] == "block"
    assert [r["old"] for r in guard_rows] == ["block", "block"]
    assert [r["new"] for r in guard_rows] == ["block", "block"]
