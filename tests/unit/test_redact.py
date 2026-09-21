from __future__ import annotations

import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from verdict_hot import redact

ROOT = Path(__file__).resolve().parents[2]


def test_local_env_secret_rule_catches_its_case() -> None:
    text = "export DEPLOY_SECRET_TOKEN=abcdEFGH12345678\nexport NODE_ENV=production"
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert "abcdEFGH12345678" not in cleaned
    assert "REDACTED" in cleaned


def test_local_high_entropy_rule_catches_its_case() -> None:
    secret = "R7q9zP2xL4vK8mN1sT6wY3bC5dF0gH2jQ4rU9tA1eZ8xW6=="
    text = f"X-Deploy-Signature: {secret}\n"
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert secret not in cleaned


def test_local_url_credential_rule_catches_its_case() -> None:
    text = "DATABASE_URL=postgres://appuser:Sup3rSecretPW9@db.internal.example.com:5432/appdb"
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert "Sup3rSecretPW9" not in cleaned
    # the non-secret parts of the URL survive
    assert "appuser" in cleaned
    assert "db.internal.example.com" in cleaned


def test_ordinary_sentence_yields_zero_hits() -> None:
    text = "The build completed without warnings and all tests passed."
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_redact_is_idempotent() -> None:
    text = "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstT3BlbkFJabcdefghijklmnopqrst"
    once, hits1 = redact.redact(text)
    twice, hits2 = redact.redact(once)
    assert once == twice
    assert hits1 >= 1
    assert hits2 == 0


def test_redact_never_raises_on_malformed_bytes_text() -> None:
    text = "\x00\x01 not \ud800 valid \x1b[ partial ansi"
    cleaned, hits = redact.redact(text)
    assert isinstance(cleaned, str)
    assert isinstance(hits, int)


@given(st.text(max_size=2000))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_redact_never_raises_and_output_is_bounded(text: str) -> None:
    cleaned, hits = redact.redact(text)
    assert len(cleaned) <= len(text) + 40 * hits


@pytest.mark.slow
def test_redact_performance_on_8k_build_log_under_15ms_median() -> None:
    rng = random.Random(1234)
    lines = []
    for i in range(140):
        lines.append(
            f"[{i:04d}] Compiling module_{i}.ts ... ok "
            f"(checksum {''.join(rng.choice('0123456789abcdef') for _ in range(12))})"
        )
    text = "\n".join(lines)
    text = text[:8192].ljust(8192, "x")

    redact.redact(text)  # warm the pattern cache

    samples = []
    for _ in range(20):
        start = time.perf_counter()
        redact.redact(text)
        samples.append(time.perf_counter() - start)
    median_ms = statistics.median(samples) * 1000
    assert median_ms < 15, f"median redact() time {median_ms:.2f}ms >= 15ms"


@pytest.mark.slow
def test_import_verdict_hot_redact_under_25ms() -> None:
    script = (
        "import sys; sys.path.insert(0, 'plugin/hooks'); "
        "import time; t0 = time.perf_counter(); "
        "import verdict_hot.redact; "
        "print((time.perf_counter() - t0) * 1000)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    elapsed_ms = float(proc.stdout.strip().splitlines()[-1])
    assert elapsed_ms < 25, f"import verdict_hot.redact took {elapsed_ms:.2f}ms >= 25ms"
