import http.client
import json
import time
from collections.abc import Mapping

import pytest
import smoke_jev as sj


def test_percentile_uses_nearest_rank() -> None:
    assert sj.percentile([10.0, 20.0, 30.0, 40.0], 50) == 20.0
    assert sj.percentile([10.0, 20.0, 30.0, 40.0], 90) == 40.0
    assert sj.percentile([5.0], 99) == 5.0


def test_percentile_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        sj.percentile([], 50)


def test_payload_has_pinned_model_requested_questions_and_provenance_sections() -> None:
    payload = sj.build_payload("jev-1.13.0", state_tokens=1200, n_questions=6)
    assert payload["model"] == "jev-1.13.0"
    questions = payload["questions"]
    assert isinstance(questions, dict) and len(questions) == 6
    assert {q["type"] for q in questions.values()} == {"noul", "score"}
    state = payload["state"]
    assert isinstance(state, dict) and set(state) == {"trusted_facts", "untrusted"}


def test_payload_state_size_is_close_to_requested_tokens() -> None:
    payload = sj.build_payload("jev-1.13.0", state_tokens=1200, n_questions=6)
    approx_tokens = len(json.dumps(payload["state"])) / 4
    assert 1000 <= approx_tokens <= 1500


def test_providers_use_pinned_ids_never_aliases() -> None:
    assert sj.PROVIDERS["openrouter"].model == "typesafe/jev-1.13-20260917"
    assert sj.PROVIDERS["typesafe"].model == "jev-1.13.0"
    assert all("latest" not in p.model for p in sj.PROVIDERS.values())


def test_validate_answers_accepts_typed_answers() -> None:
    questions = {"a": {"type": "noul"}, "b": {"type": "score"}}
    body = {"answers": {"a": {"type": "noul", "noul": 0.9}, "b": {"type": "score", "score": 2.5}}}
    sj.validate_answers(body, questions)


