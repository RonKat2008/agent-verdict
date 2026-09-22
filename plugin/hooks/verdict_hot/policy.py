"""Policy loading: packaged default, optional user override (task-3-brief.md).

Resolution order in M1:

1. An explicit `path` argument, if given.
2. `verdict_home() / "policy.json"`, if it exists -- merged **over** the
   packaged default, key by key at the top level, so a user file may omit
   keys it doesn't want to change (controller ruling, task-3-brief.md).
   Project-level policy files are never read: a cloned repo must not be
   able to relax anything a user hasn't opted into on their own machine.
3. The packaged default, found relative to this file so it works in both
   layouts this same source tree runs in (module docstring in
   `verdict_hot/__init__.py`):
   - CLI copy: `Path(__file__).parent / "default_policy.json"`, placed
     there by `scripts/sync_hot.py`'s `EXTRA_FILES`.
   - Plugin layout: `Path(__file__).resolve().parents[2] / "policies" /
     "default.json"` (`plugin/hooks/verdict_hot/policy.py` -> `plugin/`).

`VERDICT_POLICY_DEFAULT` overrides step 3 entirely when set. It exists only
so tests can point at a fixture policy without touching either packaged
copy; production code never needs to set it.

Invalid JSON, a non-object top level, or a missing required key raises
`PolicyError`. Callers on the hot path are expected to catch it and fall
back to `load_policy(path=<packaged default path>)` or simply tolerate the
exception per the fail-open recorder contract (global-constraints.md) --
this module itself does not fail open, since failing open silently here
would mean silently running with no policy at all.

Loading must be cheap (task-3-brief.md): this module never compiles a
regular expression. Every `*_patterns` / `*_globs` field is kept as plain
strings; `gates.py` and `claims.py` compile and cache lazily.

D-028 (docs/DECISIONS.md; fix round 1): value types here are
`typing.NamedTuple`, never `dataclasses` -- `dataclasses` alone costs
about 8ms per process on Python 3.9 (it unconditionally pulls in
`inspect`, `ast`, `dis`, `tokenize`), and `plugin/hooks/` bans it outright
(`tests/test_import_ban.py`). `typing` is already needed here for `Any`,
so a `NamedTuple` costs only the marginal ~3ms D-028 measured for it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from . import paths

_POLICY_FILENAME = "policy.json"
_PACKAGED_DEFAULT_NAME = "default_policy.json"
_PLUGIN_POLICY_REL = ("policies", "default.json")


class PolicyError(Exception):
    """Raised for invalid JSON or a missing required key while loading a policy."""


class StorePolicy(NamedTuple):
    retention_days: int
    excerpt_head: int
    excerpt_tail: int
    input_excerpt_max: int
    error_head: int
    error_tail: int
    prompt_max_chars: int
    final_message_max_chars: int


class NeverSendPolicy(NamedTuple):
    path_globs: tuple[str, ...]
    path_exclude_globs: tuple[str, ...]
    bash_patterns: tuple[str, ...]


class ChecksPolicy(NamedTuple):
    runner_patterns: tuple[str, ...]
    extra: tuple[str, ...]


class SoftFailurePolicy(NamedTuple):
    output_patterns: tuple[str, ...]
    masking_patterns: tuple[str, ...]
    http_error_patterns: tuple[str, ...]
    tools: tuple[str, ...]


class ClaimsPolicy(NamedTuple):
    success_verbs: tuple[str, ...]
    max_claims: int


class SpanPolicy(NamedTuple):
    """task-1-brief.md: the verification span walk-back (PLAN.md 5.3, D-020).

    Only `max_prompts` is needed by Task 1; kept as its own section (rather
    than folded into an existing one) since Task 4 grows it further (e.g. a
    guard-related field) without touching unrelated policy sections.
    """

    max_prompts: int


class Thresholds(NamedTuple):
    """Score thresholds shared across span/verdict logic.

    Only `t_ack_hi` is needed by Task 1 (D-020: a stop's `acks_failures`
    score at or above this acknowledges a listed failure). Task 4 adds more
    thresholds (e.g. for R1-R4 policy rules) to this same section, so the
    shape stays extensible: a user override may set any subset of fields
    once Task 4 lands, exactly like every other policy section.
    """

    t_ack_hi: float


class Policy(NamedTuple):
    policy_version: str
    mode: str
    store: StorePolicy
    never_send: NeverSendPolicy
    checks: ChecksPolicy
    soft_failure: SoftFailurePolicy
    claims: ClaimsPolicy
    span: SpanPolicy
    thresholds: Thresholds


_REQUIRED_TOP_KEYS = (
    "policy_version",
    "mode",
    "store",
    "never_send",
    "checks",
    "soft_failure",
    "claims",
    "span",
    "thresholds",
)


def load_policy(path: Path | None = None) -> Policy:
    if path is not None:
        return _build_policy(_read_json_object(path))

    default_dict = _read_json_object(_default_policy_path())
    user_path = paths.verdict_home() / _POLICY_FILENAME
    if user_path.exists():
        user_dict = _read_json_object(user_path)
        return _build_policy(_merge_top_level(default_dict, user_dict))
    return _build_policy(default_dict)


def _default_policy_path() -> Path:
    override = os.environ.get("VERDICT_POLICY_DEFAULT")
    if override:
        return Path(override)
    packaged = Path(__file__).parent / _PACKAGED_DEFAULT_NAME
    if packaged.exists():
        return packaged
    plugin_root = Path(__file__).resolve().parents[2]
    return plugin_root.joinpath(*_PLUGIN_POLICY_REL)


def _merge_top_level(default_dict: dict[str, Any], user_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay `user_dict` onto `default_dict`, key by key, top level only.

    A user override may supply e.g. only `{"mode": "enforce"}`; every other
    top-level key (store, never_send, checks, soft_failure, claims) keeps
    its default value untouched. This never mutates `default_dict`
    (coding-style.md: no mutation of inputs).
    """
    return {**default_dict, **user_dict}


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc}") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"invalid JSON in policy file {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PolicyError(f"policy file {path} must contain a JSON object")
    return parsed


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
    return Thresholds(t_ack_hi=float(_require(raw, "t_ack_hi", "thresholds")))


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
    )
