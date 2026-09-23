"""Shared truncation helpers for CLI output (task-6-brief.md, ruling 7).

Every command that could print ledger text (`show`, `replay`) goes through
these so raw tool output, error excerpts, and full prompts never reach a
terminal -- only a short, whitespace-collapsed excerpt does. This mirrors
the same 80-character command truncation `verdict_policy._sanitize_command`
already uses for block reasons (D-016), kept as a small local copy here so
the CLI package never imports the hot-path `verdict_policy` module just for
one string helper.
"""

from __future__ import annotations

_DEFAULT_EXCERPT_CHARS = 80


def excerpt(text: object, limit: int = _DEFAULT_EXCERPT_CHARS) -> str:
    """Collapse whitespace and truncate `text` to at most `limit` characters.

    Never raises: a non-string `text` renders as an empty excerpt rather
    than crashing a report over one malformed ledger row.
    """
    if not isinstance(text, str):
        return ""
    collapsed = " ".join(text.split())
    return collapsed[:limit]


def fmt_noul(value: object) -> str:
    """Render a `noul` answer to 2 decimal places, or `"-"` when absent or
    not a plain number (never a bool, which `isinstance(x, (int, float))`
    would otherwise accept)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.2f}"


_GUARD_PREFIX = "guard_"
_JUDGED_PLAIN = ("evidence", "always_verify")


def is_judged_action(row: object) -> bool:
    """True when an `action` row carries a real provider judgement (final
    review I1): the verifier ran and answers came back. That is any
    `gate_reason` of `evidence` or `always_verify` (research mode), or a
    `guard_*` reason (an enforce block the loop guard demoted). Stand-downs,
    provider failures, and exceptions are not judged."""
    if not isinstance(row, dict) or row.get("event") != "action":
        return False
    reason = row.get("gate_reason")
    if not isinstance(reason, str):
        return False
    return reason in _JUDGED_PLAIN or reason.startswith(_GUARD_PREFIX)


__all__ = ["excerpt", "fmt_noul", "is_judged_action"]
