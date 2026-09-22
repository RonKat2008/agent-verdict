"""Tests for `verdict policy lint` (task-6-brief.md ruling 4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_verdict import policy_lint

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"


def _load_default() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(PACKAGED_DEFAULT.read_text(encoding="utf-8"))
    return result


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_shipped_default_lints_clean(tmp_path: Path) -> None:
    path = _write(tmp_path, _load_default())

    problems = policy_lint.lint(path)

    assert problems == []


def test_main_prints_ok_and_exits_0_for_a_clean_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _load_default())

    code = policy_lint.main([str(path)])

    assert code == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_missing_required_key_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    del data["thresholds"]
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert len(problems) == 1
    assert "thresholds" in problems[0]


def test_threshold_out_of_range_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    data["thresholds"]["t_done"] = 1.5
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert any("t_done" in p for p in problems)


def test_t_ack_above_t_ack_hi_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    data["thresholds"]["t_ack"] = 0.9
    data["thresholds"]["t_ack_hi"] = 0.5
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert any("t_ack" in p for p in problems)


def test_max_blocks_above_ceiling_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    data["stop"]["max_blocks_per_prompt"] = 5
    data["stop"]["ceiling"] = 2
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert any("ceiling" in p for p in problems)


def test_deadline_above_budget_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    data["provider"]["deadline_s"] = 10.0
    data["provider"]["budget_s"] = 2.5
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert any("deadline_s" in p for p in problems)


def test_target_tokens_above_max_tokens_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    data["state"]["target_tokens"] = 99999
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert any("target_tokens" in p for p in problems)


def test_max_prompts_below_1_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    data["span"]["max_prompts"] = 0
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert any("max_prompts" in p for p in problems)


def test_bad_denylist_regex_is_a_problem(tmp_path: Path) -> None:
    data = _load_default()
    data["denylist"][0] = "("  # unbalanced group, fails to compile
    path = _write(tmp_path, data)

    problems = policy_lint.lint(path)

    assert any(data["denylist_ids"][0] in p for p in problems)


def test_main_exits_1_and_prints_each_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _load_default()
    data["thresholds"]["t_done"] = 5.0
    path = _write(tmp_path, data)

    code = policy_lint.main([str(path)])

    assert code == 1
    assert "t_done" in capsys.readouterr().out
