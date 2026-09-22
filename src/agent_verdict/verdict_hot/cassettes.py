"""Recorded provider responses for offline tests (split from provider.py).

A cassette is `tests/fixtures/cassettes/<sha256>.json`, keyed by the sha256
of the canonical request body (`provider.canonical_request_body`). It holds
only `answers`, `usage`, `model_returned`, ids, and dates: never the state.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


class MissingCassette(Exception):
    """Raised by `RecordedTransport` when no cassette matches the request.

    Deliberately allowed to escape `evaluate` uncaught (see module
    docstring): a recording gap is not a provider failure, and tests
    convert this into `pytest.skip`, never into a `ProviderResult`.
    """

    def __init__(self, sha: str) -> None:
        super().__init__(f"no cassette recorded for request sha256={sha}")
        self.sha = sha


class RecordedTransport:
    """Matches a request by the sha256 of its canonical body and replays the
    recorded `answers`, `model_returned`, and `usage` from
    `tests/fixtures/cassettes/<sha>.json`. Raises `MissingCassette(sha)`
    when nothing matches -- never opens a socket (task-3-brief.md)."""

    def __init__(self, cassette_dir: Path) -> None:
        self._cassette_dir = cassette_dir

    def __call__(
        self, host: str, path: str, body_bytes: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes, float, float]:
        import hashlib
        import json

        sha = hashlib.sha256(body_bytes).hexdigest()
        cassette_path = self._cassette_dir / f"{sha}.json"
        try:
            text = cassette_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MissingCassette(sha) from exc

        cassette = json.loads(text)
        response_body = {
            "model": cassette.get("model_returned"),
            "answers": cassette.get("answers"),
            "usage": cassette.get("usage"),
        }
        raw = json.dumps(response_body).encode("utf-8")
        return 200, raw, 0.0, 0.0
