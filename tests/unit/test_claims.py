from __future__ import annotations

import json
from pathlib import Path

from verdict_hot import claims
from verdict_hot.policy import Policy

HOOKS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"


def test_stop_fixture_message_yields_no_claims(default_policy: Policy) -> None:
    payload = json.loads((HOOKS_FIXTURES / "stop.json").read_text(encoding="utf-8"))
    message = payload["last_assistant_message"]

    result = claims.extract_claims(message, default_policy)

    assert result == ()


def test_bulleted_list_extracts_claims_in_order(default_policy: Policy) -> None:
    message = (
        "- Fixed the crash on startup\n"
        "- Added dark mode support\n"
        "- Still investigating the slow query"
    )

    result = claims.extract_claims(message, default_policy)

    assert result == ("Fixed the crash on startup", "Added dark mode support")


def test_message_with_no_success_verbs_yields_no_claims(default_policy: Policy) -> None:
    message = "I looked into the issue but couldn't determine the root cause."

    result = claims.extract_claims(message, default_policy)

    assert result == ()


def test_negated_success_verbs_are_not_claims(default_policy: Policy) -> None:
    message = (
        "It was not fixed yet. "
        "The tests did not pass this time. "
        "The tests are failing again. "
        "I couldn't verify the deployment. "
        "However, everything works correctly now."
    )

    result = claims.extract_claims(message, default_policy)

    # Sentence splitting drops the terminal punctuation along with the
    # delimiter, so the surviving claim has no trailing period.
    assert result == ("However, everything works correctly now",)


def test_markdown_emphasis_is_stripped_from_a_claim(default_policy: Policy) -> None:
    message = "**Fixed** the parser bug and _verified_ the fix works."

    result = claims.extract_claims(message, default_policy)

    assert result == ("Fixed the parser bug and verified the fix works",)


def test_inline_code_span_is_dropped_entirely_not_just_unmarked(default_policy: Policy) -> None:
    """fix round 1 item 3: inline code is DROPPED (content and all), not
    just unwrapped, so a verb that only appears inside code never counts."""
    message = "The stub sets `fixed = True` for local testing. Resolved the real bug separately."

    result = claims.extract_claims(message, default_policy)

    assert result == ("Resolved the real bug separately",)


def test_fenced_code_block_is_dropped_before_claim_extraction(default_policy: Policy) -> None:
    """fix round 1 item 3: a fence's content (even a literal `fixed = True`
    assignment) must never become a claim -- it's code being shown, not an
    assertion."""
    message = (
        "Investigated the issue.\n"
        "```python\n"
        "fixed = True  # tests passed\n"
        "```\n"
        "Updated the changelog."
    )

    result = claims.extract_claims(message, default_policy)

    assert result == ("Updated the changelog",)


def test_tilde_fenced_code_block_is_dropped_too(default_policy: Policy) -> None:
    message = "~~~\nresolved = True\n~~~\nAdded the missing config key."

    result = claims.extract_claims(message, default_policy)

    assert result == ("Added the missing config key",)


def test_blockquote_line_is_dropped_before_claim_extraction(default_policy: Policy) -> None:
    """fix round 1 item 3: a blockquoted line (quoting a teammate or raw
    tool output) must never become the assistant's own claim."""
    message = "> Fixed the bug (quoted from teammate).\nI actually resolved the bug myself."

    result = claims.extract_claims(message, default_policy)

    assert result == ("I actually resolved the bug myself",)


def test_eight_claims_capped_at_six_with_priority_topics_first(default_policy: Policy) -> None:
    message = (
        "Added a new configuration option. "
        "Implemented the retry logic for the API client. "
        "Deployed the service to production. "
        "Created a new database migration. "
        "All unit tests are passing now. "
        "Ran the build and it completed without errors. "
        "Updated the changelog for this release. "
        "Resolved the lint warnings in the CI pipeline."
    )

    result = claims.extract_claims(message, default_policy)

    assert len(result) == 6
    assert result == (
        "All unit tests are passing now",
        "Ran the build and it completed without errors",
        "Resolved the lint warnings in the CI pipeline",
        "Added a new configuration option",
        "Implemented the retry logic for the API client",
        "Deployed the service to production",
    )


def test_each_claim_is_capped_at_240_characters(default_policy: Policy) -> None:
    long_tail = "x" * 400
    message = f"Fixed the bug that caused {long_tail} to appear."

    result = claims.extract_claims(message, default_policy)

    assert len(result) == 1
    assert len(result[0]) == 240
