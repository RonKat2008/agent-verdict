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
(`transport=None`) sets the socket timeout to whatever budget remains
before each phase and aborts with `timeout` the moment the budget is
already spent, rather than handing `http.client` the full deadline and
hoping. An injected `transport` cannot be interrupted mid-call (it is a
plain function, not a socket), so `evaluate` instead checks the elapsed
wall time immediately after it returns and converts a still-successful
response into `timeout` if the budget was exceeded -- this is what makes a
fake transport that sleeps past the deadline exercise the same `timeout`
path a slow real connection would.

Answer validation: every key in `questions` must have a matching entry in
the response's `answers` object whose own `type` matches the question's
`type`; a `noul` answer's `noul` value must be a number in `[0, 1]`, a
`score` answer's `score` value must be numeric, and a `choice` answer's
`choice` value must be a string (C2/C3). Extra keys in `answers` beyond
what was asked are ignored. Anything else -- a missing key, a wrong type,
an out-of-range value -- is `invalid_answers`.

`Breaker` persists at `paths.verdict_home() / "breaker.json"`, mode 0600.
Three consecutive HTTP 429 responses open it for 600 seconds; any 200
resets the consecutive count. It never raises: a missing, corrupt, or
unwritable state file behaves as closed. `evaluate` consults it before
making a call (an open breaker short-circuits to `error="breaker_open"`
with no call at all) and records the real HTTP status after one completes.

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

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from . import paths

_BREAKER_FILENAME = "breaker.json"
_BREAKER_OPEN_SECONDS = 600
_BREAKER_THRESHOLD = 3
_BREAKER_FILE_MODE = 0o600

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


class MissingCassette(Exception):
    """Raised by `RecordedTransport` when no cassette matches the request.

    Deliberately allowed to escape `evaluate` uncaught (see module
    docstring): a recording gap is not a provider failure, and tests
    convert this into `pytest.skip`, never into a `ProviderResult`.
    """

    def __init__(self, sha: str) -> None:
        super().__init__(f"no cassette recorded for request sha256={sha}")
        self.sha = sha


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


def _real_call(
    preset: Preset, body: bytes, headers: Mapping[str, str], deadline_s: float, start: float
) -> tuple[int, bytes, float, float]:
    """Open a real HTTPS connection, honoring the remaining deadline before
    each phase. Raises `_TimeoutSignal`/`_ConnectErrorSignal`, caught by
    `_evaluate`, rather than returning a `ProviderResult` itself, so the
    injected-`transport` and real-socket paths converge on one place."""
    import http.client
    import socket
    import ssl

    from . import sslctx

    def _remaining() -> float:
        return deadline_s - (time.monotonic() - start)

    if _remaining() <= 0:
        raise _TimeoutSignal()

    context = sslctx.build_context()
    conn = http.client.HTTPSConnection(preset.host, timeout=_remaining(), context=context)
    try:
        try:
            conn.connect()
        except socket.timeout as exc:  # noqa: UP041 - not a TimeoutError alias until 3.10
            raise _TimeoutSignal() from exc
        except (OSError, ssl.SSLError) as exc:
            raise _ConnectErrorSignal() from exc

        sock = conn.sock
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        connected = time.monotonic()
        conn_ms = (connected - start) * 1000.0
        remaining = _remaining()
        if remaining <= 0:
            raise _TimeoutSignal(conn_ms)
        if sock is not None:
            sock.settimeout(remaining)

        try:
            conn.request("POST", preset.path, body=body, headers=dict(headers))
            response = conn.getresponse()
            raw = response.read()
        except socket.timeout as exc:  # noqa: UP041 - not a TimeoutError alias until 3.10
            raise _TimeoutSignal(conn_ms) from exc
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise _ConnectErrorSignal(conn_ms) from exc

        infer_ms = (time.monotonic() - connected) * 1000.0
        return response.status, raw, conn_ms, infer_ms
    finally:
        conn.close()


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


class Breaker:
    """Circuit breaker persisted at `paths.verdict_home() / "breaker.json"`
    (task-3-brief.md). Three consecutive HTTP 429 responses open it for
    `_BREAKER_OPEN_SECONDS`; any 200 resets the consecutive count. Never
    raises: a missing, corrupt, or unwritable state file behaves as closed.

    `path` is an optional override for tests; production code always uses
    the default (`paths.verdict_home()`, itself `VERDICT_HOME`-overridable),
    resolved lazily on every call rather than cached at construction, the
    same way every other module in this package reads it.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    def _resolve_path(self) -> Path:
        if self._path is not None:
            return self._path
        return paths.verdict_home() / _BREAKER_FILENAME

    def _read(self) -> dict[str, object]:
        import json

        try:
            text = self._resolve_path().read_text(encoding="utf-8")
            parsed = json.loads(text)
        except (OSError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _write(self, data: Mapping[str, object]) -> None:
        import json

        try:
            target = self._resolve_path()
            paths.ensure_private_dir(target.parent, allow_symlink=True)
            text = json.dumps(dict(data), separators=(",", ":"))
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _BREAKER_FILE_MODE)
            try:
                os.fchmod(fd, _BREAKER_FILE_MODE)
                os.write(fd, text.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            pass  # never raises: an unwritable state file behaves as closed

    def is_open(self, now: float) -> bool:
        try:
            data = self._read()
            open_until = data.get("open_until")
            return isinstance(open_until, (int, float)) and now < open_until
        except Exception:  # noqa: BLE001 - never raises (task-3-brief.md)
            return False

    def record(self, status: int, now: float) -> None:
        try:
            data = self._read()
            consecutive = data.get("consecutive_429")
            consecutive = consecutive if isinstance(consecutive, int) else 0
            if status == 429:
                consecutive += 1
                open_until: object = data.get("open_until", 0)
                if consecutive >= _BREAKER_THRESHOLD:
                    open_until = now + _BREAKER_OPEN_SECONDS
                self._write({"consecutive_429": consecutive, "open_until": open_until})
            elif status == 200:
                self._write({"consecutive_429": 0, "open_until": data.get("open_until", 0)})
        except Exception:  # noqa: BLE001 - never raises (task-3-brief.md)
            return


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
