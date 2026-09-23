"""Record the demo site's scenarios with real `claude -p` sessions (billed).

For each `site/scenarios/<name>/` this copies `repo/` into a temp cwd,
runs `claude -p` with the plugin loaded in the scenario's mode against a
temp VERDICT_HOME, checks the run the same way `e2e_cheap.py` does, and
copies the single session ledger to `site/scenarios/<name>/ledger.jsonl`
with a `recording.json` beside it. Refuses without OPENROUTER_API_KEY.
Never reads ~/.config/agent-verdict/dev.env.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_cheap as e2e  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "site" / "scenarios"
MAX_TURNS = 8
TIMEOUT_S = 300.0
_SUMMARY_KEYS = (
    "event",
    "tool_name",
    "exit_code",
    "action",
    "would_have",
    "rule_id",
    "gate_reason",
)


def load_scenario(name: str) -> dict[str, Any]:
    text = (SCENARIOS / name / "scenario.json").read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


_TEMP_DIR_RE = re.compile(
    r"/(?:private/)?(?:var|tmp)/[^\s\"']*?verdict-site-(cwd|home)-[A-Za-z0-9_]+"
)


def scrub_temp_paths(text: str) -> str:
    """The recorder's temp cwd and VERDICT_HOME paths carry a per-machine
    temp-folder token; the committed ledger replaces them with `<cwd>` and
    `<home>` so nothing machine-specific is published."""
    return _TEMP_DIR_RE.sub(lambda m: f"<{m.group(1)}>", text)


def copy_ledger(verdict_home: Path, out_dir: Path) -> Path:
    files = sorted((verdict_home / "events").glob("*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"expected exactly one session ledger, found {len(files)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "ledger.jsonl"
    target.write_text(scrub_temp_paths(files[0].read_text(encoding="utf-8")), encoding="utf-8")
    return target


def _claude_version() -> str:
    proc = subprocess.run(
        ["claude", "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.stdout.strip().split()[0] if proc.stdout.strip() else "unknown"


def _allowed_tools(spec: dict[str, Any]) -> str:
    """`scenario.json` may list `tools`; default is Bash only (the M1/M2
    e2e default). unbacked-check needs Read and Edit."""
    tools = spec.get("tools", ["Bash"])
    return ",".join(str(t) for t in tools)


def _run(spec: dict[str, Any], verdict_home: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["VERDICT_HOME"] = str(verdict_home)
    env["CLAUDE_PLUGIN_OPTION_MODE"] = str(spec["mode"])
    env["CLAUDE_PLUGIN_OPTION_PROVIDER"] = "openrouter"

    def runner(model: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            e2e._claude_command(model, str(spec["prompt"]), MAX_TURNS, _allowed_tools(spec)),
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=TIMEOUT_S,
            text=True,
            check=False,
        )

    return e2e._run_with_fallback(runner, e2e.DEFAULT_MODEL)


def _check(
    spec: dict[str, Any], events: list[dict[str, Any]], home: Path, rows: list[dict[str, Any]]
) -> None:
    e2e.assert_plugin_loaded_cleanly(events)
    e2e.assert_our_stop_hooks_ran_clean(home, rows)
    actions = [r for r in rows if r.get("event") == "action"]
    if not actions:
        raise e2e.AssertionFailure("no action row")
    last = actions[-1]
    got = last.get("would_have") or last.get("action")
    expect = spec["expect"]
    if got != expect["action"] or last.get("rule_id") != expect["rule_id"]:
        raise e2e.AssertionFailure(
            f"expected {expect['action']}/{expect['rule_id']}, got {got}/{last.get('rule_id')} "
            f"(gate_reason={last.get('gate_reason')})"
        )


def record(name: str) -> int:
    spec = load_scenario(name)
    with (
        tempfile.TemporaryDirectory(prefix="verdict-site-home-") as home_dir,
        tempfile.TemporaryDirectory(prefix="verdict-site-cwd-") as cwd_dir,
    ):
        verdict_home, cwd = Path(home_dir), Path(cwd_dir)
        shutil.copytree(SCENARIOS / name / "repo", cwd, dirs_exist_ok=True)
        os.environ["VERDICT_HOME"] = str(verdict_home)
        result = _run(spec, verdict_home, cwd)
        events = e2e.parse_stream(result.stdout)
        rows = e2e.load_ledger_rows(verdict_home)
        try:
            _check(spec, events, verdict_home, rows)
        except e2e.AssertionFailure as exc:
            print(f"record {name} FAILED: {exc}", file=sys.stderr)
            for row in rows:
                print({k: row.get(k) for k in _SUMMARY_KEYS if k in row}, file=sys.stderr)
            return 1
        copy_ledger(verdict_home, SCENARIOS / name)
        recording = {
            "recorded_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "claude_code_version": _claude_version(),
            "model": e2e.DEFAULT_MODEL,
            "mode": spec["mode"],
        }
        recording_text = json.dumps(recording, indent=2) + "\n"
        (SCENARIOS / name / "recording.json").write_text(recording_text, encoding="utf-8")
    print(f"record {name} OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="record demo-site scenarios (billed)")
    parser.add_argument(
        "--scenario", action="append", help="scenario name; repeatable; default all"
    )
    args = parser.parse_args(argv)
    if not os.environ.get(e2e._API_KEY_ENV, "").strip():
        print(
            f"REFUSED: {e2e._API_KEY_ENV} is not set; this script bills a real provider.",
            file=sys.stderr,
        )
        return e2e._REFUSE_NO_KEY_EXIT
    names = args.scenario or sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir())
    return max(record(n) for n in names)


if __name__ == "__main__":
    sys.exit(main())
