"""Tests for scripts/bench_hook.py's pure percentile/threshold logic (gate G1.2,
docs/DECISIONS.md D-029).

No real subprocess is spawned here (task-6-brief.md's required-tests list); the
actual `run.sh`/interpreter timing is exercised by `make bench-hook` directly.
Two tests below monkeypatch `subprocess.run` to lock in the exact keyword
shape `_time_one_invocation`/`measure_floor` pass it -- D-029 exists because
that shape matters (`capture_output=True`, not `stdout=`/`stderr=DEVNULL`,
whenever a `timeout=` is combined with a pipe `stdin`): CPython's
`Popen.communicate()` can only detect a child's exit via `select()`-ing for
EOF on captured `stdout`/`stderr`, and falls back to `Popen.wait()`'s
exponential-backoff polling loop otherwise, which added a flat ~18-20ms to
every measured invocation regardless of the event's real cost.
"""

from __future__ import annotations

import subprocess

import bench_hook as bh
import pytest


def test_percentile_uses_nearest_rank() -> None:
    assert bh.percentile([10.0, 20.0, 30.0, 40.0], 50) == 20.0
    assert bh.percentile([10.0, 20.0, 30.0, 40.0], 90) == 40.0
    assert bh.percentile([5.0], 99) == 5.0


def test_percentile_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        bh.percentile([], 50)


def test_summarize_timings_computes_p50_and_p95() -> None:
    timings = [float(i) for i in range(1, 101)]

    result = bh.summarize_timings("post", timings)

    assert result.event == "post"
    assert result.p50 == bh.percentile(timings, 50)
    assert result.p95 == bh.percentile(timings, 95)


def test_gate_thresholds_match_d029() -> None:
    """docs/DECISIONS.md D-029: G1.2 is p50 <= 75ms, p95 <= 150ms per event
    through `run.sh` (supersedes the plan's un-measured 60/120ms)."""
    assert bh.P50_MAX_MS == 75.0
    assert bh.P95_MAX_MS == 150.0


def test_gate_failures_flags_p50_over_threshold() -> None:
    timing = bh.EventTiming("post", p50=76.0, p95=10.0)

    failures = bh.gate_failures([timing])

    assert len(failures) == 1
    assert "p50" in failures[0]


def test_gate_failures_flags_p95_over_threshold() -> None:
    timing = bh.EventTiming("stop", p50=10.0, p95=151.0)

    failures = bh.gate_failures([timing])

    assert len(failures) == 1
    assert "p95" in failures[0]


def test_gate_failures_flags_both_when_both_exceed_thresholds() -> None:
    timing = bh.EventTiming("post_fail", p50=90.0, p95=200.0)

    failures = bh.gate_failures([timing])

    assert len(failures) == 2


def test_gate_failures_empty_when_within_thresholds() -> None:
    timing = bh.EventTiming("stop", p50=75.0, p95=150.0)

    assert bh.gate_failures([timing]) == []


def test_gate_failures_checks_every_event_independently() -> None:
    good = bh.EventTiming("post", p50=10.0, p95=20.0)
    bad = bh.EventTiming("post_fail", p50=90.0, p95=200.0)

    failures = bh.gate_failures([good, bad])

    assert len(failures) == 2
    assert all("post_fail" in f for f in failures)


def test_time_one_invocation_captures_output_instead_of_devnull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-029's actual harness fix: `_time_one_invocation` must pass
    `capture_output=True` (never `stdout=subprocess.DEVNULL`/
    `stderr=subprocess.DEVNULL`) alongside `input=` and `timeout=`, or the
    flat-~18-20ms-per-call regression this decision documents comes back."""
    calls: list[dict[str, object]] = []

    def fake_run(cmd: object, **kwargs: object) -> None:
        calls.append({"cmd": cmd, **kwargs})

    monkeypatch.setattr(subprocess, "run", fake_run)

    bh._time_one_invocation("post-fail", b'{"k": "v"}', {"PATH": "/usr/bin"})

    assert len(calls) == 1
    call = calls[0]
    assert call["input"] == b'{"k": "v"}'
    assert call["capture_output"] is True
    assert call["timeout"] == bh.SUBPROCESS_TIMEOUT_S
    assert call["env"] == {"PATH": "/usr/bin"}
    assert "stdout" not in call
    assert "stderr" not in call


def test_measure_floor_returns_p50_of_bare_interpreter_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`measure_floor` uses the same percentile machinery as event timings,
    over `python3 -S -c pass` instead of `run.sh` -- keep this in sync with
    the actual `subprocess.run` shape (`capture_output=True`, explicit
    `stdin=DEVNULL` and `timeout=`, no `stdout=`/`stderr=`) so the fixed-cost
    regression this decision exists to catch (D-029: a `DEVNULL`+`timeout`
    call falling back to `Popen.wait()`'s polling loop instead of detecting
    exit via pipe EOF) cannot silently creep back in without a test noticing
    the call shape changed."""
    calls: list[dict[str, object]] = []

    def fake_run(cmd: object, **kwargs: object) -> None:
        calls.append({"cmd": cmd, **kwargs})

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = bh.measure_floor(None, n=3)

    assert result >= 0.0  # fake_run is near-instant; just not negative/NaN
    assert len(calls) == bh.WARMUPS + 3
    for call in calls:
        assert call["cmd"] == ["python3", "-S", "-c", "pass"]
        assert call["stdin"] is subprocess.DEVNULL
        assert call["capture_output"] is True
        assert call["timeout"] == bh.SUBPROCESS_TIMEOUT_S
        assert "stdout" not in call
        assert "stderr" not in call
