"""Tests for scripts/bench_hook.py's pure percentile/threshold logic (gate G1.2).

No subprocess is spawned here (task-6-brief.md's required-tests list); the
`bench_one_interpreter`/`run_family` subprocess path is exercised instead by
`make bench-hook` directly, and is deliberately not unit-tested here.
"""

from __future__ import annotations

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


def test_gate_failures_flags_p50_over_threshold() -> None:
    timing = bh.EventTiming("post", p50=61.0, p95=10.0)

    failures = bh.gate_failures([timing])

    assert len(failures) == 1
    assert "p50" in failures[0]


def test_gate_failures_flags_p95_over_threshold() -> None:
    timing = bh.EventTiming("stop", p50=10.0, p95=121.0)

    failures = bh.gate_failures([timing])

    assert len(failures) == 1
    assert "p95" in failures[0]


def test_gate_failures_flags_both_when_both_exceed_thresholds() -> None:
    timing = bh.EventTiming("post_fail", p50=70.8, p95=130.0)

    failures = bh.gate_failures([timing])

    assert len(failures) == 2


def test_gate_failures_empty_when_within_thresholds() -> None:
    timing = bh.EventTiming("stop", p50=10.0, p95=50.0)

    assert bh.gate_failures([timing]) == []


def test_gate_failures_checks_every_event_independently() -> None:
    good = bh.EventTiming("post", p50=10.0, p95=20.0)
    bad = bh.EventTiming("post_fail", p50=65.0, p95=125.0)

    failures = bh.gate_failures([good, bad])

    assert len(failures) == 2
    assert all("post_fail" in f for f in failures)
