"""Fix round 4 regression tests (task-2-report.md "Fix round 4" section).

Each test corresponds to one numbered finding in the coordinator's
fix-round-4 brief:

1. The dictionary-word filter blinded ~99 vendored rules whose captured
   secret begins with a fixed literal prefix (`plaid-api-token`'s
   `access-sandbox-<uuid>` was 100% blind), and was too eager on free
   values (Monte-Carlo false-negative cost ~3.9% at 40 random chars).
2. `local-password-assignment`'s bare-line branch captured the separator
   (`\\W` matches a space), so the captured group was `= "changeme"`,
   unwrapping failed and the placeholder checks were bypassed.
3. The multi-word passphrase branch redacted prose ("password:
   authentication failed for user app on host db.internal").
4. Same-span rule attribution was alphabetical, crediting `generic-api-key`
   over the vendor rule that matched the identical span.
5. The keyword prefilter blinded self-anchored rules whose vendor keyword
   can never occur in the token itself.
"""

from __future__ import annotations

import random
import string

import pytest
from verdict_hot import _redact_filters, _redact_rules, redact

# --------------------------------------------------------------------------
# Finding 1(a): rules whose captured secret starts with a fixed literal
# prefix defined by the rule itself are structured tokens -- the
# placeholder and dictionary-word filters must never apply to them.
# --------------------------------------------------------------------------

_LITERAL_PREFIXED_EXAMPLES = (
    "plaid-api-token",
    "typeform-api-token",
    "mailgun-private-api-token",
    "new-relic-user-api-key",
    "gocardless-api-token",
    "yandex-api-key",
)


@pytest.mark.parametrize("rule_id", _LITERAL_PREFIXED_EXAMPLES)
def test_literal_prefixed_rules_are_generated_as_such(rule_id: str) -> None:
    assert rule_id in _redact_rules.LITERAL_PREFIXED_RULE_IDS


@pytest.mark.parametrize("rule_id", _LITERAL_PREFIXED_EXAMPLES)
def test_literal_prefixed_rules_are_excluded_from_word_filters(rule_id: str) -> None:
    assert rule_id not in _redact_filters._ASSIGNMENT_RULE_IDS
    # A value that the dictionary-word / placeholder filters would reject
    # must still pass for these rules: the rule's own literal prefix is the
    # evidence that the value is a structured token.
    assert _redact_filters.passes_local_filters(rule_id, "access-sandbox-dead-beef-cafe", "")
    assert _redact_filters.passes_local_filters(rule_id, "changeme", "")


def test_free_value_rules_keep_the_word_filters() -> None:
    """Control: rules whose captured value is freeform (no literal prefix of
    the rule's own) must keep placeholder/dictionary filtering."""
    for rule_id in ("generic-api-key", "local-env-secret", "hashicorp-tf-password"):
        assert rule_id in _redact_filters._ASSIGNMENT_RULE_IDS
        assert rule_id not in _redact_rules.LITERAL_PREFIXED_RULE_IDS
    assert not _redact_filters.passes_local_filters("hashicorp-tf-password", '"changeme"', "")


def test_plaid_access_token_is_redacted() -> None:
    token = "access-sandbox-3a0e1f2b-9c4d-4e5f-8a7b-1c2d3e4f5a6b"
    text = f'plaid_access_token = "{token}"'
    cleaned, rule_ids = redact.redact_detail(text)
    assert token not in cleaned
    assert "plaid-api-token" in rule_ids


def test_typeform_token_is_redacted() -> None:
    token = "tfp_" + "".join(random.Random(4001).choice(string.ascii_lowercase) for _ in range(59))
    text = f"typeform_token: {token}"
    cleaned, rule_ids = redact.redact_detail(text)
    assert token not in cleaned
    assert "typeform-api-token" in rule_ids


# --------------------------------------------------------------------------
# Finding 1(b): where the dictionary filter still applies, a lowercase
# dictionary-like run must cover >= 50% of the value, and the value must
# carry no 4+ digit run and no mixed-case alternation.
# --------------------------------------------------------------------------


def test_dictionary_words_still_matches_a_real_name() -> None:
    assert _redact_filters.looks_like_dictionary_words("prod-db-credentials")


@pytest.mark.parametrize(
    "value",
    [
        # word runs cover far less than half the value
        "ab-cd-k3j4h5g6f7d8s9a0q1w2e3r4t5y6u7i8o9p0zxcv",
        # a 4+ digit run: random, not a name
        "api-key-20250921-token",
        # mixed-case alternation: random, not a name
        "prod-DB-credsXyZ-rotate",
    ],
)
def test_dictionary_words_rejects_non_name_values(value: str) -> None:
    assert not _redact_filters.looks_like_dictionary_words(value)


