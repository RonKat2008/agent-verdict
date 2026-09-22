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
    """Score thresholds for span/verdict logic. `t_ack_hi` (D-020): a
    stop's `acks_failures` at or above this acknowledges a listed failure.
    `t_done`/`t_ack`/`t_check`/`t_soft`/`t_claim` (task-4-brief.md, PLAN
    5.3) feed the R1-R4 rules in `verdict_policy.py`."""

    t_ack_hi: float
    t_done: float
    t_ack: float
    t_check: float
    t_soft: float
    t_claim: float


class StopPolicy(NamedTuple):
    """Stop/SubagentStop knobs (task-4-brief.md, PLAN 5.4).
    `max_blocks_per_prompt` is the guard's limit; `ceiling` is the hard cap
    a user override can never exceed (`min(max_blocks_per_prompt,
    ceiling)`). `always_verify` forces a call despite no G-STOP evidence.
    `subagent_block` gates whether a SubagentStop verdict may actually
    block; false records `would_have` only."""

    max_blocks_per_prompt: int
    ceiling: int
    always_verify: bool
    subagent_block: bool


class ProviderPolicy(NamedTuple):
    """Provider selection/timing (task-4-brief.md, D-011, D-012). `default`
    is the preset used absent `CLAUDE_PLUGIN_OPTION_PROVIDER`/an override.
    `deadline_s` bounds one call; `budget_s` bounds the whole Stop hook's
    provider work; `retry_min_remaining_s` is the minimum left to retry;
    `breaker_open_s` is the breaker's open duration after three 429s."""

    default: str
    deadline_s: float
    budget_s: float
    retry_min_remaining_s: float
    breaker_open_s: int


class StatePolicy(NamedTuple):
    """Provider state builder budgets (task-2-brief.md, D-010, C4).

    `target_tokens` is the size `state.build_state` compresses toward;
    `max_tokens` is the hard cap it must never exceed (token estimate is
    `len(json.dumps(state)) // 4`). `excerpt_head`/`excerpt_tail` bound how
    much of each `step_output_excerpts` entry survives once compression
    reaches that stage. A user override may omit this whole section --
    like every other section here, `_merge_top_level` overlays the user's
    top-level keys onto the packaged default one key at a time, so leaving
    `state` out of `~/.verdict/policy.json` keeps the packaged default's
    values untouched.
    """

    target_tokens: int
    max_tokens: int
    excerpt_head: int
    excerpt_tail: int


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
    state: StatePolicy
    stop: StopPolicy
    provider: ProviderPolicy
    denylist: tuple[str, ...]
    denylist_ids: tuple[str, ...]


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
    "state",
    "stop",
    "provider",
    "denylist",
    "denylist_ids",
)


def load_policy(path: Path | None = None) -> Policy:
    from ._policy_build import _build_policy

    if path is not None:
        return _build_policy(_read_json_object(path))

    default_dict = _read_json_object(default_policy_path())
    user_path = paths.verdict_home() / _POLICY_FILENAME
    if user_path.exists():
        user_dict = _read_json_object(user_path)
        return _build_policy(_merge_top_level(default_dict, user_dict))
    return _build_policy(default_dict)


def default_policy_path() -> Path:
    """The packaged default policy's path (fix round 1 item 14: promoted
    from `_default_policy_path` to a public name so `verdict_hook.py` and
    `recorders.py` can build the same fail-open fallback `load_policy`
    without reaching into this module's private API)."""
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
