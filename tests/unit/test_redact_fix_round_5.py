"""Fix round 5 regression tests (task-2-report.md "Fix round 5" section).

1. `has_public_token_prefix` (fix round 4) tested only a PREFIX, so any
   tail could ride a publishable-key prefix: `password = "pk_live_<30>"`
   and `postgres://app:pk_live_<24>@db/prod` stopped being redacted. The
   exemption now requires Stripe's full real shape end to end, and never
   applies to a URL credential or to a password-named assignment (a
   publishable key is never a database password).
2. `generic-api-key` redacted `keyboard-interactive` in `ssh: Permission
   denied (publickey,password,keyboard-interactive).` -- the vendored rule
   accepts `,` as a separator, so any hyphenated lowercase word after
   `password` in a comma-separated list was captured as a value.
"""

from __future__ import annotations

import random
import string

import pytest
from verdict_hot import _redact_filters, redact


def _alnum(seed: int, length: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(length))


# --------------------------------------------------------------------------
# Item 1: the publishable-key exemption needs the whole shape, and never
# applies to a password value.
# --------------------------------------------------------------------------


def test_publishable_key_prefix_on_a_password_value_is_still_redacted() -> None:
    value = "pk_live_" + _alnum(5001, 30)
    text = f'password = "{value}"'
    cleaned, hits = redact.redact(text)
    assert hits >= 1, "a password value must never be exempted as a publishable key"
    assert value not in cleaned


def test_publishable_key_prefix_in_a_url_credential_is_still_redacted() -> None:
    value = "pk_live_" + _alnum(5002, 24)
    text = f"postgres://app:{value}@db/prod"
    cleaned, rule_ids = redact.redact_detail(text)
    assert value not in cleaned
    assert "local-url-credential" in rule_ids


def test_publishable_key_env_assignment_still_survives() -> None:
    text = "STRIPE_PUBLISHABLE_KEY=pk_live_" + _alnum(5003, 24)
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_publishable_key_json_field_still_survives() -> None:
    text = '  "publishableKey": "pk_test_' + _alnum(5004, 28) + '",'
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


@pytest.mark.parametrize(
    "value",
    [
        "pk_live_abc!def" + _alnum(5005, 24),  # a symbol: not the real shape
        "pk_live_short",  # too short
        "sk_live_" + _alnum(5006, 24),  # the SECRET-side prefix
        "pk_live_" + _alnum(5007, 24) + " trailing",  # not the whole value
    ],
)
def test_only_the_full_publishable_key_shape_is_exempt(value: str) -> None:
    assert not _redact_filters.is_stripe_publishable_key(value)


def test_publishable_key_shape_is_recognised() -> None:
    assert _redact_filters.is_stripe_publishable_key("pk_live_" + _alnum(5008, 24))
    assert _redact_filters.is_stripe_publishable_key('"pk_test_' + _alnum(5009, 32) + '"')


def test_malformed_publishable_key_in_env_assignment_is_redacted() -> None:
    value = "pk_live_abc!def" + _alnum(5010, 24)
    text = f"STRIPE_PUBLISHABLE_KEY={value}"
    cleaned, hits = redact.redact(text)
    assert hits >= 1, "a value that is not a real publishable key gets no exemption"
    assert value not in cleaned


# --------------------------------------------------------------------------
# Item 2: hyphenated lowercase vocabulary after a keyword is not a secret.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ssh: Permission denied (publickey,password,keyboard-interactive).",
        "debug1: Authentications that can continue: publickey,password,keyboard-interactive",
        "sudo: pam_unix(sudo:auth): conversation failed; password,keyboard-interactive"
        " not supported",
        "Authentication methods available: password,keyboard-interactive,gssapi-with-mic",
    ],
)
def test_auth_method_lists_are_not_redacted(text: str) -> None:
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"auth-method vocabulary was redacted: {text!r}"
    assert cleaned == text


@pytest.mark.parametrize(
    "value",
    ["keyboard-interactive", "gssapi-with-mic", "hostbased-auth", "public-key-only"],
)
def test_hyphenated_words_are_recognised(value: str) -> None:
    assert _redact_filters.looks_like_hyphenated_words(value)


@pytest.mark.parametrize(
    "value",
    [
        "Xk29fLq8Zt-vQ2",  # mixed case and digits: a real value
        "abc-9df-1ka-2md",  # digits: not vocabulary
        "keyboard",  # no hyphen (the plain-English filter's job)
        "KEYBOARD-INTERACTIVE",  # not lowercase
    ],
)
def test_non_vocabulary_values_are_not_treated_as_hyphenated_words(value: str) -> None:
    assert not _redact_filters.looks_like_hyphenated_words(value)


def test_real_hyphen_bearing_secret_still_hits() -> None:
    value = "Xk29f-Lq8Zt-Qm41v-Rb7Tz"
    text = f"api_key = {value}"
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert value not in cleaned


def test_random_values_are_not_falsely_suppressed_after_round_5() -> None:
    """The round-4 Monte-Carlo bound must still hold with the two new
    filters in place (under 0.5% false suppression)."""
    rng = random.Random(20260921)
    alphabet = string.ascii_lowercase + string.digits + "_-"
    trials = 2000
    suppressed = sum(
        not _redact_filters.passes_local_filters(
            "generic-api-key", "".join(rng.choice(alphabet) for _ in range(40)), ""
        )
        for _ in range(trials)
    )
    assert suppressed / trials < 0.005
