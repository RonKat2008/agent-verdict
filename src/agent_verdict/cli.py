"""Command line entry point. Dispatches to every `verdict` subcommand.

Each subcommand owns its flags exactly once: the subparsers are built from
`<module>.build_arg_parser()` as argparse `parents`, and the parsed
namespace is handed straight to `<module>.run`. Before this, `verdict
doctor --no-probe` would have needed a second flag declaration here and a
third in the argv the dispatcher rebuilt, which is exactly how
`--fix-interpreter` and friends drifted (final review, minor).

`policy lint` is the one nested subcommand (task-6-brief.md controller
notes): `verdict policy` has its own subparsers, currently holding just
`lint`, so a second `policy` subcommand can be added later without
reshaping this dispatch.
"""

from __future__ import annotations

import argparse

from agent_verdict import (
    __version__,
    doctor,
    export,
    policy_lint,
    purge,
    replay,
    show,
    stats,
)


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
    subparsers.add_parser(
        "show",
        help="show one session's timeline",
        parents=[show.build_arg_parser(add_help=False)],
        add_help=True,
    )
    subparsers.add_parser(
        "replay",
        help="re-evaluate stored verdicts under a policy",
        parents=[replay.build_arg_parser(add_help=False)],
        add_help=True,
    )
    subparsers.add_parser(
        "purge",
        help="delete session files from the ledger",
        parents=[purge.build_arg_parser(add_help=False)],
        add_help=True,
    )
    subparsers.add_parser(
        "export",
        help="export a derived, redaction-safe goldset",
        parents=[export.build_arg_parser(add_help=False)],
        add_help=True,
    )

    policy_parser = subparsers.add_parser("policy", help="policy file tools")
    policy_subparsers = policy_parser.add_subparsers(dest="policy_command")
    policy_subparsers.add_parser(
        "lint",
        help="validate a policy file",
        parents=[policy_lint.build_arg_parser(add_help=False)],
        add_help=True,
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Late-bound attribute lookups (`doctor.run(args)`, not a dict built at
    # import time): tests monkeypatch e.g. `doctor.run` after `cli` is
    # already imported, and a dict built once at module load would still
    # hold the original, un-patched function object.
    if args.command == "stats":
        return stats.run(args)
    if args.command == "doctor":
        return doctor.run(args)
    if args.command == "show":
        return show.run(args)
    if args.command == "replay":
        return replay.run(args)
    if args.command == "purge":
        return purge.run(args)
    if args.command == "export":
        return export.run(args)

    if args.command == "policy":
        if args.policy_command == "lint":
            return policy_lint.run(args)
        print("usage: verdict policy lint <file>")
        return 2

    if args.version:
        print(f"verdict {__version__}")
        return 0

    parser.print_help()
    return 0
