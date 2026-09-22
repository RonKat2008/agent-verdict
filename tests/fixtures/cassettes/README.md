Cassettes here are keyed by the sha256 of the canonical request body and were recorded on 2026-09-22 against `typesafe/jev-1.13-20260917`; fix round 2's token-estimate and wording changes altered the request bodies, so some of these no longer match by hash (do not delete, edit, or re-record them here).

Task 3's matching scheme (`verdict_hot.provider`): the canonical body is
`canonical_request_body(model, state, questions)`, i.e.
`json.dumps({"model": model, "state": state, "questions": questions}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`;
the cassette filename is that body's sha256 hex digest plus `.json`.
`RecordedTransport(cassette_dir)` hashes the outgoing request body the same
way and raises `MissingCassette(sha)` when nothing matches;
`evaluate(..., transport=RecordedTransport(cassette_dir))` lets that
exception propagate rather than turning it into a `ProviderResult`, and
`tests/unit/test_provider.py` converts it into `pytest.skip(...)` per
golden state.

In practice four of the five committed cassettes (`no_failures`,
`over_budget`, `soft_fail`, `unresolved_failure`) still match their current
golden state and question files byte-for-byte, since the D-031 token-budget
fix only changes serialized output for a state large enough to hit the hard
cap; only `overflow` (the fixture built specifically to exercise that hard
cap) currently misses and skips. `scripts/record_cassettes.py`
(controller-run only, needs `OPENROUTER_API_KEY`) re-records whichever
cassettes drift out of sync, using this exact same canonical form.
