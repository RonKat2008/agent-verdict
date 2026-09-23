"""scripts/traffic_snapshot.py: merging keeps history and never loses a day."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import traffic_snapshot as ts  # noqa: E402

_STATS = {"stars": 3, "forks": 1, "watchers": 2}


def _series(key: str, points: list[tuple[str, int, int]]) -> dict[str, object]:
    return {
        key: [
            {"timestamp": f"{day}T00:00:00Z", "count": count, "uniques": uniques}
            for day, count, uniques in points
        ]
    }


def test_a_partial_day_is_replaced_by_the_fuller_later_capture() -> None:
    first = ts.merge_snapshot(
        ts.empty_store(),
        "2026-09-23",
        _series("views", [("2026-09-23", 2, 1)]),
        _series("clones", []),
        [],
        [],
        _STATS,
    )
    second = ts.merge_snapshot(
        first,
        "2026-09-24",
        _series("views", [("2026-09-23", 9, 4), ("2026-09-24", 1, 1)]),
        _series("clones", []),
        [],
        [],
        _STATS,
    )

    assert second["views"]["2026-09-23"] == {"count": 9, "uniques": 4}
    assert second["views"]["2026-09-24"] == {"count": 1, "uniques": 1}


def test_days_that_fall_out_of_githubs_window_are_kept() -> None:
    old = ts.merge_snapshot(
        ts.empty_store(),
        "2026-09-01",
        _series("views", [("2026-08-20", 5, 3)]),
        _series("clones", [("2026-08-20", 2, 1)]),
        [],
        [],
        _STATS,
    )
    later = ts.merge_snapshot(
        old,
        "2026-09-23",
        _series("views", [("2026-09-22", 1, 1)]),
        _series("clones", []),
        [],
        [],
        _STATS,
    )

    assert later["views"]["2026-08-20"] == {"count": 5, "uniques": 3}
    assert later["clones"]["2026-08-20"] == {"count": 2, "uniques": 1}


def test_merge_does_not_mutate_the_input_store() -> None:
    store = ts.empty_store()
    ts.merge_snapshot(
        store, "2026-09-23", _series("views", [("2026-09-23", 1, 1)]), {}, [], [], _STATS
    )
    assert store == ts.empty_store()


def test_aggregates_and_stats_are_kept_per_capture_date() -> None:
    referrers = [{"referrer": "news.ycombinator.com", "count": 40, "uniques": 30}]
    store = ts.merge_snapshot(ts.empty_store(), "2026-09-23", {}, {}, referrers, [], _STATS)

    assert store["referrers"]["2026-09-23"] == referrers
    assert store["repo_stats"]["2026-09-23"] == _STATS
    summary = "\n".join(ts.summarize(store))
    assert "news.ycombinator.com (30)" in summary
    assert "3 stars" in summary


def test_store_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "repo.json"
    store = ts.merge_snapshot(
        ts.empty_store(), "2026-09-23", _series("views", [("2026-09-23", 1, 1)]), {}, [], [], _STATS
    )
    ts.save_store(path, store)

    assert ts.load_store(path) == store
    assert ts.load_store(tmp_path / "missing.json") == ts.empty_store()
