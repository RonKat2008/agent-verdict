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
candidate. `has_nearby_public_token_cue` is adjacency-only: the candidate
must be IMMEDIATELY preceded (no free text in between) by one of a fixed
set of real cue strings. The same round added SSH-public-key and PEM
public-key/certificate exclusions, and a "plain English word" check for
`local-password-assignment`.

Fix round 3 (D-027, docs/DECISIONS.md): splits false positives by harm
(evidence text vs. opaque tokens). Extends the placeholder/dictionary-word
filter to every rule whose secret is a freeform "keyword + separator +
value", not just the three hand-picked ids -- `_redact_rules.
ASSIGNMENT_LIKE_RULE_IDS` is generated structurally (by pattern shape, not
by rule id) so it covers the whole class the round's brief pointed at with
"for example hashicorp-tf-password" -- and adds well-known PUBLIC token
conventions (CSP nonces, CSRF fields, bcrypt/argon2 prefixes, pagination
cursors, Stripe publishable keys, JWKS fields, Jupyter/idempotency
markers) to the adjacency-cue list, all still entropy-rule-only.
"""

from __future__ import annotations

import re

from . import _redact_rules

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
        "pass",
        "user",
        "username",
        "admin",
    }
)

# Adjacency-only cues (fix round 2 item 2, fix round 3 item 4): the
# candidate must be an EXACT suffix match against one of these immediately
# before its start, with no free text in between. Each is a real, specific
# convention -- hash/lockfile shapes ("sha512-<base64>", "h1:<base64>",
# "base64,<blob>", "etag: "<hex>"", "@sha256:<hex>") and, as of round 3,
# well-known PUBLIC token conventions (a CSP nonce, a CSRF hidden-field
# value, a bcrypt/argon2 hash prefix, a pagination cursor, a Stripe
# publishable key prefix, a JWKS field, a Jupyter image output, an
# Idempotency-Key header) -- never a word that might appear anywhere on
# the same line.
_ADJACENT_PUBLIC_TOKEN_CUES: tuple[str, ...] = (
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
    "nonce-",
    "'nonce-",
    'csrfmiddlewaretoken" value="',
    'name="csrf_token" value="',
    "$2a$",
    "$2b$",
    "$2y$",
    "$argon2",
    '"next_cursor":"',
    '"cursor":"',
    '"page_token":"',
    "pk_live_",
    "pk_test_",
    '"n":"',
    '"kid":"',
    '"image/png":"',
    '"image/jpeg":"',
    # Pretty-printed JSON (nbformat notebooks, indented API payloads) puts a
    # space after the colon; compact/wire JSON does not. Cover both.
    '"image/png": "',
    '"image/jpeg": "',
    '"next_cursor": "',
    '"cursor": "',
    '"page_token": "',
    '"n": "',
    '"kid": "',
    "Idempotency-Key: ",
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

# Values that are public by convention (fix round 4): Stripe publishable
# keys are documented as shippable in client code. Fix round 5 replaced the
# prefix test this started as -- any tail could ride the prefix, so
# `password = "pk_live_<30 random>"` leaked -- with Stripe's full real
# shape, matched end to end. Secret-side prefixes (`sk_live_`, `rk_live_`)
# are deliberately absent.
_PUBLISHABLE_KEY_RE = re.compile(r"pk_(?:live|test)_[A-Za-z0-9]{24,}")

# Contexts where the exemption above is never granted, however well-formed
# the value looks: a publishable key is not a database password, so a value
# sitting in a URL credential or after a password-named key is a secret
# someone pasted into the wrong field, not a publishable key (fix round 5).
_NEVER_PUBLIC_RULE_IDS = frozenset({"local-url-credential", "local-password-assignment"})
_PASSWORD_ASSIGNMENT_RE = re.compile(r"(?:password|passwd|pwd)\W{0,6}$", re.IGNORECASE)

# Lowercase words joined by hyphens (`keyboard-interactive`,
# `gssapi-with-mic`): vocabulary, never a secret (fix round 5). The
# vendored `generic-api-key` rule accepts `,` as a keyword/value separator,
# so every hyphenated word after "password" in an SSH or PAM auth-method
# list was being captured as a value.
_HYPHENATED_WORDS_RE = re.compile(r"[a-z]{2,}(?:-[a-z]{2,})+")

# Fix round 3, item 3: placeholder/dictionary-word/plain-word filtering now
# applies to every rule whose captured secret is a freeform "password-like"
# value -- the three hand-picked local rules, `local-url-credential` (its
# group is the URL password, exactly this shape), and every vendored rule
# sharing gitleaks' generic keyword+separator+value template (detected
# structurally in gen_redact.py, not by hand-enumerating ids -- covers
# hashicorp-tf-password and the ~95 other rules like it). Rules with a
# FIXED prefix baked into the same capturing group (stripe-access-token's
# group is the whole "sk_live_..." token) are not in this set and must
# never be: splitting "sk_live_<suffix>" on "_" finds two short lowercase
# words ("sk", "live") and would wrongly reject a real secret.
#
# Fix round 4, finding 1(a): a rule whose captured secret must START with a
# fixed literal the rule itself defines (`plaid-api-token`'s `access-`,
# `typeform-api-token`'s `tfp_`, `new-relic-user-api-key`'s `NRAK-`, ...)
# is a STRUCTURED vendor token, not a freeform value, so these filters must
# never apply to it -- `access-sandbox-<uuid>` splits into lowercase words
# and was 100% blind to `looks_like_dictionary_words` before this round.
# The set is computed from the patterns by gen_redact.py, so it tracks the
# vendored rules instead of being hand-enumerated.
_ASSIGNMENT_RULE_IDS = (
    frozenset(
        {"local-env-secret", "local-password-assignment", "generic-api-key", "local-url-credential"}
    )
    | frozenset(_redact_rules.ASSIGNMENT_LIKE_RULE_IDS)
) - frozenset(_redact_rules.LITERAL_PREFIXED_RULE_IDS)


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


_WRAPPING_QUOTES = ('"', "'", "`")


