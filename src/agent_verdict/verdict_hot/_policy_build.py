"""Policy JSON -> `Policy` NamedTuple construction (split from `policy.py`
to stay under the 400-line file cap, task-7-brief.md cleanup item 5).

`policy.py` keeps the public loading API (`load_policy`, `default_policy_path`)
and every `NamedTuple` shape; this module holds the section-by-section
builders those shapes are assembled from, plus the small field-reading
helpers (`_require`, `_str_tuple`) only they need. `policy.py` imports
`_build_policy` from here lazily, inside `load_policy`, so the two modules
never form a module-level import cycle (this module imports the `NamedTuple`
classes and `_REQUIRED_TOP_KEYS` from `policy.py` at module level instead).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .policy import (
    _REQUIRED_TOP_KEYS,
    ChecksPolicy,
    ClaimsPolicy,
    NeverSendPolicy,
    Policy,
    PolicyError,
    ProviderPolicy,
    SoftFailurePolicy,
    SpanPolicy,
    StatePolicy,
    StopPolicy,
    StorePolicy,
    Thresholds,
)


def _require(d: Mapping[str, Any], key: str, path_hint: str) -> Any:
    if key not in d:
        raise PolicyError(f"policy missing required key: {path_hint}.{key}")
    return d[key]


def _str_tuple(value: Any, path_hint: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"policy field {path_hint} must be a list of strings")
    return tuple(value)


def _build_store(raw: Mapping[str, Any]) -> StorePolicy:
    return StorePolicy(
        retention_days=int(_require(raw, "retention_days", "store")),
        excerpt_head=int(_require(raw, "excerpt_head", "store")),
        excerpt_tail=int(_require(raw, "excerpt_tail", "store")),
        input_excerpt_max=int(_require(raw, "input_excerpt_max", "store")),
        error_head=int(_require(raw, "error_head", "store")),
        error_tail=int(_require(raw, "error_tail", "store")),
        prompt_max_chars=int(_require(raw, "prompt_max_chars", "store")),
        final_message_max_chars=int(_require(raw, "final_message_max_chars", "store")),
    )


def _build_never_send(raw: Mapping[str, Any]) -> NeverSendPolicy:
    return NeverSendPolicy(
        path_globs=_str_tuple(_require(raw, "path_globs", "never_send"), "never_send.path_globs"),
        path_exclude_globs=_str_tuple(
            _require(raw, "path_exclude_globs", "never_send"), "never_send.path_exclude_globs"
        ),
        bash_patterns=_str_tuple(
            _require(raw, "bash_patterns", "never_send"), "never_send.bash_patterns"
        ),
    )


def _build_checks(raw: Mapping[str, Any]) -> ChecksPolicy:
    return ChecksPolicy(
        runner_patterns=_str_tuple(
            _require(raw, "runner_patterns", "checks"), "checks.runner_patterns"
        ),
        extra=_str_tuple(_require(raw, "extra", "checks"), "checks.extra"),
    )


def _build_soft_failure(raw: Mapping[str, Any]) -> SoftFailurePolicy:
    return SoftFailurePolicy(
        output_patterns=_str_tuple(
            _require(raw, "output_patterns", "soft_failure"), "soft_failure.output_patterns"
        ),
        masking_patterns=_str_tuple(
            _require(raw, "masking_patterns", "soft_failure"), "soft_failure.masking_patterns"
        ),
        http_error_patterns=_str_tuple(
            _require(raw, "http_error_patterns", "soft_failure"),
            "soft_failure.http_error_patterns",
        ),
        tools=_str_tuple(_require(raw, "tools", "soft_failure"), "soft_failure.tools"),
    )


def _build_claims(raw: Mapping[str, Any]) -> ClaimsPolicy:
    return ClaimsPolicy(
        success_verbs=_str_tuple(_require(raw, "success_verbs", "claims"), "claims.success_verbs"),
        max_claims=int(_require(raw, "max_claims", "claims")),
    )


def _build_span(raw: Mapping[str, Any]) -> SpanPolicy:
    return SpanPolicy(max_prompts=int(_require(raw, "max_prompts", "span")))


def _build_thresholds(raw: Mapping[str, Any]) -> Thresholds:
    return Thresholds(
        t_ack_hi=float(_require(raw, "t_ack_hi", "thresholds")),
        t_done=float(_require(raw, "t_done", "thresholds")),
        t_ack=float(_require(raw, "t_ack", "thresholds")),
        t_check=float(_require(raw, "t_check", "thresholds")),
        t_soft=float(_require(raw, "t_soft", "thresholds")),
        t_claim=float(_require(raw, "t_claim", "thresholds")),
    )


def _build_stop(raw: Mapping[str, Any]) -> StopPolicy:
    return StopPolicy(
        max_blocks_per_prompt=int(_require(raw, "max_blocks_per_prompt", "stop")),
        ceiling=int(_require(raw, "ceiling", "stop")),
        always_verify=bool(_require(raw, "always_verify", "stop")),
        subagent_block=bool(_require(raw, "subagent_block", "stop")),
    )


def _build_provider(raw: Mapping[str, Any]) -> ProviderPolicy:
    return ProviderPolicy(
        default=str(_require(raw, "default", "provider")),
        deadline_s=float(_require(raw, "deadline_s", "provider")),
        budget_s=float(_require(raw, "budget_s", "provider")),
        retry_min_remaining_s=float(_require(raw, "retry_min_remaining_s", "provider")),
        breaker_open_s=int(_require(raw, "breaker_open_s", "provider")),
    )


def _build_state(raw: Mapping[str, Any]) -> StatePolicy:
    return StatePolicy(
        target_tokens=int(_require(raw, "target_tokens", "state")),
        max_tokens=int(_require(raw, "max_tokens", "state")),
        excerpt_head=int(_require(raw, "excerpt_head", "state")),
        excerpt_tail=int(_require(raw, "excerpt_tail", "state")),
    )


def _build_denylist(raw: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`denylist` regexes paired by position with `denylist_ids` (rules.py)."""
    patterns = _str_tuple(raw["denylist"], "denylist")
    ids = _str_tuple(raw["denylist_ids"], "denylist_ids")
    if len(patterns) != len(ids):
        raise PolicyError("denylist and denylist_ids must have the same length")
    return patterns, ids


def _build_policy(raw: Mapping[str, Any]) -> Policy:
    for key in _REQUIRED_TOP_KEYS:
        if key not in raw:
            raise PolicyError(f"policy missing required key: {key}")

    try:
        store = _build_store(raw["store"])
        never_send = _build_never_send(raw["never_send"])
        checks = _build_checks(raw["checks"])
        soft_failure = _build_soft_failure(raw["soft_failure"])
        claims = _build_claims(raw["claims"])
        span = _build_span(raw["span"])
        thresholds = _build_thresholds(raw["thresholds"])
        state = _build_state(raw["state"])
        stop = _build_stop(raw["stop"])
        provider = _build_provider(raw["provider"])
        denylist, denylist_ids = _build_denylist(raw)
    except (TypeError, ValueError, KeyError) as exc:
        raise PolicyError(f"malformed policy field: {exc}") from exc

    return Policy(
        policy_version=str(raw["policy_version"]),
        mode=str(raw["mode"]),
        store=store,
        never_send=never_send,
        checks=checks,
        soft_failure=soft_failure,
        claims=claims,
        span=span,
        thresholds=thresholds,
        state=state,
        stop=stop,
        provider=provider,
        denylist=denylist,
        denylist_ids=denylist_ids,
    )


__all__ = ["_build_policy"]
