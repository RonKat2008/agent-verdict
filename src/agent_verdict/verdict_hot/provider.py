"""System One provider client: presets, deadline, breaker, cassettes
(task-3-brief.md, D-011, D-012, C1, C3, C5, C7, D1-D3, E2, E6).

`evaluate(state, questions, preset, api_key, deadline_s, transport=None)` is
the only entry point. It never raises (global-constraints.md's fail-open
rule): every failure path returns a `ProviderResult(ok=False, ...)` with a
fixed-string `error` from a small, closed set (`timeout`, `connect_error`,
`http_<status>`, `invalid_json`, `invalid_answers`, `breaker_open`,
`no_key`, `exception:<ClassName>`) -- never response content, and never the
exception's own message, since that could otherwise echo a secret a
transport happened to mention. The one deliberate exception to "never
raises" is `MissingCassette`, which is allowed to propagate out of
`evaluate` uncaught: `RecordedTransport` raises it when a cassette is
missing, and tests convert that into `pytest.skip` rather than treating a
recording gap as a real provider failure.

Request body: exactly `{"model", "state", "questions"}`, serialized by
`canonical_request_body` with `sort_keys=True, separators=(",", ":"),
ensure_ascii=False` -- the same canonical form `RecordedTransport` and
`scripts/record_cassettes.py` hash to key a cassette, so the wire body and
the cassette lookup key are always in sync by construction, not by two
copies of the same recipe drifting apart.

Deadline handling: `deadline_s` is an absolute budget for connect plus
request plus read (task-3-brief.md controller notes). The real transport
(`transport=None`) re-arms the socket timeout to the remaining budget
before connect, before send, and before every chunked read, so a server
that dribbles bytes cannot hold the hook past the deadline; the body is
also capped at `_MAX_BODY_BYTES` (`response_too_large`). An injected
`transport` is a plain function that cannot be interrupted, so `evaluate`
checks wall time after it returns and converts a late success into
`timeout`.

Answer validation: every key in `questions` must have a matching entry in
the response's `answers` object whose own `type` matches the question's
`type`; a `noul` answer's `noul` value must be a number in `[0, 1]`, a
`score` answer's `score` value must be numeric, and a `choice` answer's
`choice` value must be a string (C2/C3). Extra keys in `answers` beyond
what was asked are ignored. Anything else -- a missing key, a wrong type,
an out-of-range value -- is `invalid_answers`.

`Breaker` (breaker.py) persists at `paths.verdict_home() / "breaker.json"`,
mode 0600, O_NOFOLLOW. Three consecutive HTTP 429s open it for 600 s; any
200 resets the count; it never raises. `evaluate` consults it before a call
(`breaker_open`, no call made) and records the real status afterwards.

Security: the API key is used only to build the `Authorization: Bearer`
header handed to the transport; it is never written into a returned
`ProviderResult` field, never embedded in a raised exception's message
(exceptions become the fixed string `exception:<ClassName>`, never
`str(exc)`), and never logged by this module (this module does not log at
all).

Lazy imports (D-028-adjacent, controller notes): `http.client`, `ssl`, and
`json` are imported inside the functions that need them, not at module
level, since this module is loaded only when a Stop hook actually reaches
the provider call -- most hook invocations never import it at all.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import NamedTuple

from .breaker import Breaker
from .cassettes import MissingCassette, RecordedTransport

Transport = Callable[[str, str, bytes, Mapping[str, str]], tuple[int, bytes, float, float]]


class Preset(NamedTuple):
    name: str
    host: str
    path: str
    key_env: str
    model: str


PRESETS: dict[str, Preset] = {
    "openrouter": Preset(
        name="openrouter",
        host="openrouter.ai",
        path="/api/v1/systemone",
        key_env="OPENROUTER_API_KEY",
        model="typesafe/jev-1.13-20260917",
    ),
    "typesafe": Preset(
        name="typesafe",
        host="api.typesafe.ai",
        path="/v1/systemone",
        key_env="TYPESAFE_API_KEY",
        model="jev-1.13.0",
    ),
}


class ProviderResult(NamedTuple):
    ok: bool
    answers: Mapping[str, object]
    model_returned: str | None
    input_tokens: int | None
    conn_ms: float
    infer_ms: float
    status: int
    error: str | None


class _TimeoutSignal(Exception):
    def __init__(self, conn_ms: float = 0.0) -> None:
        super().__init__("deadline exceeded")
        self.conn_ms = conn_ms


class _ConnectErrorSignal(Exception):
    def __init__(self, conn_ms: float = 0.0) -> None:
        super().__init__("connect error")
        self.conn_ms = conn_ms


def canonical_request_body(model: str, state: object, questions: Mapping[str, object]) -> bytes:
    """The exact bytes sent on the wire, and the exact bytes `RecordedTransport`
    and `scripts/record_cassettes.py` hash to key a cassette (task-3-brief.md:
    "Make your matching scheme exactly that canonical form")."""
    import json

    payload = {"model": model, "state": state, "questions": questions}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _failure(
    error: str, *, status: int = 0, conn_ms: float = 0.0, infer_ms: float = 0.0
) -> ProviderResult:
    return ProviderResult(
        ok=False,
        answers={},
        model_returned=None,
        input_tokens=None,
        conn_ms=conn_ms,
        infer_ms=infer_ms,
        status=status,
        error=error,
    )


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_answer(question: object, answer: object) -> bool:
    if not isinstance(question, Mapping) or not isinstance(answer, Mapping):
        return False
    qtype = question.get("type")
    if answer.get("type") != qtype:
        return False
    if qtype == "noul":
        value = answer.get("noul")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return 0.0 <= float(value) <= 1.0
    if qtype == "score":
        return _is_number(answer.get("score"))
    if qtype == "choice":
        return isinstance(answer.get("choice"), str)
    return False


def _validate_answers(answers: Mapping[str, object], questions: Mapping[str, object]) -> bool:
    return all(_valid_answer(question, answers.get(key)) for key, question in questions.items())


def _finish(
    status: int, raw: bytes, questions: Mapping[str, object], conn_ms: float, infer_ms: float
) -> ProviderResult:
    if status != 200:
        return _failure(f"http_{status}", status=status, conn_ms=conn_ms, infer_ms=infer_ms)

    import json

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _failure("invalid_json", status=status, conn_ms=conn_ms, infer_ms=infer_ms)
    if not isinstance(parsed, Mapping):
        return _failure("invalid_json", status=status, conn_ms=conn_ms, infer_ms=infer_ms)

    answers = parsed.get("answers")
    if not isinstance(answers, Mapping) or not _validate_answers(answers, questions):
        return _failure("invalid_answers", status=status, conn_ms=conn_ms, infer_ms=infer_ms)

    usage = parsed.get("usage")
    input_tokens = usage.get("input_tokens") if isinstance(usage, Mapping) else None
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
        input_tokens = None
    model_returned = parsed.get("model")
    if not isinstance(model_returned, str):
        model_returned = None

    return ProviderResult(
        ok=True,
        answers=answers,
        model_returned=model_returned,
        input_tokens=input_tokens,
        conn_ms=conn_ms,
        infer_ms=infer_ms,
        status=status,
        error=None,
    )


_MAX_BODY_BYTES = 512 * 1024
_READ_CHUNK = 65536


class _TooLargeSignal(Exception):
    def __init__(self, conn_ms: float = 0.0) -> None:
        self.conn_ms = conn_ms


def _real_call(
    preset: Preset, body: bytes, headers: Mapping[str, str], deadline_s: float, start: float
) -> tuple[int, bytes, float, float]:
    """Open a real HTTPS connection, re-deriving the remaining budget before
    EVERY phase and every read chunk, so a slow or dribbling server can never
    hold the process past `deadline_s`, and capping the body at
    `_MAX_BODY_BYTES` (fix round 1). Raises the `_*Signal` exceptions that
    `_evaluate` converts into fixed-string errors."""
    import http.client

    from . import sslctx

    def remaining() -> float:
        return deadline_s - (time.monotonic() - start)

    if remaining() <= 0:
        raise _TimeoutSignal()
    conn = http.client.HTTPSConnection(
        preset.host, timeout=remaining(), context=sslctx.build_context()
    )
    try:
        conn_ms = _connect(conn, start, remaining)
        response = _send(conn, preset.path, body, headers, conn_ms, remaining)
        raw = _read_body(conn, response, conn_ms, remaining)
        infer_ms = (time.monotonic() - start) * 1000.0 - conn_ms
        return int(getattr(response, "status", 0)), raw, conn_ms, infer_ms
    finally:
        conn.close()


def _connect(conn: object, start: float, remaining: Callable[[], float]) -> float:
    import socket
    import ssl

    try:
        conn.connect()  # type: ignore[attr-defined]
    except socket.timeout as exc:  # noqa: UP041 - not a TimeoutError alias until 3.10
        raise _TimeoutSignal() from exc
    except (OSError, ssl.SSLError) as exc:
        raise _ConnectErrorSignal() from exc
    sock = getattr(conn, "sock", None)
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn_ms = (time.monotonic() - start) * 1000.0
    if remaining() <= 0:
        raise _TimeoutSignal(conn_ms)
    return conn_ms


def _arm(conn: object, conn_ms: float, remaining: Callable[[], float]) -> None:
    left = remaining()
    if left <= 0:
        raise _TimeoutSignal(conn_ms)
    sock = getattr(conn, "sock", None)
    if sock is not None:
        sock.settimeout(left)


def _send(
    conn: object,
    path: str,
    body: bytes,
    headers: Mapping[str, str],
    conn_ms: float,
    remaining: Callable[[], float],
) -> object:
    import http.client
    import socket
    import ssl

    try:
        _arm(conn, conn_ms, remaining)
        conn.request("POST", path, body=body, headers=dict(headers))  # type: ignore[attr-defined]
        _arm(conn, conn_ms, remaining)
        return conn.getresponse()  # type: ignore[attr-defined]
    except socket.timeout as exc:  # noqa: UP041
        raise _TimeoutSignal(conn_ms) from exc
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise _ConnectErrorSignal(conn_ms) from exc


def _read_body(
    conn: object, response: object, conn_ms: float, remaining: Callable[[], float]
) -> bytes:
    import http.client
    import socket
    import ssl

    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            _arm(conn, conn_ms, remaining)
            chunk = response.read(_READ_CHUNK)  # type: ignore[attr-defined]
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > _MAX_BODY_BYTES:
                raise _TooLargeSignal(conn_ms)
            chunks.append(chunk)
    except socket.timeout as exc:  # noqa: UP041
        raise _TimeoutSignal(conn_ms) from exc
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise _ConnectErrorSignal(conn_ms) from exc


def _evaluate(
    state: object,
    questions: Mapping[str, object],
    preset: Preset,
    api_key: str,
    deadline_s: float,
    transport: Transport | None,
) -> ProviderResult:
    if not api_key:
        return _failure("no_key")

    breaker = Breaker()
    wall_now = time.time()
    if breaker.is_open(wall_now):
        return _failure("breaker_open")

    body = canonical_request_body(preset.model, state, questions)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    start = time.monotonic()
    if deadline_s <= 0:
        return _failure("timeout")

    try:
        if transport is not None:
            status, raw, conn_ms, infer_ms = transport(preset.host, preset.path, body, headers)
            if time.monotonic() - start > deadline_s:
                return _failure("timeout", status=status, conn_ms=conn_ms, infer_ms=infer_ms)
        else:
            status, raw, conn_ms, infer_ms = _real_call(preset, body, headers, deadline_s, start)
    except _TimeoutSignal as exc:
        return _failure("timeout", conn_ms=exc.conn_ms)
    except _ConnectErrorSignal as exc:
        return _failure("connect_error", conn_ms=exc.conn_ms)
    except _TooLargeSignal as exc:
        return _failure("response_too_large", conn_ms=exc.conn_ms)

    breaker.record(status, time.time())
    return _finish(status, raw, questions, conn_ms, infer_ms)


def evaluate(
    state: object,
    questions: Mapping[str, object],
    preset: Preset,
    api_key: str,
    deadline_s: float,
    transport: Transport | None = None,
) -> ProviderResult:
    """Call System One once. Never raises except `MissingCassette` (module
    docstring). No retry: the caller (Task 4) decides whether to retry."""
    try:
        return _evaluate(state, questions, preset, api_key, deadline_s, transport)
    except MissingCassette:
        raise
    except Exception as exc:  # noqa: BLE001 - fail-open: never propagate, never leak exc content
        return _failure(f"exception:{type(exc).__name__}")


__all__ = [
    "PRESETS",
    "Breaker",
    "MissingCassette",
    "Preset",
    "ProviderResult",
    "RecordedTransport",
    "Transport",
    "canonical_request_body",
    "evaluate",
]
