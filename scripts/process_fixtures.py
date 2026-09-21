"""Turn raw captured hook payloads into sanitized, named test fixtures with provenance."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "tests/fixtures/hooks/raw"
OUT_DIR = ROOT / "tests/fixtures/hooks"
MAX_STRING = 2000
KEEP = 900


def sanitize(value: object, home: str) -> object:
    if isinstance(value, str):
        text = value.replace(home, "/Users/USER")
        if len(text) > MAX_STRING:
            cut = len(text) - 2 * KEEP
            marker = f"... [{cut} characters truncated by process_fixtures] ..."
            text = f"{text[:KEEP]}{marker}{text[-KEEP:]}"
        return text
    if isinstance(value, Mapping):
        return {key: sanitize(item, home) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, home) for item in value]
    return value


def fixture_name(payload: Mapping[str, object]) -> str:
    event = str(payload.get("hook_event_name", "unknown"))
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
    tool = payload.get("tool_name")
    return f"{snake}_{str(tool).lower()}" if tool else snake


def claude_version() -> str:
    result = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
    return result.stdout.strip() or "unknown"


def main() -> int:
    home = str(Path.home())
    written: dict[str, Path] = {}
    for raw_path in sorted(RAW_DIR.glob("*.json")):
        try:
            payload = json.loads(raw_path.read_text())
        except json.JSONDecodeError:
            print(f"skipping unparseable payload {raw_path.name}")
            continue
        name = fixture_name(payload)
        if name in written:
            continue
        out_path = OUT_DIR / f"{name}.json"
        out_path.write_text(json.dumps(sanitize(payload, home), indent=1, sort_keys=True) + "\n")
        written[name] = out_path
    captured_line = (
        f"Captured {dt.date.today().isoformat()} with `{claude_version()}` by "
        "`make capture-fixtures`."
    )
    lines = [
        "# Fixture provenance",
        "",
        captured_line,
        "Payloads are real hook stdin, sanitized by `scripts/process_fixtures.py`.",
        "",
        *[f"- `{path.name}`" for path in written.values()],
    ]
    (OUT_DIR / "PROVENANCE.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {len(written)} fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
