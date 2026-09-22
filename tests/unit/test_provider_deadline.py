"""The real socket path must bound the TOTAL wait and the body size, not
just each syscall (Task 3 fix round 1)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from verdict_hot import provider
from verdict_hot.provider import PRESETS, Breaker, evaluate

QUESTIONS = {"claims_done": {"type": "noul", "instructions": "x"}}


class _DribbleResponse:
    """One byte per read, each read just under the per-call timeout."""

    status = 200

    def __init__(self, per_read_s: float) -> None:
        self.per_read_s = per_read_s
        self.calls = 0

    def read(self, amt: int | None = None) -> bytes:
        self.calls += 1
        time.sleep(self.per_read_s)
        return b"x"  # never ends


class _HugeResponse:
    status = 200

    def read(self, amt: int | None = None) -> bytes:
        return b"y" * (amt or 65536)  # endless body


class _FakeConn:
    response_factory: Callable[[], object] | None = None
    sock = None

    def __init__(self, host: str, timeout: float = 0, context: object = None) -> None:
        self.host = host

    def connect(self) -> None:
        pass

    def request(self, *a: object, **k: object) -> None:
        pass

    def getresponse(self) -> object:
        assert _FakeConn.response_factory is not None
        assert _FakeConn.response_factory is not None
        return _FakeConn.response_factory()

    def close(self) -> None:
        pass


def _patch_conn(monkeypatch: pytest.MonkeyPatch, factory: Callable[[], object]) -> None:
    import http.client

    _FakeConn.response_factory = factory
    monkeypatch.setattr(http.client, "HTTPSConnection", _FakeConn)


def test_dribbling_server_cannot_exceed_the_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    deadline = 0.6
    _patch_conn(monkeypatch, lambda: _DribbleResponse(per_read_s=0.15))
    t = time.monotonic()
    result = evaluate({"a": 1}, QUESTIONS, PRESETS["openrouter"], "k" * 40, deadline_s=deadline)
    wall = time.monotonic() - t
    assert result.ok is False and result.error == "timeout"
    assert wall < deadline + 0.3, f"held for {wall:.2f}s"


def test_oversized_body_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_conn(monkeypatch, _HugeResponse)
    result = evaluate({"a": 1}, QUESTIONS, PRESETS["openrouter"], "k" * 40, deadline_s=5.0)
    assert result.ok is False and result.error == "response_too_large"


def test_symlinked_breaker_file_is_not_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERDICT_HOME", str(tmp_path))
    target = tmp_path / "victim.txt"
    target.write_text("keep me")
    link = tmp_path / "breaker.json"
    os.symlink(target, link)
    b = Breaker()
    for _ in range(3):
        b.record(429, now=1000.0)
    assert target.read_text() == "keep me"
    assert b.is_open(now=1001.0) is False  # unwritable state behaves as closed


def test_provider_module_size_limits() -> None:
    src = Path(provider.__file__).read_text().splitlines()
    assert len(src) <= 400
