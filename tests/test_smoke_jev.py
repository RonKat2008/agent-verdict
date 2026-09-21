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
    import json

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
