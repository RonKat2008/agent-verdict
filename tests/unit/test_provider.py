"""Tests for verdict_hot.provider (task-3-brief.md, D-011, C1, C3, C5, C7).

No test in this file ever opens a socket: every call to `evaluate` either
supplies a fake `transport` or relies on `evaluate`'s own pre-flight checks
(`no_key`, `breaker_open`, an already-exhausted deadline) short-circuiting
before a transport would be needed at all.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

import pytest
from verdict_hot import provider

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_STATES = ROOT / "tests" / "golden" / "states"
GOLDEN_QUESTIONS = ROOT / "tests" / "golden" / "questions"
CASSETTE_DIR = ROOT / "tests" / "fixtures" / "cassettes"
GOLDEN_NAMES = ["no_failures", "over_budget", "overflow", "soft_fail", "unresolved_failure"]

_NOUL_QUESTION: dict[str, object] = {
    "type": "noul",
    "instructions": "Did it happen?",
    "criteria": {"true": "yes", "false": "no"},
}
_SCORE_QUESTION: dict[str, object] = {
    "type": "score",
    "instructions": "Rate it.",
    "criteria": ["low", "high"],
}
_QUESTIONS: dict[str, object] = {"claims_done": _NOUL_QUESTION, "completion": _SCORE_QUESTION}
_VALID_ANSWERS: dict[str, object] = {
    "claims_done": {"type": "noul", "noul": 0.75},
    "completion": {
        "type": "score",
        "score": 1.5,
        "legend": {},
        "probabilities": {},
        "confidence": 0.9,
    },
}


def _response_body(answers: dict[str, object], model: str = "typesafe/jev-1.13-20260917") -> bytes:
    return json.dumps(
        {"model": model, "answers": answers, "usage": {"input_tokens": 42, "output_tokens": 7}}
    ).encode("utf-8")


class _Recorder:
    """A fake transport that records every call and returns a fixed response."""

    def __init__(
        self, status: int, body: bytes, conn_ms: float = 1.0, infer_ms: float = 2.0
    ) -> None:
        self.status = status
        self.body = body
        self.conn_ms = conn_ms
        self.infer_ms = infer_ms
        self.calls: list[tuple[str, str, bytes, dict[str, str]]] = []

    def __call__(
        self, host: str, path: str, body_bytes: bytes, headers: Any
    ) -> tuple[int, bytes, float, float]:
        self.calls.append((host, path, body_bytes, dict(headers)))
        return self.status, self.body, self.conn_ms, self.infer_ms


# --- Presets -------------------------------------------------------------


def test_presets_match_d011_exactly() -> None:
    assert provider.PRESETS["openrouter"] == provider.Preset(
        name="openrouter",
        host="openrouter.ai",
        path="/api/v1/systemone",
        key_env="OPENROUTER_API_KEY",
        model="typesafe/jev-1.13-20260917",
    )
    assert provider.PRESETS["typesafe"] == provider.Preset(
        name="typesafe",
        host="api.typesafe.ai",
        path="/v1/systemone",
        key_env="TYPESAFE_API_KEY",
        model="jev-1.13.0",
    )


# --- Canonical request body ------------------------------------------------


def test_canonical_request_body_is_sorted_compact_and_ensure_ascii_false() -> None:
    body = provider.canonical_request_body("m", {"b": 1, "a": "café"}, {"q1": {"type": "noul"}})
    text = body.decode("utf-8")
    assert text == json.dumps(
        {"model": "m", "state": {"b": 1, "a": "café"}, "questions": {"q1": {"type": "noul"}}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert "café" in text
    assert "\\u00e9" not in text


# --- Request shape and headers ---------------------------------------------


def test_evaluate_sends_bearer_header_and_key_is_absent_from_the_body(
    isolated_verdict_home: Path,
) -> None:
    preset = provider.PRESETS["openrouter"]
    api_key = "sk-real-secret-abcdefg"
    recorder = _Recorder(200, _response_body(_VALID_ANSWERS))

    result = provider.evaluate(
        {"trusted_facts": {}}, _QUESTIONS, preset, api_key, 5.0, transport=recorder
    )

    assert result.ok is True
    assert len(recorder.calls) == 1
    host, path, body_bytes, headers = recorder.calls[0]
    assert host == preset.host
    assert path == preset.path
    assert headers["Authorization"] == f"Bearer {api_key}"
    assert api_key not in body_bytes.decode("utf-8")


def test_evaluate_sends_state_unchanged_and_utf8_ensure_ascii_false(
    isolated_verdict_home: Path,
) -> None:
    preset = provider.PRESETS["openrouter"]
    state = {"untrusted": {"final_message": "déjà vu — café"}}
    recorder = _Recorder(200, _response_body(_VALID_ANSWERS))

    provider.evaluate(state, _QUESTIONS, preset, "key", 5.0, transport=recorder)

    _, _, body_bytes, _ = recorder.calls[0]
    text = body_bytes.decode("utf-8")
    sent = json.loads(text)
    assert sent["state"] == state
    assert sent["questions"] == _QUESTIONS
    assert sent["model"] == preset.model
    assert "café" in text  # ensure_ascii=False: never \uXXXX-escaped
    assert "\\u" not in text


# --- Answer validation -------------------------------------------------


def test_evaluate_ok_with_valid_answers(isolated_verdict_home: Path) -> None:
    preset = provider.PRESETS["openrouter"]
    recorder = _Recorder(200, _response_body(_VALID_ANSWERS))

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 5.0, transport=recorder)

    assert result.ok is True
    assert result.error is None
    assert result.answers == _VALID_ANSWERS
    assert result.model_returned == "typesafe/jev-1.13-20260917"
    assert result.input_tokens == 42
    assert result.status == 200


def test_evaluate_ignores_extra_answer_keys(isolated_verdict_home: Path) -> None:
    preset = provider.PRESETS["openrouter"]
    answers = {**_VALID_ANSWERS, "unexpected_extra": {"type": "noul", "noul": 0.1}}
    recorder = _Recorder(200, _response_body(answers))

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 5.0, transport=recorder)

    assert result.ok is True


@pytest.mark.parametrize(
    "bad_answers",
    [
        {"completion": _VALID_ANSWERS["completion"]},  # claims_done missing entirely
        {**_VALID_ANSWERS, "claims_done": {"type": "noul", "noul": 1.5}},  # out of range high
        {**_VALID_ANSWERS, "claims_done": {"type": "noul", "noul": -0.1}},  # out of range low
        {**_VALID_ANSWERS, "claims_done": {"type": "noul", "noul": "0.5"}},  # wrong value type
        {**_VALID_ANSWERS, "completion": {"type": "score", "score": "high"}},  # non-numeric score
        {**_VALID_ANSWERS, "claims_done": {"type": "score", "noul": 0.5}},  # type mismatch
    ],
)
def test_evaluate_rejects_missing_or_out_of_range_answers(
    bad_answers: dict[str, object], isolated_verdict_home: Path
) -> None:
    preset = provider.PRESETS["openrouter"]
    recorder = _Recorder(200, _response_body(bad_answers))

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 5.0, transport=recorder)

    assert result.ok is False
    assert result.error == "invalid_answers"
    assert result.answers == {}


def test_evaluate_invalid_json_body(isolated_verdict_home: Path) -> None:
    preset = provider.PRESETS["openrouter"]
    recorder = _Recorder(200, b"not json at all {")

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 5.0, transport=recorder)

    assert result.ok is False
    assert result.error == "invalid_json"


# --- Non-200 and timeout paths -------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_evaluate_non_200_returns_fixed_http_error_string(
    status: int, isolated_verdict_home: Path
) -> None:
    preset = provider.PRESETS["openrouter"]
    recorder = _Recorder(status, b"irrelevant body content that must never leak")

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 5.0, transport=recorder)

    assert result.ok is False
    assert result.error == f"http_{status}"
    assert result.status == status
    assert "irrelevant body" not in (result.error or "")


def test_evaluate_deadline_already_exhausted_never_calls_transport(
    isolated_verdict_home: Path,
) -> None:
    preset = provider.PRESETS["openrouter"]

    def _should_not_be_called(*args: object) -> tuple[int, bytes, float, float]:
        raise AssertionError("transport must not be called when the deadline is exhausted")

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 0.0, transport=_should_not_be_called)

    assert result.ok is False
    assert result.error == "timeout"


def test_evaluate_respects_deadline_against_a_slow_fake_transport(
    isolated_verdict_home: Path,
) -> None:
    preset = provider.PRESETS["openrouter"]

    def _slow_transport(*args: object) -> tuple[int, bytes, float, float]:
        time.sleep(0.15)
        return 200, _response_body(_VALID_ANSWERS), 1.0, 1.0

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 0.05, transport=_slow_transport)

    assert result.ok is False
    assert result.error == "timeout"


# --- no_key / breaker_open pre-flight checks --------------------------------


def test_evaluate_no_key_never_calls_transport(isolated_verdict_home: Path) -> None:
    preset = provider.PRESETS["openrouter"]

    def _should_not_be_called(*args: object) -> tuple[int, bytes, float, float]:
        raise AssertionError("transport must not be called with no api key")

    result = provider.evaluate({}, _QUESTIONS, preset, "", 5.0, transport=_should_not_be_called)

    assert result.ok is False
    assert result.error == "no_key"


def test_evaluate_breaker_open_never_calls_transport(isolated_verdict_home: Path) -> None:
    preset = provider.PRESETS["openrouter"]
    now = time.time()
    provider.Breaker().record(429, now)
    provider.Breaker().record(429, now)
    provider.Breaker().record(429, now)

    def _should_not_be_called(*args: object) -> tuple[int, bytes, float, float]:
        raise AssertionError("transport must not be called while the breaker is open")

    result = provider.evaluate({}, _QUESTIONS, preset, "key", 5.0, transport=_should_not_be_called)

    assert result.ok is False
    assert result.error == "breaker_open"


# --- Security: the key never leaks through an exception ---------------------


def test_evaluate_never_leaks_the_key_when_the_transport_raises(
    isolated_verdict_home: Path,
) -> None:
    preset = provider.PRESETS["openrouter"]
    sentinel_key = "sk-sentinel-should-never-appear-anywhere-observable"

    def _raising_transport(*args: object) -> tuple[int, bytes, float, float]:
        raise RuntimeError(f"connection failed while using key {sentinel_key}")

    result = provider.evaluate(
        {}, _QUESTIONS, preset, sentinel_key, 5.0, transport=_raising_transport
    )

    assert result.ok is False
    assert result.error == "exception:RuntimeError"
    assert sentinel_key not in (result.error or "")
    assert sentinel_key not in repr(result)
    assert sentinel_key not in str(result)


# --- Breaker ---------------------------------------------------------------


def test_breaker_opens_after_three_consecutive_429s(tmp_path: Path) -> None:
    breaker = provider.Breaker(tmp_path / "breaker.json")
    now = 1_700_000_000.0

    assert breaker.is_open(now) is False
    breaker.record(429, now)
    assert breaker.is_open(now) is False
    breaker.record(429, now)
    assert breaker.is_open(now) is False
    breaker.record(429, now)
    assert breaker.is_open(now) is True


def test_breaker_closes_after_expiry(tmp_path: Path) -> None:
    breaker = provider.Breaker(tmp_path / "breaker.json")
    now = 1_700_000_000.0
    for _ in range(3):
        breaker.record(429, now)
    assert breaker.is_open(now + 599) is True
    assert breaker.is_open(now + 601) is False


def test_breaker_a_200_resets_the_consecutive_count(tmp_path: Path) -> None:
    breaker = provider.Breaker(tmp_path / "breaker.json")
    now = 1_700_000_000.0
    breaker.record(429, now)
    breaker.record(429, now)
    breaker.record(200, now)
    breaker.record(429, now)
    breaker.record(429, now)
    assert breaker.is_open(now) is False  # only 2 consecutive since the reset


def test_breaker_state_file_is_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "breaker.json"
    provider.Breaker(path).record(429, 1_700_000_000.0)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_breaker_never_raises_on_a_corrupt_state_file(tmp_path: Path) -> None:
    path = tmp_path / "breaker.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    breaker = provider.Breaker(path)

    assert breaker.is_open(1_700_000_000.0) is False
    breaker.record(429, 1_700_000_000.0)  # must not raise


def test_breaker_never_raises_when_the_directory_is_unwritable(tmp_path: Path) -> None:
    missing_parent = tmp_path / "does" / "not" / "exist"
    breaker = provider.Breaker(missing_parent / "breaker.json")
    os.chmod(tmp_path, 0o500)
    try:
        assert breaker.is_open(1_700_000_000.0) is False
        breaker.record(429, 1_700_000_000.0)  # must not raise
    finally:
        os.chmod(tmp_path, 0o700)


# --- RecordedTransport / cassette matching ----------------------------------


def test_recorded_transport_round_trip(tmp_path: Path) -> None:
    preset = provider.PRESETS["openrouter"]
    body = provider.canonical_request_body(preset.model, {"s": 1}, _QUESTIONS)
    import hashlib

    sha = hashlib.sha256(body).hexdigest()
    cassette = {
        "name": "synthetic",
        "request_sha256": sha,
        "model_requested": preset.model,
        "model_returned": preset.model,
        "provider": "openrouter",
        "recorded": "2026-09-22",
        "answers": _VALID_ANSWERS,
        "usage": {"input_tokens": 10, "output_tokens": 3, "cost": 1e-6},
    }
    (tmp_path / f"{sha}.json").write_text(json.dumps(cassette), encoding="utf-8")
    transport = provider.RecordedTransport(tmp_path)

    status, raw, conn_ms, infer_ms = transport(
        preset.host, preset.path, body, {"Authorization": "Bearer x"}
    )

    assert status == 200
    parsed = json.loads(raw)
    assert parsed["answers"] == _VALID_ANSWERS
    assert parsed["model"] == preset.model
    assert conn_ms == 0.0
    assert infer_ms == 0.0


def test_recorded_transport_raises_missing_cassette(tmp_path: Path) -> None:
    transport = provider.RecordedTransport(tmp_path)
    with pytest.raises(provider.MissingCassette) as exc_info:
        transport("host", "/path", b"no such body", {})
    assert len(exc_info.value.sha) == 64


def test_evaluate_via_recorded_transport_round_trip(
    tmp_path: Path, isolated_verdict_home: Path
) -> None:
    preset = provider.PRESETS["openrouter"]
    state = {"s": 1}
    body = provider.canonical_request_body(preset.model, state, _QUESTIONS)
    import hashlib

    sha = hashlib.sha256(body).hexdigest()
    cassette = {
        "name": "synthetic",
        "request_sha256": sha,
        "model_requested": preset.model,
        "model_returned": preset.model,
        "provider": "openrouter",
        "recorded": "2026-09-22",
        "answers": _VALID_ANSWERS,
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }
    (tmp_path / f"{sha}.json").write_text(json.dumps(cassette), encoding="utf-8")

    result = provider.evaluate(
        state, _QUESTIONS, preset, "key", 5.0, transport=provider.RecordedTransport(tmp_path)
    )

    assert result.ok is True
    assert result.answers == _VALID_ANSWERS


# --- The five real cassettes: expected to miss by hash (see README) --------


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_real_cassettes_skip_cleanly_when_the_hash_no_longer_matches(
    name: str, isolated_verdict_home: Path
) -> None:
    """The five committed cassettes were recorded before fix round 2 changed
    state/question wording (tests/fixtures/cassettes/README.md), so none of
    them match the current golden states by hash. That is expected: this
    proves the mismatch is detected and skipped, not silently ignored or
    treated as a provider failure, and the controller will re-record after
    this task lands."""
    state = json.loads((GOLDEN_STATES / f"{name}.json").read_text(encoding="utf-8"))
    questions = json.loads((GOLDEN_QUESTIONS / f"{name}.json").read_text(encoding="utf-8"))
    preset = provider.PRESETS["openrouter"]

    try:
        result = provider.evaluate(
            state, questions, preset, "key", 5.0, transport=provider.RecordedTransport(CASSETTE_DIR)
        )
    except provider.MissingCassette as exc:
        pytest.skip(
            f"no cassette matches golden {name!r} by sha256={exc.sha} "
            "(expected: fix round 2 changed the request wording; "
            "the controller will re-record after this task lands)"
        )
    else:
        # If a future re-recording makes this match, it must still validate.
        assert result.ok is True
