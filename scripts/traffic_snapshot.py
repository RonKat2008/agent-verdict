"""Save a GitHub repository's traffic data before GitHub forgets it.

GitHub keeps views, clones, referrers, and popular paths for only the last
14 days, and only the owner can read them. Run this at least weekly and the
history accumulates in one JSON file per repository:

    uv run python scripts/traffic_snapshot.py                 # this repo
    uv run python scripts/traffic_snapshot.py --repo OWNER/REPO

Storage: ``~/.config/agent-verdict/traffic/<owner>__<repo>.json`` (outside
the repository; override with ``--store``). Daily series (views, clones) are
merged per day, keeping the larger count, because a day captured before it
ends can only grow. Referrers and popular paths are 14-day totals with no
per-day breakdown, so each capture is kept under its capture date. Stars,
forks, and watchers are recorded once per capture date.

Clones are a plausible proxy for plugin installs (adding a marketplace from a
GitHub repository fetches it), but Claude Code's documentation does not state
the mechanism, so treat clone counts as a proxy, not an install count.

Uses the authenticated ``gh`` CLI; no token is read or stored by this script.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_REPO = "RonKat2008/agent-verdict"
DEFAULT_STORE_DIR = Path.home() / ".config" / "agent-verdict" / "traffic"
_GH_TIMEOUT_S = 30.0
_DAILY_SERIES = ("views", "clones")


def gh_json(path: str) -> Any:
    """Run ``gh api <path>`` and parse its JSON output."""
    proc = subprocess.run(
        ["gh", "api", path],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_GH_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def empty_store() -> dict[str, Any]:
    return {"views": {}, "clones": {}, "referrers": {}, "paths": {}, "repo_stats": {}}


def merge_daily(existing: dict[str, Any], payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Merge one daily series (``views`` or ``clones``) by day, keeping the larger counts."""
    merged = dict(existing)
    for point in payload.get(key, []):
        day = str(point["timestamp"])[:10]
        old = merged.get(day, {"count": 0, "uniques": 0})
        merged[day] = {
            "count": max(int(old["count"]), int(point["count"])),
            "uniques": max(int(old["uniques"]), int(point["uniques"])),
        }
    return merged


def merge_snapshot(
    store: dict[str, Any],
    captured_on: str,
    views: dict[str, Any],
    clones: dict[str, Any],
    referrers: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    repo_stats: dict[str, int],
) -> dict[str, Any]:
    """Return a new store with one capture merged in; ``store`` is not mutated."""
    out = {name: dict(store.get(name, {})) for name in empty_store()}
    out["views"] = merge_daily(out["views"], views, "views")
    out["clones"] = merge_daily(out["clones"], clones, "clones")
    out["referrers"][captured_on] = referrers
    out["paths"][captured_on] = paths
    out["repo_stats"][captured_on] = repo_stats
    return out


def summarize(store: dict[str, Any]) -> list[str]:
    """Human-readable totals across every stored day."""
    lines = []
    for key in _DAILY_SERIES:
        days = store.get(key, {})
        total = sum(int(v["count"]) for v in days.values())
        daily_uniques = sum(int(v["uniques"]) for v in days.values())
        span = f"{min(days)} .. {max(days)}" if days else "no data"
        lines.append(
            f"{key}: {total} total, {daily_uniques} summed daily uniques, {len(days)} days ({span})"
        )
    stats = store.get("repo_stats", {})
    if stats:
        latest = max(stats)
        s = stats[latest]
        lines.append(
            f"as of {latest}: {s['stars']} stars, {s['forks']} forks, {s['watchers']} watchers"
        )
    referrers = store.get("referrers", {})
    if referrers:
        latest = max(referrers)
        top = ", ".join(f"{r['referrer']} ({r['uniques']})" for r in referrers[latest][:5])
        lines.append(f"top referrers in the 14 days before {latest}: {top or 'none'}")
    lines.append("summed daily uniques overcount people who visit on more than one day")
    return lines


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_store()
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def capture(repo: str) -> tuple[Any, Any, Any, Any, dict[str, int]]:
    base = f"repos/{repo}"
    info = gh_json(base)
    stats = {
        "stars": int(info["stargazers_count"]),
        "forks": int(info["forks_count"]),
        "watchers": int(info["subscribers_count"]),
    }
    return (
        gh_json(f"{base}/traffic/views"),
        gh_json(f"{base}/traffic/clones"),
        gh_json(f"{base}/traffic/popular/referrers"),
        gh_json(f"{base}/traffic/popular/paths"),
        stats,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="save GitHub traffic data before it expires")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="OWNER/REPO")
    parser.add_argument("--store", type=Path, default=None, help="JSON file to accumulate into")
    args = parser.parse_args(argv)
    store_path = args.store or DEFAULT_STORE_DIR / f"{args.repo.replace('/', '__')}.json"
    try:
        views, clones, referrers, paths, stats = capture(args.repo)
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as exc:
        print(f"traffic_snapshot: {exc}", file=sys.stderr)
        return 1
    today = dt.datetime.now(dt.UTC).date().isoformat()
    merged = merge_snapshot(load_store(store_path), today, views, clones, referrers, paths, stats)
    save_store(store_path, merged)
    print(f"saved to {store_path}")
    for line in summarize(merged):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
