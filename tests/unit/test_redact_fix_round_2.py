"""Fix round 2 regression tests (task-2-report.md "Fix round 2" section).

Each test corresponds to one numbered item in the coordinator's fix-round-2
brief: structural (adjacency-only) hash/asset-cue suppression instead of
"anywhere in a lookback window", a password-assignment rule that does not
eat ordinary English, SSH public-key / PEM public-key / certificate
exclusions from the high-entropy rules, the Telegram bot token URL rule,
and the Twilio/HuggingFace rule adjustments.
"""

from __future__ import annotations

import random

import pytest
from verdict_hot import redact

# --------------------------------------------------------------------------
# Item 2 (highest priority, false-negative risk): suppression must be
# structural and adjacent only. A hash/asset word ELSEWHERE on the line
# must never suppress a real, unrelated secret.
# --------------------------------------------------------------------------


def _secret40(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(rng.choice(alphabet) for _ in range(44))


def test_sha256_mentioned_earlier_on_the_line_does_not_suppress_a_real_secret() -> None:
    rng = random.Random(11)
    secret = _secret40(rng)
    text = f"sha256 verified. rotated value {secret}"
    cleaned, hits = redact.redact(text)
    assert hits >= 1, "a real secret was suppressed by an unrelated cue word"
    assert secret not in cleaned


def test_integrity_mentioned_earlier_on_the_line_does_not_suppress_a_real_secret() -> None:
    rng = random.Random(12)
    secret = _secret40(rng)
    text = f"integrity ok; new value {secret}"
    cleaned, hits = redact.redact(text)
    assert hits >= 1, "a real secret was suppressed by an unrelated cue word"
    assert secret not in cleaned


@pytest.mark.parametrize(
    "template",
    [
        "base64 encoding is used elsewhere in this file. current secret is {secret}",
        "hash rotation completed last week. today's credential is {secret}",
        "digest mismatch reported in CI. the replacement value is {secret}",
    ],
)
def test_additional_free_text_cue_placements_do_not_suppress_a_real_secret(
    template: str,
) -> None:
    rng = random.Random(hash(template) % (2**32))
    secret = _secret40(rng)
    text = template.format(secret=secret)
    cleaned, hits = redact.redact(text)
    assert hits >= 1, f"a real secret was suppressed for template: {template!r}"
    assert secret not in cleaned


def test_adjacent_cue_still_suppresses_a_real_lockfile_hash() -> None:
    # The adjacency-only replacement must still do its job when the cue IS
    # immediately adjacent (no free text in between).
    rng = random.Random(13)
    hexish = "".join(rng.choice("0123456789abcdef") for _ in range(64))
    text = f'"integrity": "sha512-{hexish}=="'
    cleaned, hits = redact.redact(text)
    assert hits == 0, "an adjacent, legitimate hash cue should still suppress"


# --------------------------------------------------------------------------
# Item 3: local-password-assignment must not eat ordinary English.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a hint about the password prompt.",
        "password whenever",
        "password protected",
        "This page is password protected and requires a valid session.",
        "The password field should never be logged in plaintext.",
        "Users must reset their password every ninety days per policy.",
    ],
)
def test_password_prose_does_not_hit(text: str) -> None:
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"password rule fired on ordinary English: {text!r}"
    assert cleaned == text


def test_password_yaml_style_still_hits() -> None:
    cleaned, hits = redact.redact("password: Sup3r!Secret9")
    assert hits >= 1
    assert "Sup3r!Secret9" not in cleaned


def test_password_netrc_style_still_hits() -> None:
    text = "machine example.com login bob password Sup3r!Secret9"
    cleaned, hits = redact.redact(text)
    assert hits >= 1
    assert "Sup3r!Secret9" not in cleaned


def test_password_changeme_placeholder_still_does_not_hit() -> None:
    cleaned, hits = redact.redact("password: changeme")
    assert hits == 0
    assert cleaned == "password: changeme"


