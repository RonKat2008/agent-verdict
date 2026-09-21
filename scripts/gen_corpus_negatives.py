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

    Realistically mixed: about half the time there is *some* nearby comment
    naming the asset kind (still no literal "base64" or data-URI prefix);
    the rest have no cue at all. The no-cue half is the genuinely hard case
    fix round 1 calls out -- an unlabeled high-entropy blob is structurally
    indistinguishable from a bare secret without file/content-type context
    this text-only redactor does not have. Kept honest rather than
    special-cased into passing.
    """
    body = _rand_chars(rng, _B64, rng.randint(60, 140)) + "=="
    if rng.random() < 0.5:
        kind = rng.choice(["wasm module", "font glyph table", "icon atlas"])
        return f"// preloaded {kind} follows\nconst BLOB_{rng.randint(1, 99)} = '{body}';"
    return f"const DATA_{rng.randint(1, 99)} = '{body}';"


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
    "python-traceback-address": _neg_python_traceback_address,
    "long-java-identifier": _neg_long_java_identifier,
    "minified-js": _neg_minified_js,
    "long-url-slug": _neg_long_url_slug,
    "env-ordinary": _neg_env_ordinary,
    "env-placeholder": _neg_env_placeholder,
    "file-path": _neg_file_path,
    "hex-color": _neg_hex_color,
    "prose": _neg_prose,
}