def _unwrap(value: str) -> str:
    """Strip whitespace, then one layer of matching wrapping quotes.

    Some vendored rules capture the value's surrounding quote characters as
    part of the same group (e.g. `hashicorp-tf-password`'s group is
    `("[a-z0-9=_\\-]{8,20}")`, quotes included), so `"changeme"` would not
    match the bare `changeme` placeholder without this (fix round 3).
    """
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in _WRAPPING_QUOTES:
        return stripped[1:-1].strip()
    return stripped


_DIGIT_RUN_RE = re.compile(r"\d{4,}")
_WORD_COVERAGE = 0.5


def _has_mixed_case(value: str) -> bool:
    return any(c.islower() for c in value) and any(c.isupper() for c in value)


def looks_like_dictionary_words(value: str) -> bool:
    """True for values like `prod-db-credentials`: a name, not a secret.

    Fix round 4, finding 1(b): "at least two lowercase alphabetic parts"
    alone rejected 3.9% of random 40-character secrets (6.7% at 64), since a
    long random `[a-z0-9_-]` run splits into parts that are often
    accidentally all-alpha. Three further conditions, all properties of a
    real name and none of a random value, bound that cost:

    - the dictionary-like runs must cover at least half the value;
    - no run of 4 or more digits (a name does not carry one);
    - no mixed-case alternation (a name is written in one case).
    """
    unwrapped = _unwrap(value)
    parts = re.split(r"[-_]", unwrapped)
    if len(parts) < 3:
        return False
    lowercase_words = [p for p in parts if p.isalpha() and p.islower() and len(p) >= 2]
    if len(lowercase_words) < 2:
        return False
    covered = sum(len(word) for word in lowercase_words)
    if covered < _WORD_COVERAGE * len(unwrapped):
        return False
    if _DIGIT_RUN_RE.search(unwrapped):
        return False
    return not _has_mixed_case(unwrapped)


def looks_like_placeholder(value: str) -> bool:
    stripped = _unwrap(value)
    if stripped.lower() in _PLACEHOLDER_EXACT:
        return True
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    if stripped.startswith("${") and stripped.endswith("}"):
        return True
    if stripped.startswith("{{") and stripped.endswith("}}"):
        return True
    return len(stripped) >= 4 and set(stripped.lower()) <= {"x"}


_SENTENCE_PUNCTUATION = ".,;:!?)]}"


