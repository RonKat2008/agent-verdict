"""Final-review C1: overlapping spans must merge into their union.

`_redact_unsafe` emitted markers left to right and skipped any span that
started before the cursor, so a span that STARTS inside an already-accepted
span but ENDS after it left its tail in the clear. The reviewer reproduced
it with `CLAUDE_PLUGIN_OPTION_API_KEY` set to the first 20 characters of a
`ghp_` token: the env-value span (20 chars) was accepted, the longer
`github-pat` span over the same token was skipped, and the row held
`[REDACTED:env-configured-secret]` followed by the token's remaining 20
characters.

The fix merges overlapping spans across ALL rules into their union before
any marker is emitted, keeping the first span's rule id for attribution
unless a later merged span has a non-generic id and the first does not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from verdict_hot import _redact_rules, redact

# 40 characters: `ghp_` plus the 36 alphanumerics `github-pat` requires.
GHP_TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0KlMnOpQrStUvWxYz"
ENV_PREFIX = GHP_TOKEN[:20]

InstallRules = Callable[[tuple[tuple[str, str, tuple[str, ...], float | None], ...]], None]


@pytest.fixture
def synthetic_rules(monkeypatch: pytest.MonkeyPatch) -> Iterator[InstallRules]:
    """Replace the generated rule table with a small, deterministic one.

    Partial overlaps between two *specific* vendored rules depend on the
    pinned gitleaks snapshot; these synthetic rules exercise the same
    `_rule_hits` -> merge path with spans whose offsets are known exactly.
    `_PATTERN_CACHE` is keyed by rule id, so it is cleared on both sides to
    keep a synthetic pattern from poisoning (or being shadowed by) a real
    rule compiled elsewhere in the session.
    """

    def install(rules: tuple[tuple[str, str, tuple[str, ...], float | None], ...]) -> None:
        redact._PATTERN_CACHE.clear()
        monkeypatch.setattr(_redact_rules, "RULES", rules)

    yield install
    redact._PATTERN_CACHE.clear()


def test_env_prefix_of_a_longer_token_leaves_no_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reviewer's exact scenario (final review, C1)."""
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", ENV_PREFIX)

    cleaned, rule_ids = redact.redact_detail(f"token {GHP_TOKEN} printed")

    assert cleaned == "token [REDACTED:env-configured-secret] printed"
    assert GHP_TOKEN not in cleaned
    assert GHP_TOKEN[20:] not in cleaned
    assert rule_ids == ("env-configured-secret",)


def test_entropy_and_base64_rules_do_not_leave_a_trailing_tail() -> None:
    """A real two-rule overlap from the corpus: the alnum span ends before
    the b64 span's `==` padding, which used to survive."""
    secret = "rBn8XXqW4VpIU1NvnJGz5flxLuvIZT3vK6ob15KrxgpB=="

    cleaned, rule_ids = redact.redact_detail(f'Copy exactly: "{secret}"')

    assert secret not in cleaned
    assert "==" not in cleaned
    assert cleaned.count("[REDACTED:") == 1
    assert len(rule_ids) == 1


def test_two_overlapping_rules_merge_into_one_marker(synthetic_rules: InstallRules) -> None:
    synthetic_rules(
        (
            ("test-vendor-alpha", r"AAAA[a-z]{6}", ("aaaa",), None),
            ("test-vendor-beta", r"[a-z]{4}BBBB", ("bbbb",), None),
        )
    )

    cleaned, rule_ids = redact.redact_detail("row AAAAqrstuvBBBB end")

    assert cleaned == "row [REDACTED:test-vendor-alpha] end"
    assert rule_ids == ("test-vendor-alpha",)


def test_chain_of_three_overlapping_rules_merges_into_one_span(
    synthetic_rules: InstallRules,
) -> None:
    synthetic_rules(
        (
            ("test-vendor-alpha", r"AAAA[a-z]{6}", ("aaaa",), None),
            ("test-vendor-beta", r"[a-z]{4}BBBB", ("bbbb",), None),
            ("test-vendor-gamma", r"BBB[a-z]{4}CCCC", ("cccc",), None),
        )
    )

    cleaned, rule_ids = redact.redact_detail("row AAAAqrstuvBBBBmnopCCCC end")

    assert cleaned == "row [REDACTED:test-vendor-alpha] end"
    assert rule_ids == ("test-vendor-alpha",)


def test_merged_span_is_attributed_to_the_non_generic_rule(
    synthetic_rules: InstallRules,
) -> None:
    """Attribution rule: keep the first span's id, unless it is generic and
    a span merged into it is not."""
    synthetic_rules(
        (
            ("generic-api-key", r"AAAA[a-z]{6}", ("aaaa",), None),
            ("test-vendor-beta", r"[a-z]{4}BBBB", ("bbbb",), None),
        )
    )

    cleaned, rule_ids = redact.redact_detail("row AAAAqrstuvBBBB end")

    assert cleaned == "row [REDACTED:test-vendor-beta] end"
    assert rule_ids == ("test-vendor-beta",)


def test_non_overlapping_spans_still_get_one_marker_each(
    synthetic_rules: InstallRules,
) -> None:
    synthetic_rules(
        (
            ("test-vendor-alpha", r"AAAA[a-z]{6}", ("aaaa",), None),
            ("test-vendor-beta", r"[a-z]{4}BBBB", ("bbbb",), None),
        )
    )

    cleaned, rule_ids = redact.redact_detail("row AAAAqrstuv and mnopBBBB end")

    assert cleaned == "row [REDACTED:test-vendor-alpha] and [REDACTED:test-vendor-beta] end"
    assert rule_ids == ("test-vendor-alpha", "test-vendor-beta")


def test_merging_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", ENV_PREFIX)
    once, _ = redact.redact_detail(f"token {GHP_TOKEN} printed")

    twice, rule_ids = redact.redact_detail(once)

    assert twice == once
    assert rule_ids == ()


def test_merging_is_idempotent_for_synthetic_chains(synthetic_rules: InstallRules) -> None:
    synthetic_rules(
        (
            ("test-vendor-alpha", r"AAAA[a-z]{6}", ("aaaa",), None),
            ("test-vendor-beta", r"[a-z]{4}BBBB", ("bbbb",), None),
        )
    )
    once, _ = redact.redact_detail("row AAAAqrstuvBBBB end")

    twice, hits = redact.redact("row AAAAqrstuvBBBB end")
    again, again_hits = redact.redact(once)

    assert twice == once and hits == 1
    assert again == once and again_hits == 0
