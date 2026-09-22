"""Tests for `verdict show` (task-6-brief.md ruling 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_verdict import show
from agent_verdict.verdict_hot import ledger


@pytest.fixture
def cli_verdict_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VERDICT_HOME", str(home))
    return home


def _row(event: str, session_id: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_v": 1,
        "session_id": session_id,
        "event": event,
        "ts": 1000.0,
        "prompt_id": extra.pop("prompt_id", "p1"),
        "agent_id": extra.pop("agent_id", None),
    }
    row.update(extra)
    return row


def _seed(session_id: str) -> None:
    ledger.append_row(_row("prompt", session_id, prompt_excerpt="x" * 200))
    ledger.append_row(
        _row(
            "post",
            session_id,
            tool_name="Bash",
            tool_use_id="t1",
            input_excerpt="echo " + "y" * 200,
            status="ok",
            exit_code=0,
            out_head="should not print this raw output",
            out_tail="",
        )
    )
    ledger.append_row(
        _row(
            "post_fail",
            session_id,
            tool_name="Bash",
            tool_use_id="t2",
            input_excerpt="false",
            status="error",
            exit_code=1,
            error_excerpt="should not print this either",
        )
    )
    ledger.append_row(
        _row(
            "verdict",
            session_id,
            question_key="claims_done",
            question_type="noul",
            answer={"type": "noul", "noul": 0.83333},
        )
    )
    ledger.append_row(
        _row(
            "action",
            session_id,
            action="flag",
            would_have=None,
            rule_id="R4",
            gate_reason="evidence",
            hook_ms=1.0,
            open_failures=[],
        )
    )


def test_missing_session_exits_1(
    cli_verdict_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = show.main(["nope"])

    assert code == 1
    out = capsys.readouterr()
    assert "nope" in (out.out + out.err)


def test_show_prints_a_timeline_without_raw_output(
    cli_verdict_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed("s1")

    code = show.main(["s1"])

    assert code == 0
    out = capsys.readouterr().out
    assert "should not print this raw output" not in out
    assert "should not print this either" not in out
    assert "y" * 200 not in out  # the long command is truncated
    assert "0.83" in out
    assert "R4" in out
    assert "flag" in out


def test_malformed_session_id_exits_2_instead_of_raising(
    cli_verdict_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`paths.session_file` raises `ValueError` for an id containing `/` or
    `..`; `show` must turn that into a one-line message and exit 2, never
    an uncaught traceback (fix round 1, M1)."""
    code = show.main(["../etc/passwd"])

    assert code == 2
    out = capsys.readouterr()
    assert (out.out + out.err).strip() != ""


def test_show_prompt_filter_narrows_to_one_prompt(cli_verdict_home: Path) -> None:
    _seed("s1")
    ledger.append_row(_row("prompt", "s1", prompt_id="p2", prompt_excerpt="second"))

    code = show.main(["s1", "--prompt", "p2"])

    assert code == 0
