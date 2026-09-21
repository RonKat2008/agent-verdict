"""Success-claim sentence extraction (PLAN.md 5.3; task-3-brief.md).

`extract_claims` is code, not a model call (PLAN 5.3: "Claim extraction
(code, not model)"). It splits a message into sentences, keeps the ones
that assert a success verb as a whole word without negating it, prefers
sentences about tests/builds/lint/type-checks/fixes, and caps the result
at `policy.claims.max_claims`.

Negation matters: "not fixed", "did not pass", and "couldn't verify" must
NOT become claims even though (in the first two cases) the sentence
contains a literal success verb. A short lookback window before the
matched verb is checked for a negation cue (task-3-brief.md: "Add tests
for negation").

Like `gates.py`, the policy-supplied `success_verbs` list is compiled
lazily and cached (`functools.cache`), never at import.
"""

from __future__ import annotations

import re
from functools import cache

from .policy import Policy

# Fixed, small, internal-only patterns -- see gates.py's identical rationale
# for compiling these (not the policy-supplied verb list) at import time.
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_TERMINATOR_RE = re.compile(r"[.!?]+")
_EMPHASIS_RE = re.compile(r"(\*\*|\*|__|_|`+)(.+?)\1")

_NEGATION_CUES = (
    "not",
    "never",
    "no longer",
    "cannot",
    "can't",
    "won't",
    "wouldn't",
    "shouldn't",
    "couldn't",
    "didn't",
    "doesn't",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "haven't",
    "hasn't",
    "hadn't",
    "failed to",
    "fails to",
    "unable to",
)
_NEGATION_LOOKBACK_WORDS = 6

_PRIORITY_KEYWORDS = ("test", "build", "lint", "type check", "typecheck", "fix")


@cache
def _verb_patterns(verbs: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(rf"\b{re.escape(verb)}\b", re.IGNORECASE) for verb in verbs)


def _split_sentences(message: str) -> list[str]:
    sentences: list[str] = []
    for line in message.splitlines():
        line = _BULLET_RE.sub("", line)
        for piece in _TERMINATOR_RE.split(line):
            piece = piece.strip()
            if piece:
                sentences.append(piece)
    return sentences


def _strip_markdown(sentence: str) -> str:
    result = sentence
    while True:
        stripped = _EMPHASIS_RE.sub(r"\2", result)
        if stripped == result:
            return result.strip()
        result = stripped


def _is_negated(sentence: str, verb_start: int) -> bool:
    preceding_words = sentence[:verb_start].lower().split()
    window = " ".join(preceding_words[-_NEGATION_LOOKBACK_WORDS:])
    return any(cue in window for cue in _NEGATION_CUES)


def _mentions_priority_topic(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(keyword in lowered for keyword in _PRIORITY_KEYWORDS)


def _asserts_success(cleaned_sentence: str, verbs: tuple[str, ...]) -> bool:
    for pattern in _verb_patterns(verbs):
        match = pattern.search(cleaned_sentence)
        if match and not _is_negated(cleaned_sentence, match.start()):
            return True
    return False


def extract_claims(message: str, policy: Policy) -> tuple[str, ...]:
    candidates: list[tuple[bool, str]] = []
    for raw_sentence in _split_sentences(message):
        cleaned = _strip_markdown(raw_sentence)
        if not cleaned or not _asserts_success(cleaned, policy.claims.success_verbs):
            continue
        candidates.append((_mentions_priority_topic(cleaned), cleaned[:240]))

    # Stable sort: priority (test/build/lint/type-check/fix) sentences
    # first, preserving each group's original relative order.
    candidates.sort(key=lambda item: not item[0])
    capped = candidates[: policy.claims.max_claims]
    return tuple(text for _priority, text in capped)
