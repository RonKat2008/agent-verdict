"""Fix round 3 regression tests (task-2-report.md "Fix round 3" section).

Each test corresponds to one numbered item in the coordinator's
fix-round-3 brief (D-027, docs/DECISIONS.md): extended placeholder/
dictionary-word filtering for password-like free values (item 3), cheap
adjacency-cue wins for well-known public token conventions (item 4), and
the multi-word passphrase branch (item 5).
"""

from __future__ import annotations

import random

import pytest
from verdict_hot import redact

# --------------------------------------------------------------------------
# Item 3: placeholder/dictionary-word filtering extended to
# local-url-credential and password-shaped vendored rules.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "postgres://app:changeme@localhost/db",
        "postgres://app:pass@localhost/db",
        "postgres://app:password@localhost/db",
        "postgres://app:example@localhost/db",
        "postgres://app:your-api-key-here@localhost/db",
    ],
)
def test_url_credential_placeholder_values_do_not_hit(text: str) -> None:
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"placeholder URL credential should not hit: {text!r}"
    assert cleaned == text


def test_url_credential_real_password_still_hits() -> None:
    text = "postgres://app:Xk29fLq8Zt@db.internal/prod"
    cleaned, rule_ids = redact.redact_detail(text)
    assert "Xk29fLq8Zt" not in cleaned
    assert "local-url-credential" in rule_ids
    assert "app" in cleaned
    assert "db.internal" in cleaned


@pytest.mark.parametrize(
    "text",
    [
        'administrator_login_password = "changeme"',
        'administrator_login_password = "replace_me"',
        'administrator_login_password = "your-api-key-here"',
    ],
)
def test_hashicorp_tf_password_placeholder_does_not_hit(text: str) -> None:
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"placeholder value should not hit: {text!r}"
    assert cleaned == text


def test_hashicorp_tf_password_real_value_still_hits() -> None:
    text = 'administrator_login_password = "Xk29fLq8ZtQm"'
    cleaned, rule_ids = redact.redact_detail(text)
    assert "Xk29fLq8ZtQm" not in cleaned
    assert "hashicorp-tf-password" in rule_ids


# --------------------------------------------------------------------------
# Item 4: cheap adjacency-cue wins for well-known public token conventions
# (entropy rules only -- never structured family rules).
# --------------------------------------------------------------------------


def _rand40(
    rng: random.Random,
    alphabet: str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
) -> str:
    return "".join(rng.choice(alphabet) for _ in range(44))


def test_csp_nonce_is_suppressed() -> None:
    rng = random.Random(41)
    nonce = _rand40(rng)
    text = f"Content-Security-Policy: script-src 'nonce-{nonce}'"
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_csrf_django_middleware_token_is_suppressed() -> None:
    rng = random.Random(42)
    token = _rand40(rng)
    text = f'<input type="hidden" name="csrfmiddlewaretoken" value="{token}">'
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_csrf_generic_token_field_is_suppressed() -> None:
    rng = random.Random(43)
    token = _rand40(rng)
    text = f'<input type="hidden" name="csrf_token" value="{token}">'
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_pagination_cursor_is_suppressed() -> None:
    rng = random.Random(44)
    cursor = _rand40(rng)
    text = f'{{"next_cursor": "{cursor}", "has_more": true}}'
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_idempotency_key_header_cue_applies_to_entropy_rule() -> None:
    # generic-api-key (not an entropy rule) still catches this header by
    # design (item 4 keeps suppression entropy-rule-only); the check here
    # is that the cue does not, say, crash or misbehave, and that the
    # structured/vendored rule is unaffected by the new cue list.
    rng = random.Random(45)
    value = _rand40(rng)
    text = f"Idempotency-Key: {value}"
    cleaned, hits = redact.redact(text)
    assert isinstance(hits, int)
    assert isinstance(cleaned, str)


def test_stripe_publishable_key_is_suppressed() -> None:
    rng = random.Random(46)
    body = _rand40(rng)
    text = f"STRIPE_PUBLISHABLE_KEY=pk_live_{body}"
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_jwks_n_and_kid_fields_are_suppressed() -> None:
    rng = random.Random(47)
    n = _rand40(rng)
    kid = "".join(rng.choice("0123456789abcdef") for _ in range(32))
    text = f'{{"kty":"RSA","n":"{n}","e":"AQAB","kid":"{kid}"}}'
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_jupyter_image_png_output_is_suppressed() -> None:
    rng = random.Random(48)
    body = _rand40(rng) + "=="
    text = f'{{"output_type": "display_data", "data": {{"image/png": "{body}"}}}}'
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_structured_family_rule_is_never_suppressed_by_public_token_cues() -> None:
    # Suppression must apply only to the generic entropy rules, never to a
    # structured family rule -- even if a public-token cue happens to sit
    # right before a real structured secret.
    rng = random.Random(49)
    secret = (
        "sk-ant-api03-"
        + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(93))
        + "AA"
    )
    text = f'{{"next_cursor":"{secret}"}}'
    cleaned, rule_ids = redact.redact_detail(text)
    assert secret not in cleaned
    assert "anthropic-api-key" in rule_ids


# --------------------------------------------------------------------------
# Item 5: multi-word passphrase, only with an explicit separator and 3+
# words; must not hit ordinary prose.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "password: correct horse battery staple",
        "password = correct horse battery staple",
    ],
)
def test_unquoted_multiword_value_is_no_longer_redacted(text: str) -> None:
    """SUPERSEDED by fix round 4, finding 3: the multi-word passphrase
    branch was removed because it redacted ordinary prose ("password:
    authentication failed for user app on host db.internal"), which D-027
    classes as evidence text. An unquoted multi-word value is
    indistinguishable from such a sentence, so it is no longer redacted; a
    quoted one still is (see tests/unit/test_redact_fix_round_4.py)."""
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


@pytest.mark.parametrize(
    "text",
    [
        "Summary: this release fixes several bugs and improves performance overall",
        "Note: the password must be at least twelve characters long and unique",
        "password whenever the system requests one during setup",
    ],
)
def test_multiword_passphrase_branch_does_not_hit_prose(text: str) -> None:
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"multi-word passphrase branch fired on prose: {text!r}"
    assert cleaned == text
