"""Record real System One responses for the five golden states into
`tests/fixtures/cassettes/<sha256>.json` (task-3-brief.md).

CONTROLLER-RUN ONLY: this script makes a real network call with a real
`OPENROUTER_API_KEY` and is never run by a worker. It refuses outright when
that key is not set, and never prints it -- only per-golden latency and
`input_tokens` reach stdout/stderr.

The cassette filename is the sha256 hex digest of
`verdict_hot.provider.canonical_request_body(model, state, questions)`, the
exact same canonical form `RecordedTransport` hashes to look one up, so a
cassette recorded here is found by `evaluate(..., transport=
RecordedTransport(cassette_dir))` without either side needing a second copy
of the recipe. The cassette keeps only `answers`, `model` (as
`model_returned`), and `usage` from the response body -- everything else
the provider might have sent is dropped before it ever touches disk.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "hooks"))

from verdict_hot import provider, sslctx  # noqa: E402

GOLDEN_STATES = ROOT / "tests" / "golden" / "states"
GOLDEN_QUESTIONS = ROOT / "tests" / "golden" / "questions"
CASSETTE_DIR = ROOT / "tests" / "fixtures" / "cassettes"
PRESET = provider.PRESETS["openrouter"]
REQUEST_TIMEOUT_S = 15.0


def _golden_names() -> list[str]:
    return sorted(p.stem for p in GOLDEN_STATES.glob("*.json"))


def _load_golden(name: str) -> tuple[Any, Any]:
    state = json.loads((GOLDEN_STATES / f"{name}.json").read_text(encoding="utf-8"))
    questions = json.loads((GOLDEN_QUESTIONS / f"{name}.json").read_text(encoding="utf-8"))
    return state, questions


def _call_real_endpoint(body: bytes, api_key: str) -> tuple[int, bytes, float]:
    """Return (status, raw_body, elapsed_ms). Never logs `api_key`."""
    context = sslctx.build_context()
    conn = http.client.HTTPSConnection(PRESET.host, timeout=REQUEST_TIMEOUT_S, context=context)
    start = time.perf_counter()
    try:
        conn.request(
            "POST",
            PRESET.path,
            body=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        raw = response.read()
    finally:
        conn.close()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return response.status, raw, elapsed_ms


def _write_cassette(name: str, sha: str, parsed: dict[str, Any]) -> Path:
    cassette = {
        "name": name,
        "request_sha256": sha,
        "model_requested": PRESET.model,
        "model_returned": parsed.get("model"),
        "provider": PRESET.name,
        "recorded": dt.date.today().isoformat(),
        "answers": parsed.get("answers"),
        "usage": parsed.get("usage"),
    }
    target = CASSETTE_DIR / f"{sha}.json"
    target.write_text(json.dumps(cassette, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return target


def _record_one(name: str, api_key: str) -> bool:
    state, questions = _load_golden(name)
    body = provider.canonical_request_body(PRESET.model, state, questions)
    import hashlib

    sha = hashlib.sha256(body).hexdigest()

    status, raw, elapsed_ms = _call_real_endpoint(body, api_key)
    if status != 200:
        print(f"{name}: FAILED http_{status}", file=sys.stderr)
        return False

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        print(f"{name}: FAILED invalid_json", file=sys.stderr)
        return False
    if not isinstance(parsed, dict) or not isinstance(parsed.get("answers"), dict):
        print(f"{name}: FAILED invalid_answers", file=sys.stderr)
        return False

    target = _write_cassette(name, sha, parsed)
    usage = parsed.get("usage")
    input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    print(f"{name}: wrote {target.name} ({elapsed_ms:.0f} ms, input_tokens={input_tokens})")
    return True


def main() -> int:
    api_key = os.environ.get(PRESET.key_env, "")
    if not api_key:
        print(f"refusing to run: {PRESET.key_env} is not set", file=sys.stderr)
        return 2

    names = _golden_names()
    if not names:
        print(f"no golden states found under {GOLDEN_STATES}", file=sys.stderr)
        return 2

    CASSETTE_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in names:
        ok = _record_one(name, api_key) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
