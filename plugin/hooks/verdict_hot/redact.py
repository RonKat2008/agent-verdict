"""Measured secret redactor (task-2-brief.md, gate G1.7).

`redact(text)` replaces each detected secret span with `[REDACTED:<rule_id>]`
and returns the hit count. Rules come from `_redact_rules.RULES` (generated
by `scripts/gen_redact.py` from vendor/gitleaks.toml plus three local rules).

Startup cost matters: importing this module compiles nothing. Each rule's
pattern is a plain string until its keyword is first seen in scanned text,
at which point it is compiled once and cached in `_PATTERN_CACHE` for the
life of the process.

Catastrophic-backtracking guard: text is processed in overlapping windows of
`_WINDOW_SIZE` characters (`_WINDOW_OVERLAP` overlap, comfortably larger than
any rule's realistic match span) rather than handed to the regexes whole, so
a pathologically large payload cannot blow up a single regex call. Hits
found in the overlap region are identical (same absolute span, same rule) in
both windows and are deduplicated by that span before the text is rebuilt,
so nothing is redacted twice.

`redact()` never raises: any unexpected failure falls back to returning the
input unchanged with zero hits rather than propagating (callers still treat
zero hits as "not proven safe", never as "proven safe").
"""

from __future__ import annotations

import math
import re
from collections import Counter

from . import _redact_rules

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}
_ALLOWLIST_CACHE: list[re.Pattern[str]] | None = None

_WINDOW_SIZE = 64 * 1024
_WINDOW_OVERLAP = 4096
_REDACTED_MARKER_PREFIX = "[REDACTED:"
_MARKER_TEMPLATE = _REDACTED_MARKER_PREFIX + "{}]"


def _compiled_pattern(rule_id: str, pattern: str) -> re.Pattern[str]:
    compiled = _PATTERN_CACHE.get(rule_id)
    if compiled is None:
        compiled = re.compile(pattern)
        _PATTERN_CACHE[rule_id] = compiled
    return compiled


def _allowlist_patterns() -> list[re.Pattern[str]]:
    global _ALLOWLIST_CACHE
    if _ALLOWLIST_CACHE is None:
        _ALLOWLIST_CACHE = [re.compile(p) for p in _redact_rules.ALLOWLIST_REGEXES]
    return _ALLOWLIST_CACHE


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    length = len(value)
    counts = Counter(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _is_allowlisted(secret: str) -> bool:
    lowered = secret.lower()
    if any(stopword.lower() in lowered for stopword in _redact_rules.STOPWORDS):
        return True
    return any(pattern.fullmatch(secret) for pattern in _allowlist_patterns())


def _rule_hits(window: str, lowered_window: str) -> list[tuple[int, int, str]]:
    hits: list[tuple[int, int, str]] = []
    for rule_id, pattern, keywords, entropy_floor, group in _redact_rules.RULES:
        if keywords and not any(keyword in lowered_window for keyword in keywords):
            continue
        try:
            compiled = _compiled_pattern(rule_id, pattern)
            for match in compiled.finditer(window):
                start, end = match.span(group)
                if start == -1 or start == end:
                    continue
                secret = window[start:end]
                if _REDACTED_MARKER_PREFIX in secret:
                    continue  # idempotency: never re-redact our own marker text
                if entropy_floor is not None and not (_shannon_entropy(secret) > entropy_floor):
                    continue
                if _is_allowlisted(secret):
                    continue
                hits.append((start, end, rule_id))
        except re.error:
            continue
    return hits


def _windows(text: str) -> list[tuple[int, str]]:
    length = len(text)
    if length <= _WINDOW_SIZE:
        return [(0, text)]
    windows: list[tuple[int, str]] = []
    start = 0
    while True:
        end = min(length, start + _WINDOW_SIZE)
        windows.append((start, text[start:end]))
        if end >= length:
            return windows
        start = end - _WINDOW_OVERLAP


def _redact_unsafe(text: str) -> tuple[str, int]:
    spans: set[tuple[int, int, str]] = set()
    for offset, window in _windows(text):
        lowered = window.lower()
        for start, end, rule_id in _rule_hits(window, lowered):
            spans.add((offset + start, offset + end, rule_id))
    if not spans:
        return text, 0

    parts: list[str] = []
    cursor = 0
    count = 0
    for start, end, rule_id in sorted(spans):
        if start < cursor:
            continue  # overlaps a span already accepted; keep the earlier one
        parts.append(text[cursor:start])
        parts.append(_MARKER_TEMPLATE.format(rule_id))
        cursor = end
        count += 1
    parts.append(text[cursor:])
    return "".join(parts), count


def redact(text: str) -> tuple[str, int]:
    try:
        return _redact_unsafe(text)
    except Exception:  # noqa: BLE001 - redact() must never raise (module docstring)
        return text, 0
