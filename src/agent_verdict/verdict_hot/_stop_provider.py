"""Provider selection and the bounded, optionally-retried Stop call
(split from stop.py to stay under the 400-line file cap, task-4-brief.md).

A provider call is bounded by a background daemon thread rather than by
`evaluate`'s own deadline handling alone: an injected test transport is a
plain function Python cannot interrupt mid-call (provider.py's own
docstring), so a pathological transport that ignores its deadline and
blocks past the whole Stop budget must still let the hook return within
that budget (task-4-brief.md's budget test: "a transport that sleeps 3 s
yields `gate_unavailable` within 2.6 s"). The thread is a daemon and is
never joined again once its wait expires, so a still-running call cannot
hold the process open past normal exit.

Retry policy (controller notes ruling 2): after a failed call with
`error` in `{"timeout", "connect_error"}` or any `http_5xx`, retry exactly
once, only if at least `policy.provider.retry_min_remaining_s` of the
total `policy.provider.budget_s` remains; each call's own deadline is
`min(policy.provider.deadline_s, remaining_budget)`. Never retry on a 4xx,
`breaker_open`, `no_key`, or an `invalid_*` error.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from . import provider as provider_mod
from .cassettes import RecordedTransport
from .policy import Policy

_VALID_PROVIDERS = ("openrouter", "typesafe", "local-only")

TIMEOUT_RESULT = provider_mod.ProviderResult(
    ok=False,
    answers={},
    model_returned=None,
    input_tokens=None,
    conn_ms=0.0,
    infer_ms=0.0,
    status=0,
    error="timeout",
)


def resolve_provider_name(policy: Policy) -> str:
    raw = os.environ.get("CLAUDE_PLUGIN_OPTION_PROVIDER", "").strip().lower()
    if raw in _VALID_PROVIDERS:
        return raw
    configured = policy.provider.default.strip().lower()
    return configured if configured in _VALID_PROVIDERS else "openrouter"


def resolve_api_key(preset: provider_mod.Preset, override: str | None) -> str:
    if override:
        return override
    return os.environ.get(preset.key_env, "")


def cassette_transport() -> Callable[..., tuple[int, bytes, float, float]] | None:
    cassette_dir = os.environ.get("VERDICT_CASSETTE_DIR")
    return RecordedTransport(Path(cassette_dir)) if cassette_dir else None


def _call_with_timeout(
    fn: Callable[..., provider_mod.ProviderResult], args: tuple[object, ...], timeout_s: float
) -> provider_mod.ProviderResult | None:
    import threading

    outcome: list[object] = []

    def _run() -> None:
        try:
            outcome.append(fn(*args))
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            outcome.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(max(timeout_s, 0.0))
    if thread.is_alive() or not outcome:
        return None
    result = outcome[0]
    if isinstance(result, Exception):
        raise result
    return cast(provider_mod.ProviderResult, result)


def _is_retryable(error: str | None) -> bool:
    if error in ("timeout", "connect_error"):
        return True
    return error is not None and error.startswith("http_5")


def call_provider(
    state: object,
    questions: Mapping[str, object],
    preset: provider_mod.Preset,
    api_key: str,
    policy: Policy,
    start: float,
    transport: object,
) -> provider_mod.ProviderResult:
    """Never raises except `cassettes.MissingCassette` (provider.evaluate's
    own contract, propagated unchanged through the bounding thread)."""
    remaining = policy.provider.budget_s - (time.monotonic() - start)
    if remaining <= 0:
        return TIMEOUT_RESULT
    deadline = min(policy.provider.deadline_s, remaining)
    args = (state, questions, preset, api_key, deadline, transport)
    result = _call_with_timeout(provider_mod.evaluate, args, remaining)
    if result is None:
        return TIMEOUT_RESULT
    if result.ok or not _is_retryable(result.error):
        return result
    remaining2 = policy.provider.budget_s - (time.monotonic() - start)
    if remaining2 < policy.provider.retry_min_remaining_s:
        return result
    deadline2 = min(policy.provider.deadline_s, remaining2)
    args2 = (state, questions, preset, api_key, deadline2, transport)
    retried = _call_with_timeout(provider_mod.evaluate, args2, remaining2)
    return retried if retried is not None else result


__all__ = [
    "TIMEOUT_RESULT",
    "call_provider",
    "cassette_transport",
    "resolve_api_key",
    "resolve_provider_name",
]
