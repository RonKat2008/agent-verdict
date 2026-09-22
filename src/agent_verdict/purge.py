"""`verdict purge [--session ID] [--older-than 7d] [--all]` (task-6-brief.md
ruling 2).

The only path that removes a "corpus" session -- one that reached a real
Stop event and carries a `stop` ledger row -- during the study window
(D-021); the automatic `SessionEnd` prune (`verdict_hot/prune.py`) never
touches those. `purge` prints how many of the sessions it is about to
delete are unlabeled corpus sessions before asking for confirmation, so an
owner running this by hand sees exactly what they are about to lose.

Only `events/<id>.jsonl` files are ever unlinked, and never through a
symlink; `labels.jsonl`, `hook.log`, `breaker.json`, and `index.db` are
never opened for writing here at all.

Confirmation reads from an injectable `input_stream` (default `sys.stdin`)
so tests never depend on the process's real stdin. When that stream is not
a tty and `--yes` was not given, `purge` refuses outright (exit 2) rather
than blocking on input it can never receive interactively.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TextIO

from agent_verdict.verdict_hot import paths
from agent_verdict.verdict_hot.prune import has_stop_row, labeled_session_ids

_SECONDS_PER_DAY = 86400
_SUFFIX_SECONDS = {"h": 3600, "d": _SECONDS_PER_DAY}


class PurgeError(ValueError):
    """Raised for a malformed `--older-than` value."""


def _parse_older_than(raw: str) -> float:
    raw = raw.strip()
    if len(raw) < 2 or raw[-1] not in _SUFFIX_SECONDS:
        raise PurgeError(f"--older-than must end in 'd' or 'h', got {raw!r}")
    try:
        amount = float(raw[:-1])
    except ValueError as exc:
        raise PurgeError(f"--older-than must be a number followed by 'd' or 'h': {raw!r}") from exc
    return amount * _SUFFIX_SECONDS[raw[-1]]


def _select_sessions(args: argparse.Namespace, now: float) -> list[Path]:
    if args.session is not None:
        target = paths.session_file(args.session)
        return [target] if target.exists() else []
    if args.all:
        return sorted(paths.events_dir().glob("*.jsonl"))
    seconds = _parse_older_than(args.older_than)
    cutoff = now - seconds
    selected = []
    for path in sorted(paths.events_dir().glob("*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff:
                selected.append(path)
        except OSError:
            continue
    return selected


def _is_unlabeled_corpus_session(path: Path, labeled: frozenset[str]) -> bool:
    return path.stem not in labeled and has_stop_row(path)


def _confirm(count: int, input_stream: TextIO, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    is_tty = getattr(input_stream, "isatty", lambda: False)()
    if not is_tty:
        return False
    print(f"Delete {count} session file(s)? [y/N] ", end="")
    line = input_stream.readline()
    return line.strip().lower() == "y"


def build_arg_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verdict purge", description="Delete session files from the ledger", add_help=add_help
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", default=None, help="delete one session by id")
    group.add_argument("--older-than", default=None, help="delete sessions older than e.g. 7d, 2h")
    group.add_argument("--all", action="store_true", help="delete every session")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return parser


def run(args: argparse.Namespace, input_stream: TextIO | None = None) -> int:
    stream = input_stream if input_stream is not None else sys.stdin
    try:
        selected = _select_sessions(args, time.time())
    except PurgeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not selected:
        print("no sessions match")
        return 0

    labeled = labeled_session_ids()
    unlabeled_corpus = sum(1 for p in selected if _is_unlabeled_corpus_session(p, labeled))

    for path in selected:
        print(f"would delete: {path.stem}")
    print(
        f"{unlabeled_corpus} of {len(selected)} selected session(s) are unlabeled corpus sessions"
    )

    if not _confirm(len(selected), stream, args.yes):
        if not args.yes and not getattr(stream, "isatty", lambda: False)():
            print("refusing: no --yes and stdin is not a tty", file=sys.stderr)
            return 2
        print("aborted")
        return 0

    deleted = 0
    for path in selected:
        try:
            if path.is_symlink():
                print(f"refusing to delete a symlink: {path}", file=sys.stderr)
                continue
            path.unlink()
            deleted += 1
        except OSError as exc:
            print(f"failed to delete {path}: {exc}", file=sys.stderr)
    print(f"deleted {deleted} session file(s)")
    return 0


def main(argv: list[str] | None = None, input_stream: TextIO | None = None) -> int:
    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    return run(args, input_stream)


__all__ = ["build_arg_parser", "run", "main", "PurgeError"]
