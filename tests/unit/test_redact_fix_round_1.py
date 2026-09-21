"""Fix round 1 regression tests (task-2-report.md "Fix round 1" section).

Each test corresponds to one numbered item in the coordinator's fix-round-1
brief: explicit failure on internal exception, group-union span selection,
widened trailing delimiters, dedicated local rules for families gitleaks
does not cover, password assignment without a `=`, and windowing/PEM
boundary behavior on large inputs.
"""

from __future__ import annotations

import random

import pytest
from verdict_hot import redact

# --------------------------------------------------------------------------
# Item 3: redact()/redact_detail() must fail explicitly, never return raw text.
# --------------------------------------------------------------------------


def test_redact_returns_failure_marker_on_internal_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_window: str, _lowered: str) -> list[tuple[int, int, str]]:
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(redact, "_rule_hits", _boom)
    cleaned, hits = redact.redact("some text with a secret sk-ant-api03-" + "a" * 93 + "AA")
    assert cleaned == "[redaction failed]"
    assert hits == -1


def test_redact_detail_returns_failure_marker_on_internal_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_window: str, _lowered: str) -> list[tuple[int, int, str]]:
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(redact, "_rule_hits", _boom)
    cleaned, rule_ids = redact.redact_detail("anything at all")
    assert cleaned == "[redaction failed]"
    assert rule_ids == ()


def test_redact_still_works_normally_without_a_monkeypatched_failure() -> None:
    cleaned, hits = redact.redact("no secrets here, just prose")
    assert cleaned == "no secrets here, just prose"
    assert hits == 0


# --------------------------------------------------------------------------
# Item 4: redact the union of participating groups, or the whole match.
# --------------------------------------------------------------------------


def test_curl_bearer_auth_header_is_redacted() -> None:
    text = 'curl -H "Authorization: Bearer AbCdEfGh12345678IjKlMnOpQrStUvWx"'
    cleaned, rule_ids = redact.redact_detail(text)
    assert "AbCdEfGh12345678IjKlMnOpQrStUvWx" not in cleaned
    assert "curl-auth-header" in rule_ids


def test_curl_basic_auth_header_single_quoted_is_redacted() -> None:
    text = "curl -H 'Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1Njc4'"
    cleaned, rule_ids = redact.redact_detail(text)
    assert "dXNlcjpwYXNzd29yZDEyMzQ1Njc4" not in cleaned
    assert "curl-auth-header" in rule_ids


def test_curl_basic_auth_header_double_quoted_is_redacted() -> None:
    text = 'curl -H "Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1Njc4"'
    cleaned, rule_ids = redact.redact_detail(text)
    assert "dXNlcjpwYXNzd29yZDEyMzQ1Njc4" not in cleaned
    assert "curl-auth-header" in rule_ids


# --------------------------------------------------------------------------
# Item 5: widened trailing delimiter (lookahead, never consumed/redacted).
# --------------------------------------------------------------------------

_STRIPE_SECRET = "sk_live_abcdEFGH1234567890"


@pytest.mark.parametrize("delimiter", [")", "]", "}", ",", ".", ":", ">", "<", "`"])
def test_widened_trailing_delimiters_are_recognized_but_not_consumed(delimiter: str) -> None:
    text = f"token is {_STRIPE_SECRET}{delimiter}next"
    cleaned, hits = redact.redact(text)
    assert hits >= 1, f"delimiter {delimiter!r} prevented a match"
    assert _STRIPE_SECRET not in cleaned
    assert cleaned.endswith(f"{delimiter}next")  # delimiter itself is not redacted


def test_trailing_delimiter_still_works_at_end_of_string() -> None:
    cleaned, hits = redact.redact(f"token is {_STRIPE_SECRET}")
    assert hits >= 1
    assert _STRIPE_SECRET not in cleaned


# --------------------------------------------------------------------------
# Item 6: dedicated local rules for families gitleaks does not cover, and
# coverage checks for families with an existing vendored rule.
# --------------------------------------------------------------------------


def test_openrouter_key_is_redacted() -> None:
    secret = "sk-or-v1-" + "a" * 64
    cleaned, rule_ids = redact.redact_detail(f"the key is {secret}")
    assert secret not in cleaned
    assert "local-openrouter" in rule_ids


def test_openai_project_key_is_redacted() -> None:
    secret = "sk-proj-" + "AbCdEfGhIj1234567890" * 2
    cleaned, rule_ids = redact.redact_detail(f"OPENAI key = {secret}")
    assert secret not in cleaned
    assert rule_ids  # caught by either the dedicated local rule or openai-api-key


def test_anthropic_key_is_redacted() -> None:
    secret = "sk-ant-api03-" + "a" * 93 + "AA"
    cleaned, rule_ids = redact.redact_detail(f"bare value: {secret}")
    assert secret not in cleaned
    assert "anthropic-api-key" in rule_ids


def _varied(rng: random.Random, alphabet: str, n: int) -> str:
    # Entropy-bearing filler: several vendored rules carry a `entropy`
    # floor, so an all-one-character secret (zero entropy) is not a valid
    # test fixture for them.
    return "".join(rng.choice(alphabet) for _ in range(n))


