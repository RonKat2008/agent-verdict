"""`verdict stats`: summarize the local ledger (task-6-brief.md).

Reads the JSONL ledger directly through `agent_verdict.verdict_hot.ledger`
(the synced copy of the hot path) and reports counts a contributor or the
owner can sanity-check the collector with: how many sessions and prompts
were seen, how many tool calls succeeded or failed, how many stops carried
at least one success claim, how often the never-send gate fired, how many
redaction hits were recorded, a per-event row count, and the date range the
ledger covers.

`compute_stats()` is deliberately split into a pure aggregator (`_aggregate`,
tested directly with injected rows) and an impure loader (`_load_all_rows`)
so the counting logic itself needs no disk or environment to test.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_verdict.verdict_hot import ledger

_TOOL_EVENT = "post"
_FAILURE_EVENT = "post_fail"
_STOP_EVENT = "stop"


@dataclass(frozen=True)
class Stats:
    sessions: int
    prompts: int
    tool_rows: int
    failure_rows: int
    stops: int
    stops_with_claim: int
    never_send_rows: int
    redaction_hits: int
    rows_per_event: dict[str, int] = field(default_factory=dict)
    date_range: dict[str, str | None] = field(default_factory=lambda: {"start": None, "end": None})

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "prompts": self.prompts,
            "tool_rows": self.tool_rows,
            "failure_rows": self.failure_rows,
            "stops": self.stops,
            "stops_with_claim": self.stops_with_claim,
            "never_send_rows": self.never_send_rows,
            "redaction_hits": self.redaction_hits,
            "rows_per_event": dict(self.rows_per_event),
            "date_range": dict(self.date_range),
        }


def _load_all_rows() -> Iterator[dict[str, object]]:
    for session_path in ledger.iter_sessions():
        yield from ledger.read_session(session_path.stem)


def _to_iso(ts: object) -> str | None:
    if not isinstance(ts, (int, float)):
        return None
    return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()


def _aggregate(rows: Iterable[Mapping[str, object]]) -> Stats:
    sessions: set[object] = set()
    prompts = 0
    tool_rows = 0
    failure_rows = 0
    stops = 0
    stops_with_claim = 0
    never_send_rows = 0
    redaction_hits = 0
    rows_per_event: dict[str, int] = {}
    min_ts: float | None = None
    max_ts: float | None = None

    for row in rows:
        event = row.get("event")
        if isinstance(event, str):
            rows_per_event[event] = rows_per_event.get(event, 0) + 1
        session_id = row.get("session_id")
        if session_id is not None:
            sessions.add(session_id)

        if event == "prompt":
            prompts += 1
        elif event == _TOOL_EVENT:
            tool_rows += 1
        elif event == _FAILURE_EVENT:
            failure_rows += 1
        elif event == _STOP_EVENT:
            stops += 1
            claims = row.get("claims")
            if isinstance(claims, (list, tuple)) and len(claims) > 0:
                stops_with_claim += 1

        if row.get("never_send") is True:
            never_send_rows += 1

        hits = row.get("redaction_hits")
        if isinstance(hits, (int, float)) and hits > 0:
            redaction_hits += int(hits)

        ts = row.get("ts")
        if isinstance(ts, (int, float)):
            min_ts = ts if min_ts is None else min(min_ts, ts)
            max_ts = ts if max_ts is None else max(max_ts, ts)

    return Stats(
        sessions=len(sessions),
        prompts=prompts,
        tool_rows=tool_rows,
        failure_rows=failure_rows,
        stops=stops,
        stops_with_claim=stops_with_claim,
        never_send_rows=never_send_rows,
        redaction_hits=redaction_hits,
        rows_per_event=rows_per_event,
        date_range={"start": _to_iso(min_ts), "end": _to_iso(max_ts)},
    )


def compute_stats(rows: Iterable[Mapping[str, object]] | None = None) -> Stats:
    """Aggregate ledger rows. Reads the real ledger when `rows` is omitted."""
    return _aggregate(rows if rows is not None else _load_all_rows())


def _print_report(result: Stats) -> None:
    print(f"sessions: {result.sessions}")
    print(f"prompts: {result.prompts}")
    print(f"tool rows: {result.tool_rows}")
    print(f"failure rows: {result.failure_rows}")
    print(f"stops: {result.stops}")
    print(f"stops with a claim: {result.stops_with_claim}")
    print(f"never-send rows: {result.never_send_rows}")
    print(f"redaction hits: {result.redaction_hits}")
    print("rows per event:")
    for event_name in sorted(result.rows_per_event):
        print(f"  {event_name}: {result.rows_per_event[event_name]}")
    print(f"date range: {result.date_range['start']} .. {result.date_range['end']}")


def build_arg_parser(add_help: bool = True) -> argparse.ArgumentParser:
    """`add_help=False` makes this usable as an argparse `parents=` entry,
    so `cli.py` reuses these flags instead of redeclaring them."""
    parser = argparse.ArgumentParser(
        prog="verdict stats", description="Summarize the local ledger", add_help=add_help
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--count", action="store_true", help="print only the stop count")
    return parser


def run(args: argparse.Namespace) -> int:
    """Run `stats` from an already-parsed namespace (see `doctor.run`)."""
    result = compute_stats()
    if args.count:
        print(result.stops)
    elif args.json:
        print(json.dumps(result.to_dict()))
    else:
        _print_report(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


__all__ = ["Stats", "compute_stats", "build_arg_parser", "run", "main"]