def looks_like_plain_english_word(value: str) -> bool:
    """True for a single all-lowercase alphabetic token (fix round 2, item 3):
    "whenever", "prompt", "protected" are never a password value regardless
    of which separator matched them.

    Fix round 4, finding 3: trailing sentence punctuation is stripped
    first, because the word this catches is usually the first word of an
    error message ("password: expired, reset required") and the captured
    value then ends in a comma or a period.
    """
    stripped = _unwrap(value).strip(_SENTENCE_PUNCTUATION)
    return stripped.isalpha() and stripped.islower()


def has_nearby_public_token_cue(preceding_text: str) -> bool:
    """Adjacency-only (fix round 2 item 2, fix round 3 item 4): true only
    when `preceding_text` ENDS with one of the fixed cue strings, i.e. the
    cue is immediately before the candidate with no free text in between."""
    lowered = preceding_text.lower()
    return any(lowered.endswith(cue.lower()) for cue in _ADJACENT_PUBLIC_TOKEN_CUES)


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


def _passes_entropy_filters(secret: str, preceding_text: str, pem_context: str) -> bool:
    if is_standard_hex_digest(secret):
        return False
    if not has_mixed_character_classes(secret):
        return False
    if looks_like_dictionary_words(secret):
        return False
    if looks_like_placeholder(secret):
        return False
    if has_nearby_public_token_cue(preceding_text):
        return False
    if is_ssh_public_key_context(preceding_text):
        return False
    return not is_inside_excluded_pem_block(pem_context)


def is_stripe_publishable_key(value: str) -> bool:
    """True when the WHOLE value is a Stripe publishable key.

    Stripe documents `pk_live_`/`pk_test_` keys as shippable in client
    code, so redacting one removes no secret. The match is anchored at both
    ends (fix round 5): a prefix test let any tail ride the prefix, which
    exempted real secrets such as `pk_live_<30 random chars>` sitting in a
    password field.
    """
    return _PUBLISHABLE_KEY_RE.fullmatch(_unwrap(value)) is not None


def looks_like_hyphenated_words(value: str) -> bool:
    """True for lowercase words joined by hyphens (`keyboard-interactive`).

    Fix round 5: a value made only of lowercase letters and hyphens is
    vocabulary -- an auth-method name, a feature flag, a log token -- not a
    secret, which always carries a digit, a capital or a symbol. This
    extends the plain-English word check across a hyphen, and reaches the
    two-part values the dictionary-word filter (3 parts or more) does not.
    """
    return _HYPHENATED_WORDS_RE.fullmatch(_unwrap(value)) is not None


def _is_public_value(rule_id: str, secret: str, preceding_text: str) -> bool:
    """A value that is public by convention AND is not in a password slot."""
    if rule_id in _NEVER_PUBLIC_RULE_IDS or "password" in rule_id or "passwd" in rule_id:
        return False
    if _PASSWORD_ASSIGNMENT_RE.search(preceding_text):
        return False
    return is_stripe_publishable_key(secret)


def _passes_assignment_filters(rule_id: str, secret: str, preceding_text: str) -> bool:
    if looks_like_plain_english_word(secret):
        return False
    if looks_like_hyphenated_words(secret):
        return False
    if looks_like_placeholder(secret):
        return False
    if _is_public_value(rule_id, secret, preceding_text):
        return False
    return not looks_like_dictionary_words(secret)


def passes_local_filters(
    rule_id: str, secret: str, preceding_text: str, pem_context: str = ""
) -> bool:
    """Dispatch to the filter set for `rule_id`; vendored structured-family
    rules (Stripe, GitHub, AWS, ...) are never in `_ASSIGNMENT_RULE_IDS` or
    `_ENTROPY_RULE_IDS` and always return True here unchanged.

    `preceding_text` is a short (tens of chars) lookback for adjacency
    checks; `pem_context` is a much larger lookback (a few KB) used only to
    detect an open PEM public-key/certificate block.
    """
    if rule_id in _ENTROPY_RULE_IDS:
        return _passes_entropy_filters(secret, preceding_text, pem_context)
    if rule_id in _ASSIGNMENT_RULE_IDS:
        return _passes_assignment_filters(rule_id, secret, preceding_text)
    return True
