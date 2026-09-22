"""Command line entry point. Dispatches to the `stats` and `doctor` subcommands."""

from __future__ import annotations

import argparse

from agent_verdict import __version__, doctor, stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verdict", description="Verdict command line tools")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    subparsers = parser.add_subparsers(dest="command")

    stats_parser = subparsers.add_parser("stats", help="summarize the local ledger")
    stats_parser.add_argument("--json", action="store_true", help="print the report as JSON")
    stats_parser.add_argument("--count", action="store_true", help="print only the stop count")

    doctor_parser = subparsers.add_parser("doctor", help="diagnose the local environment")
    doctor_parser.add_argument(
        "--audit",
        action="store_true",
        help="exit 1 if hook.log recorded an exception outcome in the last 7 days",
    )
    doctor_parser.add_argument(
        "--fix-interpreter",
        action="store_true",
        help="probe for a working interpreter and persist it to verdict_home()/interpreter",
    )
    doctor_parser.add_argument("--json", action="store_true", help="print the report as JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "stats":
        stats_argv = ["--json"] if args.json else []
        if args.count:
            stats_argv.append("--count")
        return stats.main(stats_argv)

    if args.command == "doctor":
        doctor_argv = []
        if args.audit:
            doctor_argv.append("--audit")
        if args.fix_interpreter:
            doctor_argv.append("--fix-interpreter")
        if args.json:
            doctor_argv.append("--json")
        return doctor.main(doctor_argv)

    if args.version:
        print(f"verdict {__version__}")
        return 0

    parser.print_help()
    return 0