@pytest.mark.parametrize(
    "answers",
    [{}, {"a": {"type": "noul", "noul": 1.7}}, {"a": {"type": "choice", "choice": "x"}}],
)
def test_validate_answers_rejects_missing_out_of_range_or_wrong_type(
    answers: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        sj.validate_answers({"answers": answers}, {"a": {"type": "noul"}})


def test_pick_ca_bundle_returns_first_existing_candidate() -> None:
    assert sj.pick_ca_bundle(["/a", "/b", "/c"], lambda p: p in {"/b", "/c"}) == "/b"
    assert sj.pick_ca_bundle(["/a"], lambda p: False) is None


def test_summarize_reports_percentiles_and_distinct_models() -> None:
    samples = [
        sj.Sample(
            conn_ms=10.0 * i,
            infer_ms=100.0 * i,
            total_ms=110.0 * i,
            status=200,
            model_returned="m",
            input_tokens=1200,
            error=None,
        )
        for i in range(1, 11)
    ]
    summary = sj.summarize(samples)
    assert summary["n_ok"] == 10 and summary["models_returned"] == ["m"]
    assert summary["total_ms"] == {"p50": 550.0, "p90": 990.0, "p99": 1100.0}


def test_main_returns_2_when_no_provider_has_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert sj.main(["--n", "1", "--provider", "openrouter", "--provider", "typesafe"]) == 2


def _valid_answers_body(payload: Mapping[str, object]) -> dict[str, object]:
    questions = payload["questions"]
    assert isinstance(questions, dict)
    answers: dict[str, object] = {}
    for key, question in questions.items():
        qtype = question["type"]
        value = 0.5 if qtype == "noul" else 2.0
        answers[key] = {"type": qtype, qtype: value}
    return {"answers": answers, "model": payload["model"]}


def test_sample_from_body_rejects_json_array_body() -> None:
    payload = sj.build_payload("m", state_tokens=50, n_questions=1)
    sample = sj._sample_from_body(b"[1, 2, 3]", payload, 1.0, 2.0)
    assert sample.status == 200
    assert sample.error is not None and sample.error.startswith("invalid")


def test_sample_from_body_rejects_json_string_body() -> None:
    payload = sj.build_payload("m", state_tokens=50, n_questions=1)
    sample = sj._sample_from_body(b'"just a string"', payload, 1.0, 2.0)
    assert sample.status == 200
    assert sample.error is not None and sample.error.startswith("invalid")


def test_sample_from_body_rejects_non_json_body() -> None:
    payload = sj.build_payload("m", state_tokens=50, n_questions=1)
    sample = sj._sample_from_body(b"not json at all", payload, 1.0, 2.0)
    assert sample.status == 200
    assert sample.error is not None and sample.error.startswith("invalid")


def test_sample_from_body_uses_fixed_message_for_invalid_json_never_response_content() -> None:
    payload = sj.build_payload("m", state_tokens=50, n_questions=1)
    raw = b"this is definitely not json and contains a SECRET_TOKEN_VALUE fragment"
    sample = sj._sample_from_body(raw, payload, 1.0, 2.0)
    assert sample.error == "invalid: JSONDecodeError"
    assert "SECRET_TOKEN_VALUE" not in (sample.error or "")


def test_sample_from_body_rejects_dict_with_non_dict_usage() -> None:
    payload = sj.build_payload("m", state_tokens=50, n_questions=1)
    body = _valid_answers_body(payload)
    body["usage"] = ["not", "an", "object"]
    sample = sj._sample_from_body(json.dumps(body).encode(), payload, 1.0, 2.0)
    assert sample.status == 200
    assert sample.error is not None and sample.error.startswith("invalid")


def _summary(
    n: int,
    n_ok: int,
    models_returned: list[str],
    p90: float | None = None,
) -> dict[str, object]:
    summary: dict[str, object] = {"n": n, "n_ok": n_ok, "models_returned": models_returned}
    if p90 is not None:
        summary["total_ms"] = {"p50": p90, "p90": p90, "p99": p90}
    return summary


def test_gate_fails_when_almost_every_call_errored() -> None:
    summary = _summary(n=30, n_ok=1, models_returned=["jev-1.13.0"], p90=5.0)
    ok, reason = sj.gate_passes(summary, "jev-1.13.0", max_p90_ms=1200.0)
    assert ok is False
    assert "ratio" in reason


def test_gate_fails_when_two_distinct_models_are_returned() -> None:
    summary = _summary(n=2, n_ok=2, models_returned=["model-a", "model-b"], p90=5.0)
    ok, reason = sj.gate_passes(summary, "jev-1.13.0", max_p90_ms=1200.0)
    assert ok is False
    assert "model" in reason


def test_gate_fails_when_returned_model_id_does_not_match_requested() -> None:
    summary = _summary(n=1, n_ok=1, models_returned=["totally-different-id"], p90=5.0)
    ok, reason = sj.gate_passes(summary, "jev-1.13.0", max_p90_ms=1200.0)
    assert ok is False
    assert "match" in reason


def test_gate_passes_for_thirty_fast_successes_with_the_pinned_id() -> None:
    summary = _summary(n=30, n_ok=30, models_returned=["jev-1.13.0"], p90=8.0)
    ok, reason = sj.gate_passes(summary, "jev-1.13.0", max_p90_ms=1200.0)
    assert ok is True, reason


def test_gate_accepts_openrouter_canonical_slug_as_a_substring_match() -> None:
    summary = _summary(n=30, n_ok=30, models_returned=["jev-1.13-20260917"], p90=8.0)
    ok, reason = sj.gate_passes(summary, "typesafe/jev-1.13-20260917", max_p90_ms=1200.0)
    assert ok is True, reason


def test_gate_fails_when_p90_exceeds_the_max() -> None:
    summary = _summary(n=30, n_ok=30, models_returned=["jev-1.13.0"], p90=5000.0)
    ok, reason = sj.gate_passes(summary, "jev-1.13.0", max_p90_ms=1200.0)
    assert ok is False
    assert "p90" in reason


def test_gate_respects_a_custom_min_ok_ratio() -> None:
    summary = _summary(n=10, n_ok=9, models_returned=["jev-1.13.0"], p90=8.0)
    ok, _ = sj.gate_passes(summary, "jev-1.13.0", max_p90_ms=1200.0, min_ok_ratio=0.95)
    assert ok is False
    ok, _ = sj.gate_passes(summary, "jev-1.13.0", max_p90_ms=1200.0, min_ok_ratio=0.85)
    assert ok is True


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeHTTPSConnection:
    def __init__(self, host: str, timeout: float | None = None, context: object = None) -> None:
        del host, timeout, context

    def connect(self) -> None:
        pass

    def request(
        self, method: str, path: str, body: bytes | None = None, headers: object = None
    ) -> None:
        del method, path, body, headers

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(200, b"not valid json")

    def close(self) -> None:
        pass


def test_conn_ms_excludes_payload_serialization_time(monkeypatch: pytest.MonkeyPatch) -> None:
    real_dumps = json.dumps

    def slow_dumps(*args: object, **kwargs: object) -> str:
        time.sleep(0.05)
        return real_dumps(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(json, "dumps", slow_dumps)
    monkeypatch.setattr(http.client, "HTTPSConnection", _FakeHTTPSConnection)
    provider = sj.PROVIDERS["typesafe"]
    payload = sj.build_payload(provider.model, state_tokens=50, n_questions=1)
    context = sj.build_ssl_context()
    sample = sj.call_once(provider, payload, "fake-key", 10.0, context)
    assert sample.conn_ms < 25.0
