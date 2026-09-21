"""Hard-negative generators for tests/fixtures/secrets_corpus.jsonl (fix round 1, item 2).

An independent reviewer's held-out probe set (different lockfile/hash/asset
formats than this repo's own negative generators) found the first corpus's
negatives were too narrow and the `local-high-entropy` exclusions were
tuned to their exact literal shape. This module widens the negative
category list; `plugin/hooks/verdict_hot/_redact_filters.py` carries the
corresponding *structural* (not shape-specific) defenses.

Every generator takes the shared `random.Random` instance and returns
realistic non-secret text. Nothing here is copied from a real project;
formats are reproduced generically (a go.sum line shape, a Cargo.lock
checksum shape, ...), never sourced from an actual dependency tree.
"""

from __future__ import annotations

import random
from collections.abc import Callable

_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_HEX_LOWER = "0123456789abcdef"
_B64 = _ALNUM + "+/"
_B64URL = _ALNUM + "-_"


def _rand_chars(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def _neg_git_sha(rng: random.Random) -> str:
    sha = _rand_chars(rng, _HEX_LOWER, 40)
    return f"commit {sha}\nAuthor: dev <dev@example.com>\n\n    fix: tighten validation"


def _neg_uuid(rng: random.Random) -> str:
    hexs = _rand_chars(rng, _HEX_LOWER, 32)
    uuid = f"{hexs[0:8]}-{hexs[8:12]}-{hexs[12:16]}-{hexs[16:20]}-{hexs[20:32]}"
    return f'{{"request_id": "{uuid}", "status": "ok"}}'


def _neg_uuid_in_url(rng: random.Random) -> str:
    hexs = _rand_chars(rng, _HEX_LOWER, 32)
    uuid = f"{hexs[0:8]}-{hexs[8:12]}-{hexs[12:16]}-{hexs[16:20]}-{hexs[20:32]}"
    return f"GET /api/v1/users/{uuid}/profile 200 14ms"


def _neg_lockfile_hash(rng: random.Random) -> str:
    algo = rng.choice(["sha512", "sha256", "sha1"])
    body = _rand_chars(rng, _B64, rng.choice([28, 44, 88]))
    pkg = rng.choice(["lodash", "react", "chalk", "typescript"])
    return (
        f'  "{pkg}": {{\n'
        f'    "version": "1.{rng.randint(0, 9)}.{rng.randint(0, 20)}",\n'
        f'    "resolved": "https://registry.npmjs.org/{pkg}/-/{pkg}-1.0.0.tgz",\n'
        f'    "integrity": "{algo}-{body}=="\n'
        "  }"
    )


def _neg_go_sum(rng: random.Random) -> str:
    pkg = rng.choice(["github.com/pkg/errors", "golang.org/x/sync", "github.com/spf13/cobra"])
    version = f"v{rng.randint(0, 2)}.{rng.randint(0, 20)}.{rng.randint(0, 9)}"
    body = _rand_chars(rng, _B64, 27) + "="
    return f"{pkg} {version} h1:{body}\n{pkg} {version}/go.mod h1:{body}"


def _neg_cargo_lock_checksum(rng: random.Random) -> str:
    crate = rng.choice(["serde", "tokio", "regex", "clap"])
    version = f"{rng.randint(0, 2)}.{rng.randint(0, 20)}.{rng.randint(0, 9)}"
    checksum = _rand_chars(rng, _HEX_LOWER, 64)
    return (
        "[[package]]\n"
        f'name = "{crate}"\n'
        f'version = "{version}"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        f'checksum = "{checksum}"'
    )


def _neg_pip_hash(rng: random.Random) -> str:
    pkg = rng.choice(["requests", "numpy", "flask", "pydantic"])
    version = f"{rng.randint(0, 3)}.{rng.randint(0, 30)}.{rng.randint(0, 9)}"
    digest = _rand_chars(rng, _HEX_LOWER, 64)
    return f"{pkg}=={version} \\\n    --hash=sha256:{digest}"


def _neg_docker_digest(rng: random.Random) -> str:
    digest = _rand_chars(rng, _HEX_LOWER, 64)
    image = rng.choice(["node", "python", "postgres", "nginx"])
    return f"{image}@sha256:{digest}  Pulling fs layer"


def _neg_etag(rng: random.Random) -> str:
    etag = _rand_chars(rng, _HEX_LOWER, 32)
    return f'HTTP/1.1 200 OK\nETag: "{etag}"\nCache-Control: max-age=3600'


def _neg_base64_asset_labeled(rng: random.Random) -> str:
    body = _rand_chars(rng, _B64, rng.randint(60, 140))
    kind = rng.choice(
        [
            ("image/png", "png"),
            ("image/jpeg", "jpg"),
            ("font/woff2", "woff2"),
            ("application/wasm", "wasm"),
        ]
    )
    mime, _ext = kind
    return f'<img src="data:{mime};base64,{body}==" alt="asset" />'


def _neg_base64_asset_bare(rng: random.Random) -> str:
    """A base64 asset blob with no `data:...;base64,` URI prefix.

    A third of the time it is passed straight through JavaScript's `atob()`
    (a real, extremely common adjacent convention for "this is base64" with
    no literal word "base64" in sight); the rest have no cue of any kind --
    the genuinely hard case D-026 exists for. Per D-026, this whole category
    is an `opaque_blob` negative: over-redaction here is reported and
    bounded (<=0.30), not required near-zero like text negatives, because no
    text-only signal distinguishes a fully unlabeled high-entropy asset blob
    from a real high-entropy secret -- both are drawn from the same
    statistical distribution.
    """
    body = _rand_chars(rng, _B64, rng.randint(60, 140)) + "=="
    variant = rng.choice(("atob", "atob", "commented", "bare"))
    if variant == "atob":
        quote = rng.choice(['"', "'"])
        return f"const decoded = atob({quote}{body}{quote});"
    if variant == "commented":
        kind = rng.choice(["wasm module", "font glyph table", "icon atlas"])
        return f"// preloaded {kind} follows\nconst BLOB_{rng.randint(1, 99)} = '{body}';"
    return f"const DATA_{rng.randint(1, 99)} = '{body}';"


def _neg_base64_sourcemap(rng: random.Random) -> str:
    """An inline source map data URI (opaque_blob, D-026), per a bundler's
    `//# sourceMappingURL=` comment. Real inline source maps are always
    base64-encoded (an earlier draft of this generator also produced an
    unrealistic `;charset=utf-8,` variant with a base64 body, which real
    tooling never emits -- a `charset=utf-8` data URI is percent-encoded
    text, not base64 -- fixed here rather than kept as an easy category)."""
    body = _rand_chars(rng, _B64, rng.randint(80, 160)) + "=="
    mime = rng.choice(["application/json", "application/json;charset=utf-8"])
    return f"//# sourceMappingURL=data:{mime};base64,{body}"


def _neg_base64_protobuf(rng: random.Random) -> str:
    """A base64-encoded protobuf/gRPC-web payload (opaque_blob, D-026).

    Half use the realistic `data:application/x-protobuf;base64,` data-URI
    form (real, and adjacent to the same `base64,` cue as other assets);
    half are a bare log-style field with no adjacent cue at all -- the
    genuinely hard case, same as `base64-asset-bare`.
    """
    body = _rand_chars(rng, _B64, rng.randint(60, 140)) + "=="
    if rng.random() < 0.7:
        return f"payload: data:application/x-protobuf;base64,{body}"
    return f"const RESPONSE_BYTES = '{body}';"


def _neg_csp_nonce(rng: random.Random) -> str:
    """CSP script-src nonce (opaque_token, D-027 item 2): a public,
    single-use value the browser uses to allow one inline script; not a
    secret, but random-looking with no word structure."""
    nonce = _rand_chars(rng, _B64, rng.randint(22, 32))
    quote = rng.choice(['"', "'"])
    return f"Content-Security-Policy: script-src {quote}nonce-{nonce}{quote}"


def _neg_csrf_hidden_field(rng: random.Random) -> str:
    """CSRF hidden form field (opaque_token, D-027 item 2): public
    (delivered to the browser), random-looking, no word structure."""
    token = _rand_chars(rng, _ALNUM, rng.randint(32, 64))
    name = rng.choice(["csrf_token", "csrfmiddlewaretoken", "_csrf", "authenticity_token"])
    return f'<input type="hidden" name="{name}" value="{token}">'


def _neg_pagination_cursor(rng: random.Random) -> str:
    """Opaque pagination cursor in a JSON API response (opaque_token, D-027
    item 2): random-looking, no word structure, not a secret."""
    cursor = _rand_chars(rng, _B64URL, rng.randint(28, 48))
    field = rng.choice(["next_cursor", "cursor", "page_token"])
    return f'{{"{field}": "{cursor}", "has_more": true}}'


def _neg_idempotency_key(rng: random.Random) -> str:
    """Idempotency-Key request header (opaque_token, D-027 item 2): a
    client-generated random value, not a secret."""
    key = f"{_rand_chars(rng, _HEX_LOWER, 8)}-{_rand_chars(rng, _HEX_LOWER, 4)}-" + _rand_chars(
        rng, _HEX_LOWER, 20
    )
    return f"Idempotency-Key: {key}"


def _neg_stripe_publishable_key(rng: random.Random) -> str:
    """Stripe PUBLISHABLE key (opaque_token, D-027 item 2): intentionally
    public, meant to be embedded client-side -- not a secret."""
    env = rng.choice(["live", "test"])
    body = _rand_chars(rng, _ALNUM, 24)
    return f"STRIPE_PUBLISHABLE_KEY=pk_{env}_{body}"


def _neg_jwks_key(rng: random.Random) -> str:
    """A JWKS public key entry (opaque_token, D-027 item 2): the modulus
    `n` and key id `kid` are public by definition (that is the point of a
    JWKS endpoint) even though they are random-looking base64url."""
    n = _rand_chars(rng, _B64URL, rng.randint(340, 350))
    kid = _rand_chars(rng, _HEX_LOWER, 32)
    return f'{{"kty":"RSA","n":"{n}","e":"AQAB","kid":"{kid}","use":"sig"}}'


def _neg_password_hash_sql_dump(rng: random.Random) -> str:
    """A password HASH (bcrypt/argon2) in a SQL dump (opaque_token, D-027
    item 2): a one-way hash, not the password itself -- the whole point of
    hashing is that this value grants no access on its own."""
    algo = rng.choice(["bcrypt", "argon2"])
    if algo == "bcrypt":
        prefix = rng.choice(["$2a$", "$2b$", "$2y$"])
        hashed = prefix + "12$" + _rand_chars(rng, _ALNUM + "./", 53)
    else:
        salt = _rand_chars(rng, _B64, 22)
        digest = _rand_chars(rng, _B64, 43)
        hashed = f"$argon2id$v=19$m=65536,t=3,p=4${salt}${digest}"
    email = f"user{rng.randint(1, 999)}@example.com"
    return f"INSERT INTO users (email, password) VALUES ('{email}', '{hashed}');"


def _neg_jupyter_image_output(rng: random.Random) -> str:
    """A Jupyter notebook cell's rendered PNG output (opaque_token, D-027
    item 2): compressed binary image data, not a secret."""
    body = _rand_chars(rng, _B64, rng.randint(80, 160)) + "=="
    return f'{{"output_type": "display_data", "data": {{"image/png": "{body}"}}}}'


def _neg_targz_base64(rng: random.Random) -> str:
    """A base64-encoded tar.gz artifact (opaque_token, D-027 item 2):
    compressed binary data, not a secret."""
    body = _rand_chars(rng, _B64, rng.randint(80, 160)) + "=="
    return f"ARTIFACT_TARBALL_BASE64={body}"


def _neg_request_trace_id(rng: random.Random) -> str:
    """A request/trace id header (opaque_token, D-027 item 2): random,
    no word structure, identifies a request for correlation -- not a
    secret."""
    header = rng.choice(["X-Request-Id", "X-Trace-Id", "X-Correlation-Id"])
    value = _rand_chars(rng, _HEX_LOWER, 32)
    return f"{header}: {value}"


# Categories whose over-redaction is reported and bounded separately
# (D-026) rather than folded into the text false-positive rate. Set from
# the category, never from whether the redactor happens to fire on a given
# row -- see gen_corpus.py's `_make_negative`.
EVIDENCE_TEXT = "evidence_text"
OPAQUE_TOKEN = "opaque_token"

# D-027 (amends D-026): every negative category is classified by HARM, not
# by outcome. Evidence text is anything a person reads meaning from (prose,
# errors, commands, paths, word-shaped identifiers, placeholders, ordinary
# env lines, secret NAMES, version strings, URLs with slugs) -- redacting
# it damages the ledger's purpose, so it is gated at <=0.02 FPR. An opaque
# token is a random-looking string of 20+ chars with no word structure
# (nonces, CSRF values, cursors, idempotency keys, request/trace ids,
# bcrypt/JWKS material, publishable keys, base64 blobs) -- redacting it
# removes nothing a verifier or labeler uses, so over-redaction is reported
# and bounded, not gated to near-zero. Standard-shape digests (git SHAs,
# sha256/sha512 hex, lockfile integrity, go.sum, docker digests, ETags,
# UUIDs) and SSH/PEM public material are evidence_text because they MUST
# survive redaction.
OPAQUE_TOKEN_CATEGORIES: frozenset[str] = frozenset(
    {
        "base64-asset-labeled",
        "base64-asset-bare",
        "base64-sourcemap",
        "base64-protobuf",
        "csp-nonce",
        "csrf-hidden-field",
        "pagination-cursor",
        "idempotency-key",
        "stripe-publishable-key",
        "jwks-key",
        "password-hash-sql-dump",
        "jupyter-image-output",
        "targz-base64",
        "request-trace-id",
    }
)


def neg_class_for_category(category: str) -> str:
    return OPAQUE_TOKEN if category in OPAQUE_TOKEN_CATEGORIES else EVIDENCE_TEXT


def _neg_python_traceback_address(rng: random.Random) -> str:
    addr = "".join(rng.choice(_HEX_LOWER) for _ in range(12))
    return (
        "Traceback (most recent call last):\n"
        f'  File "app/worker.py", line {rng.randint(10, 900)}, in run\n'
        f"    <ConnectionPool object at 0x{addr}> closed unexpectedly\n"
        "RuntimeError: pool exhausted"
    )


def _neg_long_java_identifier(rng: random.Random) -> str:
    segments = rng.sample(
        [
            "com",
            "example",
            "myapp",
            "service",
            "impl",
            "handler",
            "internal",
            "gateway",
            "adapter",
            "orchestration",
        ],
        k=rng.randint(5, 8),
    )
    method = rng.choice(
        ["processRequestAsynchronously", "validateAndPersistEntity", "resolveDependencyGraph"]
    )
    return f"at {'.'.join(segments)}.{method}(Handler.java:{rng.randint(10, 500)})"


def _neg_minified_js(rng: random.Random) -> str:
    vars_ = ["a", "b", "c", "d", "e", "n", "t", "r", "o", "i"]
    parts = []
    for _ in range(12):
        v = rng.choice(vars_)
        parts.append(f"{v}={rng.choice(vars_)}.{rng.choice(['call', 'apply', 'bind'])}(this)")
    return ";".join(parts) + ";return " + rng.choice(vars_) + "}"


def _neg_long_url_slug(rng: random.Random) -> str:
    words = rng.sample(
        [
            "how-we-scaled",
            "our-infrastructure",
            "to-handle",
            "ten-million-requests",
            "per-day",
            "without",
            "downtime",
            "a-retrospective",
        ],
        k=rng.randint(4, 6),
    )
    year = rng.randint(2020, 2026)
    month = rng.randint(1, 12)
    return f"GET /blog/{year}/{month:02d}/{'-'.join(words)} 200 8ms"


def _neg_env_ordinary(rng: random.Random) -> str:
    templates = [
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "NODE_ENV=production",
        "LANG=en_US.UTF-8",
        "PASSWORD_MIN_LENGTH=12",
        "SECRET_NAME=prod-db-credentials",
        "LOG_LEVEL=info",
        "MAX_RETRY_COUNT=5",
        "SESSION_TIMEOUT_SECONDS=1800",
    ]
    return "\n".join(rng.sample(templates, k=rng.randint(2, 4)))


def _neg_env_placeholder(rng: random.Random) -> str:
    key = rng.choice(["API_KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH_TOKEN"])
    value = rng.choice(
        ["changeme", "your-api-key-here", "xxxxxxxx", "<token>", "${VAR}", "replace_me"]
    )
    return f"# .env.example\n{key}={value}"


def _neg_toml_yaml_placeholder(rng: random.Random) -> str:
    """TOML/YAML config with a placeholder value (evidence text, D-027)."""
    value = rng.choice(
        ["your-api-key-here", "changeme", "xxxxxxxx", "replace_me", "CHANGE_ME", "example"]
    )
    if rng.random() < 0.5:
        key = rng.choice(["api_key", "token", "password"])
        return f'{key} = "{value}"'
    key = rng.choice(["api_key", "token", "password"])
    return f"{key}: {value}"


def _neg_env_example_url_placeholder(rng: random.Random) -> str:
    """`.env.example` URL with placeholder (not real) credentials (evidence
    text, D-027): a person reads "user"/"password" as literal placeholders,
    not as a real secret."""
    return "# .env.example\nDATABASE_URL=postgres://user:password@localhost:5432/mydb"


def _neg_file_path(rng: random.Random) -> str:
    segments = rng.sample(
        [
            "components",
            "hooks",
            "verdict",
            "hot",
            "utils",
            "internal",
            "nested",
            "deeply",
            "module",
            "core",
            "shared",
            "vendor",
        ],
        k=rng.randint(4, 7),
    )
    path = "/Users/dev/Projects/app/src/" + "/".join(segments) + "/index.ts"
    return f"Compiling {path} ... 240 modules transformed."


def _neg_hex_color(rng: random.Random) -> str:
    color = _rand_chars(rng, _HEX_LOWER, rng.choice([3, 6]))
    return f"--color-accent: #{color}; /* brand accent */"


_SSH_KEY_TYPES = (
    ("ssh-rsa", "AAAAB3NzaC1yc2EAAAADAQABAAABgQ"),
    ("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAI"),
    ("ssh-dss", "AAAAB3NzaC1kc3MAAACBAP"),
    ("ecdsa-sha2-nistp256", "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABB"),
    ("ecdsa-sha2-nistp384", "AAAAE2VjZHNhLXNoYTItbmlzdHAzODQAAAAIbmlzdHAzODQAAABh"),
    ("ecdsa-sha2-nistp521", "AAAAE2VjZHNhLXNoYTItbmlzdHA1MjEAAAAIbmlzdHA1MjEAAACF"),
    ("sk-ssh-ed25519@openssh.com", "AAAAGnNrLXNzaC1lZDI1NTE5QG9wZW5zc2guY29tAAAAI"),
)


def _neg_ssh_public_key(rng: random.Random) -> str:
    key_type, prefix = rng.choice(_SSH_KEY_TYPES)
    body = prefix + _rand_chars(rng, _B64, rng.randint(40, 90))
    user = rng.choice(["deploy", "ci", "runner"])
    host = rng.choice(["build-01", "laptop", "workstation"])
    return f"{key_type} {body} {user}@{host}"


def _neg_known_hosts_line(rng: random.Random) -> str:
    key_type, prefix = rng.choice(_SSH_KEY_TYPES)
    body = prefix + _rand_chars(rng, _B64, rng.randint(40, 90))
    host = rng.choice(["github.com", "gitlab.com", "bitbucket.org"])
    if rng.random() < 0.5:
        salt = _rand_chars(rng, _B64, 20)
        digest = _rand_chars(rng, _B64, 27)
        return f"|1|{salt}=|{digest}= {key_type} {body}"
    return f"{host} {key_type} {body}"


def _neg_authorized_keys_line(rng: random.Random) -> str:
    key_type, prefix = rng.choice(_SSH_KEY_TYPES)
    body = prefix + _rand_chars(rng, _B64, rng.randint(40, 90))
    comment = rng.choice(["deploy@ci", "backup-key", "jenkins"])
    if rng.random() < 0.5:
        return f'command="/usr/local/bin/deploy.sh" {key_type} {body} {comment}'
    return f"{key_type} {body} {comment}"


def _neg_pem_public_key(rng: random.Random) -> str:
    lines = ["-----BEGIN PUBLIC KEY-----"]
    for _ in range(6):
        lines.append(_rand_chars(rng, _B64, 64))
    lines.append("-----END PUBLIC KEY-----")
    return "\n".join(lines)


def _neg_pem_certificate(rng: random.Random) -> str:
    lines = ["-----BEGIN CERTIFICATE-----"]
    for _ in range(14):
        lines.append(_rand_chars(rng, _B64, 64))
    lines.append("-----END CERTIFICATE-----")
    return "\n".join(lines)


_PROSE_CREDENTIAL_SENTENCES = (
    "Please enter your password when prompted by the login screen.",
    "The API key must be included in the Authorization header of every request.",
    "This document explains how bearer tokens are validated by the gateway.",
    "Rotate the secret before the certificate expires to avoid downtime.",
    "A credential is any piece of information used to authenticate a user.",
    "The password field should never be logged in plaintext.",
    "Tokens issued by the auth server expire after one hour.",
    "Store the API key in an environment variable, not in source control.",
    "The bearer scheme is defined in RFC 6750.",
    "A weak password is one of the most common security vulnerabilities.",
    "This guide covers how to generate a new access token from the dashboard.",
    "Credential stuffing attacks reuse leaked username and password pairs.",
    "The service account key file is mounted as a read-only volume.",
    "Users must reset their password every ninety days per policy.",
    "The token endpoint accepts a refresh token and returns a new access token.",
    "Never share your secret key with anyone, including support staff.",
    "The bearer token grants temporary access to the requested resource.",
    "A strong password combines letters, numbers, and symbols.",
    "This function validates the API key format before making a request.",
    "Credential rotation is scheduled to run nightly at midnight.",
    "The login form asks for a username and a password.",
    "Secret management tools help teams avoid hardcoding credentials.",
    "The key exchange protocol negotiates a shared secret between peers.",
    "Password managers can generate and store strong, unique passwords.",
    "An API key identifies the calling application, not the end user.",
    "The token is opaque and should not be parsed by the client.",
    "A hint about the password prompt appears after three failed attempts.",
    "The word password is easy to guess, so please avoid it.",
    "This page is password protected and requires a valid session.",
    "Enter your credential whenever the system requests one.",
)


def _neg_prose_credential_vocab(rng: random.Random) -> str:
    return " ".join(rng.sample(_PROSE_CREDENTIAL_SENTENCES, k=rng.randint(2, 3)))


def _neg_prose(rng: random.Random) -> str:
    sentences = [
        "The build completed without warnings.",
        "All 42 tests passed in 3.2 seconds.",
        "Refactored the pagination helper for clarity.",
        "No lint errors were found in the changed files.",
        "The deployment finished and health checks are green.",
        "Reviewed the pull request and left two comments.",
        "Cache was warm, so the request completed quickly.",
        "The scheduled job ran successfully at midnight.",
    ]
    return " ".join(rng.sample(sentences, k=rng.randint(2, 4)))


NEGATIVE_CATEGORIES: dict[str, Callable[[random.Random], str]] = {
    "git-sha": _neg_git_sha,
    "uuid": _neg_uuid,
    "uuid-in-url": _neg_uuid_in_url,
    "lockfile-hash": _neg_lockfile_hash,
    "go-sum": _neg_go_sum,
    "cargo-lock-checksum": _neg_cargo_lock_checksum,
    "pip-hash": _neg_pip_hash,
    "docker-digest": _neg_docker_digest,
    "etag": _neg_etag,
    "base64-asset-labeled": _neg_base64_asset_labeled,
    "base64-asset-bare": _neg_base64_asset_bare,
    "base64-sourcemap": _neg_base64_sourcemap,
    "base64-protobuf": _neg_base64_protobuf,
    "python-traceback-address": _neg_python_traceback_address,
    "long-java-identifier": _neg_long_java_identifier,
    "minified-js": _neg_minified_js,
    "long-url-slug": _neg_long_url_slug,
    "env-ordinary": _neg_env_ordinary,
    "env-placeholder": _neg_env_placeholder,
    "file-path": _neg_file_path,
    "hex-color": _neg_hex_color,
    "prose": _neg_prose,
    "prose-credential-vocab": _neg_prose_credential_vocab,
    "ssh-public-key": _neg_ssh_public_key,
    "known-hosts-line": _neg_known_hosts_line,
    "authorized-keys-line": _neg_authorized_keys_line,
    "pem-public-key": _neg_pem_public_key,
    "pem-certificate": _neg_pem_certificate,
    "toml-yaml-placeholder": _neg_toml_yaml_placeholder,
    "env-example-url-placeholder": _neg_env_example_url_placeholder,
    "csp-nonce": _neg_csp_nonce,
    "csrf-hidden-field": _neg_csrf_hidden_field,
    "pagination-cursor": _neg_pagination_cursor,
    "idempotency-key": _neg_idempotency_key,
    "stripe-publishable-key": _neg_stripe_publishable_key,
    "jwks-key": _neg_jwks_key,
    "password-hash-sql-dump": _neg_password_hash_sql_dump,
    "jupyter-image-output": _neg_jupyter_image_output,
    "targz-base64": _neg_targz_base64,
    "request-trace-id": _neg_request_trace_id,
}

# Per-category negative-row-count overrides (default set by the caller).
# The prose-with-credential-vocabulary category needs >=24 varied rows
# (fix round 2, item 3) to be a meaningful 0-FP bar on its own.
NEGATIVE_COUNT_OVERRIDES: dict[str, int] = {
    "prose-credential-vocab": 24,
}
