import argparse

import pytest

from agent_verdict import __version__
from agent_verdict.cli import build_parser, main


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"verdict {__version__}"


def test_no_arguments_prints_help_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_doctor_subcommand_accepts_every_doctor_flag() -> None:
    """`verdict doctor ...` and `verdict-doctor` share one flag definition
    (final review, minor): the subparser is built from
    `doctor.build_arg_parser`, so a flag added there needs no second
    declaration here."""
    from agent_verdict import doctor

    parser = build_parser()
    args = parser.parse_args(["doctor", "--no-probe", "--audit", "--json"])

    assert (args.no_probe, args.audit, args.json) == (True, True, True)
    doctor_flags = {
        action.dest for action in doctor.build_arg_parser()._actions if action.dest != "help"
    }
    assert doctor_flags <= set(vars(args))


def test_stats_subcommand_accepts_every_stats_flag() -> None:
    from agent_verdict import stats

    parser = build_parser()
    args = parser.parse_args(["stats", "--json", "--count"])

    assert (args.json, args.count) == (True, True)
    stats_flags = {
        action.dest for action in stats.build_arg_parser()._actions if action.dest != "help"
    }
    assert stats_flags <= set(vars(args))


def test_policy_lint_is_a_nested_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_verdict import policy_lint

    seen: dict[str, object] = {}

    def _fake_run(args: argparse.Namespace) -> int:
        seen["file"] = args.file
        return 0

    monkeypatch.setattr(policy_lint, "run", _fake_run)

    assert main(["policy", "lint", "some-file.json"]) == 0
    assert seen == {"file": "some-file.json"}


def test_every_new_subcommand_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_verdict import export, purge, replay, show

    for module, argv in (
        (show, ["show", "s1"]),
        (replay, ["replay", "--policy", "p.json"]),
        (purge, ["purge", "--all", "--yes"]),
        (export, ["export", "--goldset", "--out", "o.jsonl"]),
    ):
        called: list[int] = []
        monkeypatch.setattr(module, "run", lambda args, called=called: called.append(1) or 0)
        assert main(argv) == 0
        assert called == [1]


def test_cli_doctor_passes_no_probe_through(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_verdict import doctor

    seen: dict[str, object] = {}

    def _fake_run(args: argparse.Namespace) -> int:
        seen["no_probe"] = args.no_probe
        return 0

    monkeypatch.setattr(doctor, "run", _fake_run)

    assert main(["doctor", "--no-probe"]) == 0
    assert seen == {"no_probe": True}
