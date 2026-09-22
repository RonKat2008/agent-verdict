"""Command line entry point. Dispatches to the `stats` and `doctor` subcommands.

Each subcommand owns its flags exactly once: the subparsers are built from
`stats.build_arg_parser()` and `doctor.build_arg_parser()` as argparse
`parents`, and the parsed namespace is handed straight to `stats.run` /
`doctor.run`. Before this, `verdict doctor --no-probe` would have needed a
second flag declaration here and a third in the argv the dispatcher
rebuilt, which is exactly how `--fix-interpreter` and friends drifted
(final review, minor).
"""

from __future__ import annotations

import argparse

from agent_verdict import __version__, doctor, stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verdict", description="Verdict command line tools")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "stats",
        help="summarize the local ledger",
        parents=[stats.build_arg_parser(add_help=False)],
        add_help=True,
    )
    subparsers.add_parser(
        "doctor",
        help="diagnose the local environment",
        parents=[doctor.build_arg_parser(add_help=False)],
        add_help=True,
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "stats":
        return stats.run(args)

    if args.command == "doctor":
        return doctor.run(args)

    if args.version:
        print(f"verdict {__version__}")
        return 0

    parser.print_help()
    return 0
