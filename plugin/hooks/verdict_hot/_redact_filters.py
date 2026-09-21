"""Structural false-positive/false-negative filters for the local rules.

Fix round 1 replaced literal sha*/base64-prefix lookbehinds (overfit to this
repo's own negative-corpus generators) with structural checks: standard
hex-digest lengths, required mixed character classes, dictionary-word-like
values, obvious placeholders, and a "nearby hash/asset vocabulary word"
search.

Fix round 2 found that last check itself was a false-negative risk: it
searched an entire 72-char lookback for a vocabulary word ANYWHERE, so a
sentence like "sha256 verified. rotated value <secret>" suppressed a real
secret because "sha256" appeared earlier on the line, nowhere near the
candidate. `has_nearby_hash_or_asset_context` is now adjacency-only: the
candidate must be IMMEDIATELY preceded (no free text in between) by one of
a fixed set of real cue strings (`sha256-`, `h1:`, `base64,`, `etag: "`,
...). The same round adds SSH-public-key and PEM public-key/certificate
exclusions (`is_ssh_public_key_context`, `is_inside_excluded_pem_block`),
and a "plain English word" check for `local-password-assignment` (a
lowercase dictionary word after "password" is never a hit, regardless of
which separator matched it).
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

# Adjacency-only hash/digest/asset cues (fix round 2, item 2): the candidate
# must be an EXACT suffix match against one of these immediately before its
# start, with no free text in between. Each is a real, specific convention
# ("sha512-<base64>" lockfile hashes, "h1:<base64>" go.sum lines,
# "base64,<blob>" data URIs, "etag: "<hex>"" response headers, "@sha256:
# <hex>" docker digests) rather than a word that might appear anywhere on
# the same line.
_ADJACENT_HASH_ASSET_CUES: tuple[str, ...] = (
    "sha1-",
    "sha256-",
    "sha384-",
    "sha512-",
    "sha1:",
    "sha256:",
    "sha384:",
    "sha512:",
    "--hash=sha256:",
    "integrity sha512-",
    "h1:",
    "base64,",
    "base64:",
    'etag: "',
    'etag:"',
    "@sha256:",
    'atob("',
    "atob('",
)

# SSH public-key type markers (fix round 2, item 4): the base64 body that
# follows one of these (in a standalone public key file, `known_hosts`, or
# `authorized_keys`) is a public key, never a secret. Private key PEM
# blocks are handled separately (`is_inside_excluded_pem_block`) and by the
# vendored `private-key` rule, which is untouched by this exclusion.
_SSH_PUBLIC_KEY_MARKERS: tuple[str, ...] = (
    "ssh-rsa ",
    "ssh-ed25519 ",
    "ssh-dss ",
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "sk-ssh-ed25519@openssh.com ",
)

_PEM_BEGIN_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]+?)-----")
_PEM_END_RE = re.compile(r"-----END ([A-Z0-9 ]+?)-----")
_EXCLUDED_PEM_TYPES = frozenset({"PUBLIC KEY", "CERTIFICATE"})

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
_PASSWORD_RULE_IDS = frozenset({"local-password-assignment"})


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


def looks_like_plain_english_word(value: str) -> bool:
    """True for a single all-lowercase alphabetic token (fix round 2, item 3):
    "whenever", "prompt", "protected" are never a password value regardless
    of which separator matched them."""
    return value.isalpha() and value.islower()


def has_nearby_hash_or_asset_context(preceding_text: str) -> bool:
    """Adjacency-only (fix round 2, item 2): true only when `preceding_text`
    ENDS with one of the fixed cue strings, i.e. the cue is immediately
    before the candidate with no free text in between."""
    lowered = preceding_text.lower()
    return any(lowered.endswith(cue) for cue in _ADJACENT_HASH_ASSET_CUES)


def is_ssh_public_key_context(preceding_text: str) -> bool:
    return any(preceding_text.endswith(marker) for marker in _SSH_PUBLIC_KEY_MARKERS)


def is_inside_excluded_pem_block(context_before: str) -> bool:
    """True when `context_before` (text before the candidate, bounded to a
    few KB by the caller) ends inside an open `-----BEGIN PUBLIC KEY-----`
    or `-----BEGIN CERTIFICATE-----` block, i.e. the nearest preceding BEGIN
    marker has no matching END marker before the candidate. Private-key
    blocks are not in `_EXCLUDED_PEM_TYPES`: they must still be redacted
    (by the vendored `private-key` rule, untouched here)."""
    last_begin = None
    for match in _PEM_BEGIN_RE.finditer(context_before):
        last_begin = match
    if last_begin is None:
        return False
    tail = context_before[last_begin.end() :]
    if _PEM_END_RE.search(tail):
        return False  # that block already closed before the candidate
    return last_begin.group(1).strip() in _EXCLUDED_PEM_TYPES


def passes_local_filters(
    rule_id: str, secret: str, preceding_text: str, pem_context: str = ""
) -> bool:
    """Dispatch to the filter set for `rule_id`; vendored rules are untouched.

    `preceding_text` is a short (tens of chars) lookback for adjacency
    checks; `pem_context` is a much larger lookback (a few KB) used only to
    detect an open PEM public-key/certificate block.
    """
    if rule_id in _ENTROPY_RULE_IDS:
        if is_standard_hex_digest(secret):
            return False
        if not has_mixed_character_classes(secret):
            return False
        if looks_like_dictionary_words(secret):
            return False
        if looks_like_placeholder(secret):
            return False
        if has_nearby_hash_or_asset_context(preceding_text):
            return False
        if is_ssh_public_key_context(preceding_text):
            return False
        return not is_inside_excluded_pem_block(pem_context)
    if rule_id in _PASSWORD_RULE_IDS:
        if looks_like_plain_english_word(secret):
            return False
        if looks_like_placeholder(secret):
            return False
        return not looks_like_dictionary_words(secret)
    if rule_id in _ASSIGNMENT_RULE_IDS:
        if looks_like_placeholder(secret):
            return False
        return not looks_like_dictionary_words(secret)
    return True