# --------------------------------------------------------------------------
# Item 4: SSH public keys and PEM public-key/certificate blocks must never
# be redacted by local-high-entropy-*; PEM private-key blocks still must be.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key_type,prefix",
    [
        ("ssh-rsa", "AAAAB3NzaC1yc2EAAAADAQABAAABgQC"),
        ("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAI"),
        ("ssh-dss", "AAAAB3NzaC1kc3MAAACBAP"),
        ("ecdsa-sha2-nistp256", "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABB"),
        ("ecdsa-sha2-nistp384", "AAAAE2VjZHNhLXNoYTItbmlzdHAzODQAAAAIbmlzdHAzODQAAABh"),
        ("ecdsa-sha2-nistp521", "AAAAE2VjZHNhLXNoYTItbmlzdHA1MjEAAAAIbmlzdHA1MjEAAACF"),
        ("sk-ssh-ed25519@openssh.com", "AAAAGnNrLXNzaC1lZDI1NTE5QG9wZW5zc2guY29tAAAAI"),
    ],
)
def test_ssh_public_key_body_is_never_redacted(key_type: str, prefix: str) -> None:
    rng = random.Random(21)
    body = prefix + "".join(
        rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/")
        for _ in range(60)
    )
    text = f"{key_type} {body} deploy@build-01"
    cleaned, hits = redact.redact(text)
    assert hits == 0, f"SSH public key body was redacted for {key_type}"
    assert cleaned == text


def test_known_hosts_line_is_never_redacted() -> None:
    rng = random.Random(22)
    body = "AAAAC3NzaC1lZDI1NTE5AAAAI" + "".join(
        rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        for _ in range(60)
    )
    text = f"github.com ssh-ed25519 {body}"
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_pem_public_key_block_is_never_redacted() -> None:
    rng = random.Random(23)
    lines = ["-----BEGIN PUBLIC KEY-----"]
    for _ in range(6):
        lines.append(
            "".join(
                rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/")
                for _ in range(64)
            )
        )
    lines.append("-----END PUBLIC KEY-----")
    text = "\n".join(lines)
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_pem_certificate_block_is_never_redacted() -> None:
    rng = random.Random(24)
    lines = ["-----BEGIN CERTIFICATE-----"]
    for _ in range(10):
        lines.append(
            "".join(
                rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/")
                for _ in range(64)
            )
        )
    lines.append("-----END CERTIFICATE-----")
    text = "\n".join(lines)
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


def test_pem_private_key_block_is_still_redacted() -> None:
    rng = random.Random(25)
    lines = ["-----BEGIN RSA PRIVATE KEY-----"]
    for _ in range(18):
        lines.append(
            "".join(
                rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/")
                for _ in range(64)
            )
        )
    lines.append("-----END RSA PRIVATE KEY-----")
    text = "\n".join(lines)
    cleaned, rule_ids = redact.redact_detail(text)
    assert "private-key" in rule_ids
    assert "-----BEGIN RSA PRIVATE KEY-----" not in cleaned


# --------------------------------------------------------------------------
# Item 5: Telegram bot token URL; Mailgun key (kept only if it does not
# raise text FPR -- see task-2-report.md).
# --------------------------------------------------------------------------


def test_telegram_bot_token_in_url_is_redacted() -> None:
    rng = random.Random(31)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    token = "".join(rng.choice(alphabet) for _ in range(35))
    text = f"https://api.telegram.org/bot123456789:{token}/sendMessage"
    cleaned, rule_ids = redact.redact_detail(text)
    assert token not in cleaned
    assert "local-telegram-bot-token" in rule_ids


def test_mailgun_key_is_redacted_when_included() -> None:
    rng = random.Random(32)
    secret = "key-" + "".join(rng.choice("0123456789abcdef") for _ in range(32))
    cleaned, hits = redact.redact(f"MAILGUN_API_KEY={secret}")
    assert hits >= 1
    assert secret not in cleaned


# --------------------------------------------------------------------------
# Item 6: Twilio keyword narrowed (no false trigger from "cache"/"backup");
# hf_ tokens of 30-40 chars accepted.
# --------------------------------------------------------------------------


def test_twilio_account_sid_is_redacted() -> None:
    secret = "AC" + "0123456789abcdef" * 2
    cleaned, rule_ids = redact.redact_detail(f"account sid {secret}")
    assert secret not in cleaned
    assert "local-twilio-account-sid" in rule_ids


def test_the_word_cache_does_not_trigger_twilio_rule_alone() -> None:
    text = "The cache eviction policy runs every hour and clears stale backups."
    cleaned, hits = redact.redact(text)
    assert hits == 0
    assert cleaned == text


@pytest.mark.parametrize("length", [30, 34, 40])
def test_huggingface_token_variant_lengths_are_redacted(length: int) -> None:
    rng = random.Random(100 + length)
    secret = "hf_" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(length))
    cleaned, rule_ids = redact.redact_detail(f"bare value: {secret}")
    assert secret not in cleaned
    assert rule_ids
