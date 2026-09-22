"""Gate G1.2: cold-start latency for `plugin/hooks/run.sh` (docs/DECISIONS.md D-029).

Spawns the real POSIX launcher once per timed run, per fixture family (the
six M1 hook events), under a temp `VERDICT_HOME`. 5 warm-up runs per family
are discarded before p50/p95 are computed over `--n` measured runs (default
40). Exits 1 if any family's p50 exceeds 75 ms or p95 exceeds 150 ms
(D-029, which superseded the plan's un-measured 60/120 ms once the actual
interpreter-start-plus-launcher floor was measured). Runs once for the
default interpreter (`VERDICT_PYTHON` left unset, so `run.sh` falls back to
`python3` on PATH) and again for `/usr/bin/python3` when it exists, unless
`--python` pins a single interpreter. Each interpreter pass also prints the
bare `python3 -S -c pass` p50 as "floor" beside every event's numbers
(D-029: "must report the bare-interpreter floor beside each number so the
fixed cost is visible").

`percentile`, `summarize_timings`, and `gate_failures` are pure and unit
tested with injected timings (no subprocess) in `tests/test_bench_hook.py`.

Every subprocess call below passes an explicit `timeout=` (anti-hang rule)
and either `stdin=subprocess.DEVNULL` (the floor probe, which reads no
input) or `input=<fixture bytes>` (every `run.sh` invocation) -- nothing
here ever inherits the caller's terminal stdin.

`stdout`/`stderr` are captured (`capture_output=True`) rather than sent to
`subprocess.DEVNULL`. This is not cosmetic: when `input=` makes `stdin` a
pipe and a `timeout=` is given, CPython's `Popen.communicate()` can only
detect the child's exit by `select()`-ing for EOF on `stdout`/`stderr` --
if those are `DEVNULL` (no pipe to select on) it falls back to
`Popen.wait()`'s exponential-backoff polling loop (0.5 ms doubling to a
50 ms cap) to notice the exit, instead of returning the instant the child's
own pipes close. That backoff added a flat, run-independent ~18-20 ms to
*every* measured invocation regardless of the event's actual cost --
confirmed by an isolated `subprocess.run` comparison (`DEVNULL` + timeout
vs `capture_output=True` + timeout, same command, same machine, same
timeout value) -- which is exactly what produced a flat ~77-78 ms profile
across every event, including `session-start`, which does none of the
work `post`/`post-fail`/`stop` do. Capturing `stdout`/`stderr` too lets
`select()` see the pipe-close EOF directly, matching a direct
`subprocess.run(..., capture_output=True)` call with no timeout at all to
within a few ms.
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_SH = REPO_ROOT / "plugin" / "hooks" / "run.sh"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "hooks"

# (run.sh arg, fixture file) for the six M1 hook events (hooks.json).
FIXTURE_FAMILIES: tuple[tuple[str, str], ...] = (
    ("session-start", "session_start.json"),
    ("prompt", "user_prompt_submit.json"),
    ("post", "post_tool_use_bash.json"),
    ("post-fail", "post_tool_use_failure_bash.json"),
    ("stop", "stop.json"),
    ("session-end", "session_end.json"),
)

WARMUPS = 5
DEFAULT_N = 40
SUBPROCESS_TIMEOUT_S = 5.0
# D-029 (docs/DECISIONS.md): measured on the development machine after the
# perf commit, interpreter start plus the shell wrapper is ~30-38ms of fixed
# cost that a 60/120ms budget never accounted for.
P50_MAX_MS = 75.0
P95_MAX_MS = 150.0
_USR_BIN_PYTHON3 = "/usr/bin/python3"


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        raise ValueError("percentile of empty input")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True)
class EventTiming:
    event: str
    p50: float
    p95: float


def summarize_timings(event: str, timings: Sequence[float]) -> EventTiming:
    return EventTiming(event, percentile(timings, 50), percentile(timings, 95))


def gate_failures(results: Sequence[EventTiming]) -> list[str]:
    failures: list[str] = []
    for result in results:
        if result.p50 > P50_MAX_MS:
            failures.append(f"{result.event}: p50 {result.p50:.1f}ms > {P50_MAX_MS:.0f}ms")
        if result.p95 > P95_MAX_MS:
            failures.append(f"{result.event}: p95 {result.p95:.1f}ms > {P95_MAX_MS:.0f}ms")
    return failures


def _time_one_invocation(event_arg: str, payload: bytes, env: dict[str, str]) -> float:
    start = time.perf_counter()
    subprocess.run(
        [str(RUN_SH), event_arg],
        input=payload,
        capture_output=True,
        env=env,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    return (time.perf_counter() - start) * 1000.0


def run_family(event_arg: str, payload: bytes, env: dict[str, str], n: int) -> list[float]:
    for _ in range(WARMUPS):
        _time_one_invocation(event_arg, payload, env)
    return [_time_one_invocation(event_arg, payload, env) for _ in range(n)]


def _time_bare_interpreter(python_path: str | None) -> float:
    """One `python3 -S -c pass` invocation, timed the same way as a hook."""
    start = time.perf_counter()
    subprocess.run(
        [python_path or "python3", "-S", "-c", "pass"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    return (time.perf_counter() - start) * 1000.0


def measure_floor(python_path: str | None, n: int) -> float:
    """D-029's "bare-interpreter floor": p50 of `python3 -S -c pass` alone.

    Same warm-up/measured-run shape as `run_family`, so it is directly
    comparable to the event timings printed beside it.
    """
    for _ in range(WARMUPS):
        _time_bare_interpreter(python_path)
    timings = [_time_bare_interpreter(python_path) for _ in range(n)]
    return percentile(timings, 50)


def _strip_dev_venv_from_path(path_value: str) -> str:
    """Drop this project's `.venv/bin` from PATH.

    `make bench-hook` runs under `uv run`, which prepends the dev venv's
    bin dir to PATH. A real Claude Code hook invocation never has that
    venv on PATH, so leaving it in would make the "default interpreter"
    pass measure the wrong interpreter (the dev venv's Python) instead of
    whatever `python3` a real installation's shell would resolve.
    """
    segments = [p for p in path_value.split(os.pathsep) if ".venv" not in p]
    return os.pathsep.join(segments)


def _build_env(verdict_home: Path, python_path: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env["VERDICT_HOME"] = str(verdict_home)
    env.pop("VERDICT_DISABLE", None)
    env["PATH"] = _strip_dev_venv_from_path(env.get("PATH", ""))
    if python_path is None:
        env.pop("VERDICT_PYTHON", None)
    else:
        env["VERDICT_PYTHON"] = python_path
    return env


def _interpreter_passes() -> list[tuple[str, str | None]]:
    passes: list[tuple[str, str | None]] = [("default (PATH python3)", None)]
    if os.path.exists(_USR_BIN_PYTHON3):
        passes.append(("/usr/bin/python3", _USR_BIN_PYTHON3))
    return passes


def bench_one_interpreter(label: str, python_path: str | None, n: int) -> list[EventTiming]:
    print(f"=== {label} ===")
    floor_p50 = measure_floor(python_path, n)
    results: list[EventTiming] = []
    with tempfile.TemporaryDirectory(prefix="verdict-bench-") as home_dir:
        env = _build_env(Path(home_dir), python_path)
        for event_arg, fixture_name in FIXTURE_FAMILIES:
            payload = (FIXTURES_DIR / fixture_name).read_bytes()
            timings = run_family(event_arg, payload, env, n)
            timing = summarize_timings(event_arg, timings)
            print(
                f"{timing.event}: p50={timing.p50:.1f}ms p95={timing.p95:.1f}ms "
                f"(n={n}, floor={floor_p50:.1f}ms)"
            )
            results.append(timing)
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench_hook", description="Measure plugin/hooks/run.sh cold-start latency (G1.2)"
    )
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="measured runs per family")
    parser.add_argument(
        "--python", type=str, default=None, help="run only against this interpreter path"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    passes = [(args.python, args.python)] if args.python else _interpreter_passes()

    all_failures: list[str] = []
    for label, python_path in passes:
        results = bench_one_interpreter(label, python_path, args.n)
        for failure in gate_failures(results):
            all_failures.append(f"[{label}] {failure}")

    if all_failures:
        print("GATE G1.2 FAILED:", file=sys.stderr)
        for failure in all_failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("GATE G1.2 PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
