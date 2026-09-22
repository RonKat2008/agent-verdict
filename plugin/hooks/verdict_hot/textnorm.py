"""Text normalization and error-anchored truncation (task-2-brief.md).

`normalize()` folds Unicode compatibility variants (NFKC) and strips
characters used for terminal/log spoofing: zero-width spaces and marks,
bidirectional-control characters (which can visually reorder text to hide
content), and ANSI CSI/OSC escape sequences (color codes and terminal
title-setting). It never raises on malformed input: any string is valid
input to `str.normalize` and to the regexes below.

`truncate_anchored()` keeps a fixed head and tail of a long text intact
rather than cutting off in the middle, since the payload's most informative
line (a stack trace's final `raise`, a compiler's final error) is usually
near the end.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# Zero-width and bidi-control characters (module docstring): zero-width
# space through right-to-left mark (U+200B-U+200F), the explicit
# bidi-embedding/override controls (U+202A-U+202E), the bidi isolate
# controls (U+2066-U+2069), and the BOM / zero-width no-break space
# (U+FEFF).
_ZERO_WIDTH_BIDI_RE = re.compile("[​-‏‪-‮⁦-⁩﻿]")

# ANSI CSI (e.g. color codes: ESC [ ... letter) and OSC (e.g. terminal title:
# ESC ] ... BEL or ESC \) escape sequences.
_ANSI_CSI_RE = r"\x1b\[[0-?]*[ -/]*[@-~]"
_ANSI_OSC_RE = r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
_ANSI_RE = re.compile(f"(?:{_ANSI_CSI_RE}|{_ANSI_OSC_RE})")


def normalize(text: str) -> tuple[str, int]:
    """NFKC-fold `text` and strip zero-width/bidi/ANSI characters.

    Returns the cleaned text and the count of characters removed by the
    stripping step (NFKC folding can change length too, but is not counted:
    it substitutes characters rather than removing information).
    """
    folded = unicodedata.normalize("NFKC", text)
    removed = 0

    def _count_and_drop(match: re.Match[str]) -> str:
        nonlocal removed
        removed += len(match.group())
        return ""

    stripped = _ZERO_WIDTH_BIDI_RE.sub(_count_and_drop, folded)
    stripped = _ANSI_RE.sub(_count_and_drop, stripped)
    return stripped, removed


def truncate_anchored(text: str, head: int, tail: int) -> str:
    """Keep the first `head` and last `tail` characters, marking the gap.

    Returns `text` unchanged when it already fits in `head + tail`. The tail
    is never dropped, so a message near the end of a long payload survives.
    """
    if len(text) <= head + tail:
        return text
    removed = len(text) - head - tail
    marker = f"\n... [{removed} characters truncated] ...\n"
    tail_start = len(text) - tail
    return text[:head] + marker + text[tail_start:]


class Sanitized(NamedTuple):
    text: str
    hits: int
    removed: int
    failed: bool


REDACTION_FAILED_MARKER = "[redaction failed]"


def sanitize(text: str) -> Sanitized:
    """Normalize then redact, the one pipeline for every text that leaves the
    hook process: ledger rows (recorders.py) and the provider request
    (stop.py, G2.5). A redactor failure yields the marker, never the raw
    text (D-017: a redactor exception sends nothing)."""
    from . import redact

    normalized, removed = normalize(text)
    redacted, hits = redact.redact(normalized)
    if hits == -1:
        return Sanitized(REDACTION_FAILED_MARKER, 0, removed, True)
    return Sanitized(redacted, hits, removed, False)
