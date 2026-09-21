"""Command line entry point. Subcommands arrive in later milestones."""

from __future__ import annotations

import argparse

from agent_verdict import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verdict", description="Verdict command line tools")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"verdict {__version__}")
        return 0
    parser.print_help()
    return 0
