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


__all__ = ["excerpt", "fmt_noul"]
