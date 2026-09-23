from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import record_site_scenarios as rss  # noqa: E402

EXPECTED = ["clean-pass", "honest-failure", "soft-failure", "unbacked-check", "unreported-failure"]


def test_refuses_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert rss.main(["--scenario", "clean-pass"]) == 3


def test_every_scenario_dir_has_a_valid_definition() -> None:
    names = sorted(p.name for p in (ROOT / "site" / "scenarios").iterdir() if p.is_dir())
    assert names == EXPECTED
    for name in names:
        spec = json.loads((ROOT / "site" / "scenarios" / name / "scenario.json").read_text())
        assert spec["name"] == name
        assert set(spec) - {"tools"} == {
            "name",
            "title",
            "summary",
            "prompt",
            "mode",
            "staged_claim",
            "expect",
        }
        assert spec["mode"] in ("shadow", "enforce")
        assert isinstance(spec["staged_claim"], bool)
        assert set(spec["expect"]) == {"action", "rule_id"}
        assert len(spec["summary"]) <= 240
        assert (ROOT / "site" / "scenarios" / name / "repo").is_dir()


def test_ledger_writer_copies_only_the_one_session(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "events").mkdir(parents=True)
    (home / "events" / "s1.jsonl").write_text(
        '{"schema_v":1,"event":"session_start","session_id":"s1"}\n'
    )
    out = tmp_path / "out"
    rss.copy_ledger(home, out)
    assert (out / "ledger.jsonl").read_text().count("\n") == 1


def test_ledger_writer_refuses_two_sessions(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "events").mkdir(parents=True)
    (home / "events" / "s1.jsonl").write_text("{}\n")
    (home / "events" / "s2.jsonl").write_text("{}\n")
    with pytest.raises(RuntimeError):
        rss.copy_ledger(home, tmp_path / "out")


def test_unbacked_check_allows_read_and_edit_only() -> None:
    spec = json.loads(
        (ROOT / "site" / "scenarios" / "unbacked-check" / "scenario.json").read_text()
    )
    assert rss._allowed_tools(spec) == "Read,Edit"
    assert rss._allowed_tools({"name": "x"}) == "Bash"


def test_temp_paths_are_scrubbed_from_the_committed_ledger() -> None:
    raw = (
        '{"input_excerpt":"/private/var/folders/ab/xyz123/T/verdict-site-cwd-h24ee4w7/app.py",'
        '"x":"/tmp/verdict-site-home-q1/events"}'
    )
    out = rss.scrub_temp_paths(raw)
    assert out == '{"input_excerpt":"<cwd>/app.py","x":"<home>/events"}'
