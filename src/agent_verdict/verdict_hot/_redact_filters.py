"""Structural false-positive filters for the local rules (fix round 1, item 2).

The first cut of `local-high-entropy` excluded lockfile hashes and base64
image data with lookbehinds that required an exact literal prefix
(`sha256-`, `base64,`, ...) immediately before the match. A held-out probe
set with different hash/lockfile formats (Go `go.sum`, Cargo.lock, pip
`--hash=sha256:`, docker digests, ...) showed those lookbehinds were
overfit to this repo's own negative-corpus generators rather than a real
defense. This module replaces them with structural checks that do not
enumerate specific negative shapes:

- standard hex-digest lengths (md5/sha1/sha256/sha384/sha512/etc.) are never
  high-entropy secrets regardless of context;
- a real secret's high-entropy run almost always mixes multiple character
  classes (a pure-hex or pure-lowercase run is far more likely to be a hash
  or a slug than a generated credential);
- values built from 2+ dictionary-like words joined by `-`/`_` (a secret's
  *name*, e.g. `prod-db-credentials`, or a resource slug) are not secret
  *values*;
- obvious placeholders (`changeme`, `<token>`, `${VAR}`, `xxxxxxxx`, ...)
  from `.env.example`-style files are not secrets;
- a nearby (within ~40 chars before the match) hash/digest/asset vocabulary
  word (`sha256`, `integrity`, `checksum`, `base64`, `wasm`, ...) means the
  value is conventionally being announced as a hash or an embedded asset,
  not a credential.

None of these depend on the exact literal formatting of any specific
negative-corpus generator, so a differently-formatted held-out probe should
be caught the same way a locally-generated one is.
"""

from __future__ import annotations

import re

_DIGEST_HEX_LENGTHS = frozenset({32, 40, 56, 64, 96, 128})
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

_PLACEHOLDER_EXACT = frozenset(
    {
        "changeme",
        "change_me",
        "changethis",
        "yourapikeyhere",
        "your-api-key-here",
        "your_api_key_here",
        "your_token_here",
        "insert_key_here",
        "replace_me",
        "some_password",
        "placeholder",
        "example",
        "dummy",
        "sample",
        "todo",
        "fixme",
        "test",
        "123456",
        "password",
    }
)

_HASH_ASSET_CONTEXT_RE = re.compile(
    r"(?i)\b(?:sha1|sha256|sha384|sha512|sha3|md5|blake2b?|h1|h2|integrity|"
    r"checksum|digest|hash|base64|wasm|font|woff2?|icon|glyph|atlas)\b"
)

_ENTROPY_RULE_IDS = frozenset({"local-high-entropy-alnum", "local-high-entropy-b64"})
# generic-api-key is gitleaks' own "keyword + separator + freeform value"
# catch-all (the same shape as our local assignment rules), so a value like
# `SECRET_NAME=prod-db-credentials` needs the same dictionary-word/placeholder
# filtering. Rules with a *fixed* prefix baked into the same capturing group
# (stripe-access-token's group is the whole `sk_live_...` token, not just the
# suffix) must NOT get this filter: splitting "sk_live_<suffix>" on "_" finds
# two short lowercase words ("sk", "live") and would wrongly reject a real
# secret.
_ASSIGNMENT_RULE_IDS = frozenset(
    {"local-env-secret", "local-password-assignment", "generic-api-key"}
)


def is_standard_hex_digest(value: str) -> bool:
    """True for pure-hex runs at a conventional digest length (never a secret)."""
    return len(value) in _DIGEST_HEX_LENGTHS and all(c in _HEX_DIGITS for c in value)


def has_mixed_character_classes(value: str, minimum: int = 2) -> bool:
    classes = 0
    if any(c.islower() for c in value):
        classes += 1
    if any(c.isupper() for c in value):
        classes += 1
    if any(c.isdigit() for c in value):
        classes += 1
    if any(not c.isalnum() for c in value):
        classes += 1
    return classes >= minimum


def looks_like_dictionary_words(value: str) -> bool:
    """True for values like `prod-db-credentials`: a name, not a secret."""
    parts = re.split(r"[-_]", value)
    if len(parts) < 3:
        return False
    lowercase_words = [p for p in parts if p.isalpha() and p.islower() and len(p) >= 2]
    return len(lowercase_words) >= 2


def looks_like_placeholder(value: str) -> bool:
    stripped = value.strip()
    if stripped.lower() in _PLACEHOLDER_EXACT:
        return True
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    if stripped.startswith("${") and stripped.endswith("}"):
        return True
    if stripped.startswith("{{") and stripped.endswith("}}"):
        return True
    return len(stripped) >= 4 and set(stripped.lower()) <= {"x"}


def has_nearby_hash_or_asset_context(preceding_text: str) -> bool:
    return _HASH_ASSET_CONTEXT_RE.search(preceding_text) is not None


def passes_local_filters(rule_id: str, secret: str, preceding_text: str) -> bool:
    """Dispatch to the filter set for `rule_id`; vendored rules are untouched."""
    if rule_id in _ENTROPY_RULE_IDS:
        if is_standard_hex_digest(secret):
            return False
        if not has_mixed_character_classes(secret):
            return False
        if looks_like_dictionary_words(secret):
            return False
        if looks_like_placeholder(secret):
            return False
        return not has_nearby_hash_or_asset_context(preceding_text)
    if rule_id in _ASSIGNMENT_RULE_IDS:
        if looks_like_placeholder(secret):
            return False
        return not looks_like_dictionary_words(secret)
    return True