def test_huggingface_token_is_redacted() -> None:
    rng = random.Random(1)
    secret = "hf_" + _varied(rng, "abcdefghijklmnopqrstuvwxyz", 34)
    cleaned, rule_ids = redact.redact_detail(f"bare value: {secret}")
    assert secret not in cleaned
    assert "huggingface-access-token" in rule_ids


def test_github_fine_grained_pat_is_redacted() -> None:
    rng = random.Random(2)
    secret = "github_pat_" + _varied(
        rng, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", 82
    )
    cleaned, rule_ids = redact.redact_detail(f"bare value: {secret}")
    assert secret not in cleaned
    assert "github-fine-grained-pat" in rule_ids


def test_sendgrid_key_is_redacted() -> None:
    rng = random.Random(3)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    secret = "SG." + _varied(rng, alphabet, 22) + "." + _varied(rng, alphabet, 43)
    cleaned, rule_ids = redact.redact_detail(f"bare value: {secret}")
    assert secret not in cleaned
    assert "sendgrid-api-token" in rule_ids


def test_twilio_account_sid_is_redacted() -> None:
    secret = "AC" + "0123456789abcdef" * 2
    cleaned, rule_ids = redact.redact_detail(f"account sid {secret}")
    assert secret not in cleaned
    assert "local-twilio-account-sid" in rule_ids


def test_twilio_api_key_is_redacted() -> None:
    secret = "SK" + "0123456789abcdef" * 2
    cleaned, rule_ids = redact.redact_detail(f"bare value: {secret}")
    assert secret not in cleaned
    assert "twilio-api-key" in rule_ids


def test_gcp_service_account_private_key_json_is_redacted() -> None:
    body = "\\n".join("A" * 64 for _ in range(20))
    text = (
        '{"type": "service_account", "private_key": '
        f'"-----BEGIN PRIVATE KEY-----\\n{body}\\n-----END PRIVATE KEY-----\\n"}}'
    )
    cleaned, rule_ids = redact.redact_detail(text)
    assert "BEGIN PRIVATE KEY" not in cleaned
    assert "private-key" in rule_ids


# --------------------------------------------------------------------------
# Item 7: password assignment without "=" (YAML, .netrc), placeholders and
# PASSWORD_MIN_LENGTH must not hit.
# --------------------------------------------------------------------------


def test_yaml_password_colon_space_is_redacted() -> None:
    cleaned, hits = redact.redact("password: Sup3r!Secret9")
    assert hits >= 1
    assert "Sup3r!Secret9" not in cleaned


def test_netrc_style_password_space_separated_is_redacted() -> None:
    text = "machine example.com login bob password Sup3r!Secret9"
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert "Sup3r!Secret9" not in cleaned


def test_password_with_punctuation_is_redacted() -> None:
    cleaned, hits = redact.redact("password = S3cr!t&Value@9")
    assert hits >= 1
    assert "S3cr!t&Value@9" not in cleaned


def test_password_min_length_env_var_does_not_hit() -> None:
    cleaned, hits = redact.redact("PASSWORD_MIN_LENGTH=12")
    assert hits == 0
    assert cleaned == "PASSWORD_MIN_LENGTH=12"


def test_password_changeme_placeholder_does_not_hit() -> None:
    cleaned, hits = redact.redact("password: changeme")
    assert hits == 0
    assert cleaned == "password: changeme"


def test_secret_name_slug_does_not_hit() -> None:
    cleaned, hits = redact.redact("SECRET_NAME=prod-db-credentials")
    assert hits == 0
    assert cleaned == "SECRET_NAME=prod-db-credentials"


# --------------------------------------------------------------------------
# Item 8: windowing must scan the whole input; overlap must not split a
# multi-KB PEM block straddling a window boundary.
# --------------------------------------------------------------------------


def _pem_block(rng: random.Random, body_kb: float) -> str:
    lines = ["-----BEGIN RSA PRIVATE KEY-----"]
    n_lines = int(body_kb * 1024 / 65)
    for _ in range(n_lines):
        lines.append(
            "".join(
                rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop0123456789+/")
                for _ in range(64)
            )
        )
    lines.append("-----END RSA PRIVATE KEY-----")
    return "\n".join(lines)


def test_pem_block_straddling_a_window_boundary_is_fully_redacted() -> None:
    rng = random.Random(99)
    window_size = redact._WINDOW_SIZE
    pem = _pem_block(rng, body_kb=6)
    # Position the PEM block so it starts 5 KB before the window boundary
    # and therefore ends about 1 KB after it.
    prefix_len = window_size - 5 * 1024
    prefix = "x" * prefix_len
    suffix = "y" * 4096
    text = prefix + pem + suffix
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert "-----BEGIN RSA PRIVATE KEY-----" not in cleaned
    assert cleaned.endswith(suffix)
    assert cleaned.startswith("x" * 100)


def test_secret_near_the_end_of_a_200kb_input_is_still_redacted() -> None:
    padding = "ordinary log output. " * ((200 * 1024) // len("ordinary log output. "))
    secret = "sk-ant-api03-" + "b" * 93 + "AA"
    text = padding + secret
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert secret not in cleaned
    assert len(text) > 200 * 1024