@pytest.mark.parametrize("length", [40, 64])
def test_random_secret_values_are_not_falsely_suppressed(length: int) -> None:
    """Monte-Carlo (finding 1): 2,000 seeded random `[a-z0-9_-]{n}` values
    must pass the free-value filters, with a false-suppression rate under
    0.5% (it was ~3.9% at 40 chars and ~6.7% at 64 before this round)."""
    rng = random.Random(20260921)
    alphabet = string.ascii_lowercase + string.digits + "_-"
    suppressed = 0
    trials = 2000
    for _ in range(trials):
        value = "".join(rng.choice(alphabet) for _ in range(length))
        if not _redact_filters.passes_local_filters("generic-api-key", value, ""):
            suppressed += 1
    rate = suppressed / trials
    assert rate < 0.005, f"false-suppression rate {rate:.4f} at length {length}"


# --------------------------------------------------------------------------
# Finding 2: local-password-assignment must keep the separator and the
# quotes out of the captured group, so the placeholder checks see the value.
# --------------------------------------------------------------------------

_PLACEHOLDER_VALUES = ("changeme", "${var.db_pw}", "<password>", "example", "password", "xxxxxxxx")
_ASSIGNMENT_FORMS = ('  password = "{}"', "  password: {}", "  password={}")


@pytest.mark.parametrize("value", _PLACEHOLDER_VALUES)
@pytest.mark.parametrize("form", _ASSIGNMENT_FORMS)
def test_password_placeholder_does_not_hit(form: str, value: str) -> None:
    text = form.format(value)
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"placeholder password should not hit: {text!r}"
    assert cleaned == text


@pytest.mark.parametrize("form", _ASSIGNMENT_FORMS)
def test_real_password_value_still_hits(form: str) -> None:
    text = form.format("Xk29fLq8Zt!v")
    cleaned, hits = redact.redact(text)
    assert hits >= 1, f"real password should hit: {text!r}"
    assert "Xk29fLq8Zt!v" not in cleaned


# --------------------------------------------------------------------------
# Finding 3: the multi-word passphrase branch is removed -- it redacted
# ordinary error-message prose.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "password: authentication failed for user app on host db.internal",
        "Note: the password must be at least twelve characters long and unique",
        'FATAL:  password authentication failed for user "postgres"',
        "ssh: password: permission denied, please try again",
        "Error: password: incorrect username or password combination",
        "curl: (67) Login denied: password: expired, reset required",
    ],
)
def test_password_prose_is_not_redacted(text: str) -> None:
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"prose was redacted: {text!r}"
    assert cleaned == text


def test_unquoted_multiword_value_is_not_redacted() -> None:
    text = "password: correct horse battery staple"
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_quoted_multiword_value_may_be_redacted() -> None:
    text = 'password: "correct horse battery staple"'
    _cleaned, hits = redact.redact(text)
    assert hits >= 1


# --------------------------------------------------------------------------
# Finding 4: on a tied span, the non-generic rule id is credited.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            'curl -H "X-OPENAI_API_KEY: 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" /v1',
            "openai-api-key",
        ),
        (
            '{"SLACK_BOT_TOKEN": "xoxb-618463801562-977884670599-lsPiAQD2xDz19hzWD5vuakJ9"}',
            "slack-bot-token",
        ),
        (
            "GITHUB_TOKEN=ghp_jK8X5UF6hk1LUckAbfdoejMbTxHTOc7HZ9B3",
            "github-pat",
        ),
    ],
)
def test_tied_span_is_credited_to_the_non_generic_rule(text: str, expected: str) -> None:
    _cleaned, rule_ids = redact.redact_detail(text)
    assert expected in rule_ids
    assert "generic-api-key" not in rule_ids


def test_generic_rule_still_credited_when_it_is_the_only_match() -> None:
    text = 'api_key = "Qm9ndXNLZXlWYWx1ZTEyMzQ1Njc4OTBhYmNkZWY"'
    _cleaned, rule_ids = redact.redact_detail(text)
    assert rule_ids, "expected the generic rule to still fire on its own"


# --------------------------------------------------------------------------
# Finding 5: self-anchored rules whose vendor keyword can never occur in
# the token must not be blinded by the keyword prefilter.
# --------------------------------------------------------------------------


def test_airtable_personal_access_token_in_bare_context() -> None:
    rng = random.Random(4002)
    body = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(14))
    digest = "".join(rng.choice("0123456789abcdef") for _ in range(64))
    token = f"pat{body}.{digest}"
    text = f"request rejected, token {token} is no longer valid"
    cleaned, rule_ids = redact.redact_detail(text)
    assert token not in cleaned
    assert "airtable-personnal-access-token" in rule_ids


def test_facebook_access_token_in_bare_context() -> None:
    rng = random.Random(4003)
    digits = "".join(rng.choice(string.digits) for _ in range(16))
    body = "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(32))
    token = f"{digits}|{body}"
    text = f"graph call failed with {token} in the query string"
    cleaned, rule_ids = redact.redact_detail(text)
    assert token not in cleaned
    assert "facebook-access-token" in rule_ids
