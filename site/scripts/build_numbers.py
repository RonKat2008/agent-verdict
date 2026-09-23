"""docs/DECISIONS.md D-033 -> site/src/data/numbers.json.

Every number the site shows is parsed from the milestone's own record of
its gate runs with an explicit regex, and carries the command that
produced it. A pattern that does not match raises `MeasurementMissing`;
nothing is ever defaulted or typed by hand (CLAUDE.md rule 8).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "DECISIONS.md"
OUT = ROOT / "site" / "src" / "data" / "numbers.json"

_D033_START = "### D-033"
_D033_END = "### D-034"
_MEASURED_ON = "2026-09-22"  # the date D-033 records for every M2 gate run


class MeasurementMissing(RuntimeError):
    """A number the site needs is not in D-033's text."""


def d033_text(text: str | None = None) -> str:
    source = text if text is not None else DECISIONS.read_text(encoding="utf-8")
    start = source.find(_D033_START)
    if start == -1:
        return source
    end = source.find(_D033_END, start)
    return source[start : end if end != -1 else None]


def _grab(pattern: str, text: str, what: str) -> str:
    match = re.search(pattern, text)
    if match is None:
        raise MeasurementMissing(f"{what}: pattern {pattern!r} not found in D-033")
    return match.group(1)


def build(text: str | None = None) -> list[dict[str, Any]]:
    section = d033_text(text)
    stop_p50 = float(_grab(r"Stop p50 ([\d.]+) ms", section, "stop_p50_ms"))
    stop_p95 = float(_grab(r"Stop p50 [\d.]+ ms, p95 ([\d.]+) ms", section, "stop_p95_ms"))
    recorder_p50 = float(
        _grab(
            r"stop-event \*\*recording\*\* \(not the provider call\) p50 ([\d.]+)ms",
            section,
            "recorder_p50_ms",
        )
    )
    tests = int(_grab(r"([\d,]+) tests at the tag", section, "tests").replace(",", ""))
    coverage = float(_grab(r"\*\*([\d.]+)%\*\* \(2,778 statements\)", section, "coverage_pct"))
    return [
        _entry(
            "stop_p50_ms",
            "Stop hook p50 (live, OpenRouter)",
            stop_p50,
            "ms",
            "uv run python scripts/bench_stop_live.py --n 50",
            "In-process time from hook entry; excludes the ~35 ms interpreter start.",
        ),
        _entry(
            "stop_p95_ms",
            "Stop hook p95 (live, OpenRouter)",
            stop_p95,
            "ms",
            "uv run python scripts/bench_stop_live.py --n 50",
            "Gate G2.6 bound is 2,500 ms.",
        ),
        _entry(
            "recorder_p50_ms",
            "Recorder p50 through the launcher",
            recorder_p50,
            "ms",
            "make bench-hook",
            "Stop-event recording only, no provider call; default python3.",
        ),
        _entry("tests", "Tests at tag m2", tests, "", "make test", ""),
        _entry(
            "coverage_pct",
            "Hot-path statement coverage",
            coverage,
            "%",
            "make coverage-hot",
            "Merged over plugin/hooks and src copies; gate is 90.",
        ),
    ]


def _entry(
    id_: str, label: str, value: float | int, unit: str, command: str, note: str
) -> dict[str, Any]:
    return {
        "id": id_,
        "label": label,
        "value": value,
        "unit": unit,
        "source_command": command,
        "measured_on": _MEASURED_ON,
        "note": note,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="write numbers.json from D-033")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    try:
        data = build()
    except MeasurementMissing as exc:
        print(f"build_numbers: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(data)} number(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
