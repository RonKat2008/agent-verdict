"""Seeded generator for tests/fixtures/secrets_corpus.jsonl (task-2-brief.md).

Every positive secret is synthetic and format-valid for its family, embedded
in a realistic tool-output context (env dump, curl command, stack trace, JSON
config, git remote, build log). Every negative is a realistic non-secret that
resembles a secret superficially (git SHA, UUID, lockfile integrity hash,
base64 image fragment, long file path, hex color, ordinary prose). Nothing
here reads real credentials from this machine; nothing is a real secret.

Deterministic: same `SEED` always produces the same corpus (`random.Random`
instance threaded through every helper, never the module-level `random`).
"""

from __future__ import annotations

import base64
import json
import random
from collections.abc import Callable
from pathlib import Path

SEED = 20260921
POSITIVES_PER_FAMILY = 14
NEGATIVES_PER_CATEGORY = 29

_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_HEX_LOWER = "0123456789abcdef"
_BASE32_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B64URL = _ALNUM + "-_"
_B64 = _ALNUM + "+/"


def _rand_chars(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


# --------------------------------------------------------------------------
# Fake secret generators, one per family. Each returns a format-valid,
# synthetic secret string for that family.
# --------------------------------------------------------------------------


def _aws(rng: random.Random) -> str:
    prefix = rng.choice(["AKIA", "ASIA", "ABIA", "ACCA"])
    return prefix + _rand_chars(rng, _BASE32_UPPER, 16)


def _github(rng: random.Random) -> str:
    return "ghp_" + _rand_chars(rng, _ALNUM, 36)


def _slack(rng: random.Random) -> str:
    seg1 = "".join(rng.choice("0123456789") for _ in range(12))
    seg2 = "".join(rng.choice("0123456789") for _ in range(12))
    suffix = _rand_chars(rng, _ALNUM, 24)
    return f"xoxb-{seg1}-{seg2}-{suffix}"


def _stripe(rng: random.Random) -> str:
    kind = rng.choice(["sk", "rk"])
    env = rng.choice(["test", "live", "prod"])
    return f"{kind}_{env}_" + _rand_chars(rng, _ALNUM, 24)


def _openai(rng: random.Random) -> str:
    return "sk-" + _rand_chars(rng, _ALNUM, 20) + "T3BlbkFJ" + _rand_chars(rng, _ALNUM, 20)


def _anthropic(rng: random.Random) -> str:
    return "sk-ant-api03-" + _rand_chars(rng, _ALNUM + "_-", 93) + "AA"


def _openrouter(rng: random.Random) -> str:
    return "sk-or-v1-" + _rand_chars(rng, _HEX_LOWER, 64)


def _google_api(rng: random.Random) -> str:
    return "AIza" + _rand_chars(rng, _ALNUM + "_-", 35)


def _b64url_json(rng: random.Random, obj: dict[str, object]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt(rng: random.Random) -> str:
    header = _b64url_json(rng, {"alg": "HS256", "typ": "JWT"})
    payload = _b64url_json(
        rng, {"sub": str(rng.randint(1000, 9999)), "iat": rng.randint(10**9, 2 * 10**9)}
    )
    sig = _rand_chars(rng, _B64URL, 43)
    return f"{header}.{payload}.{sig}"


def _private_key(rng: random.Random) -> str:
    lines = ["-----BEGIN RSA PRIVATE KEY-----"]
    for _ in range(18):
        lines.append(_rand_chars(rng, _B64, 64))
    lines.append("-----END RSA PRIVATE KEY-----")
    return "\n".join(lines)


def _npm(rng: random.Random) -> str:
    return "npm_" + _rand_chars(rng, "abcdefghijklmnopqrstuvwxyz0123456789", 36)


def _pypi(rng: random.Random) -> str:
    return "pypi-AgEIcHlwaS5vcmc" + _rand_chars(rng, _ALNUM + "-_", 60)


def _database_url(rng: random.Random) -> str:
    user = "appuser"
    password_alphabet = _ALNUM + "."
    password = _rand_chars(rng, password_alphabet, 20)
    host = rng.choice(["db.internal.example.com", "prod-db.example.net", "127.0.0.1"])
    return f"postgres://{user}:{password}@{host}:5432/appdb"


def _env_generic(rng: random.Random) -> str:
    return _rand_chars(rng, _ALNUM, 28)


def _generic_high_entropy(rng: random.Random) -> str:
    return _rand_chars(rng, _B64, 44) + "=="


_FAMILY_SECRET: dict[str, Callable[[random.Random], str]] = {
    "aws": _aws,
    "github": _github,
    "slack": _slack,
    "stripe": _stripe,
    "openai": _openai,
    "anthropic": _anthropic,
    "openrouter": _openrouter,
    "google-api": _google_api,
    "jwt": _jwt,
    "private-key": _private_key,
    "npm": _npm,
    "pypi": _pypi,
    "database-url": _database_url,
    "env-assignment": _env_generic,
    "generic-high-entropy": _generic_high_entropy,
}

# --------------------------------------------------------------------------
# Realistic wrapping contexts. Each takes (rng, secret) and returns text with
# the secret embedded once, verbatim.
# --------------------------------------------------------------------------


def _ctx_env_dump(rng: random.Random, key: str, secret: str) -> str:
    other_lines = [
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "LANG=en_US.UTF-8",
        f"HOME=/Users/{rng.choice(['dev', 'ci', 'runner'])}",
        "NODE_ENV=production",
    ]
    lines = other_lines[: rng.randint(1, len(other_lines))]
    lines.insert(rng.randint(0, len(lines)), f"{key}={secret}")
    return "\n".join(lines)


def _ctx_export_dump(rng: random.Random, key: str, secret: str) -> str:
    return f"export {key}={secret}\nexport NODE_ENV=production\n"


def _ctx_curl(rng: random.Random, key: str, secret: str) -> str:
    url = rng.choice(
        [
            "https://api.example.com/v1/deploy",
            "https://openrouter.ai/api/v1/chat/completions",
            "https://internal.example.net/hooks/trigger",
        ]
    )
    return f'curl -X POST "{url}" -H "{key}: {secret}" -d \'{{"ok": true}}\''


def _ctx_json_config(rng: random.Random, key: str, secret: str) -> str:
    obj = {
        "service": rng.choice(["billing", "auth", "notifications"]),
        key: secret,
        "timeout_ms": rng.choice([3000, 5000, 10000]),
    }
    return json.dumps(obj, indent=2)


def _ctx_stack_trace(rng: random.Random, key: str, secret: str) -> str:
    return (
        "Traceback (most recent call last):\n"
        f'  File "app/config.py", line {rng.randint(10, 400)}, in load_config\n'
        f"    raise RuntimeError(f'failed with {key}={secret}')\n"
        "RuntimeError: configuration error"
    )


def _ctx_git_remote(rng: random.Random, key: str, secret: str) -> str:
    del key
    return (
        "$ git remote -v\n"
        f"origin  https://{secret}:x-oauth-basic@github.com/acme/widgets.git (fetch)\n"
        f"origin  https://{secret}:x-oauth-basic@github.com/acme/widgets.git (push)"
    )


def _ctx_build_log(rng: random.Random, key: str, secret: str) -> str:
    return (
        f"[{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}] Deploying build "
        f"#{rng.randint(100, 9999)}...\n"
        f"{key}: {secret}\n"
        "[build] done in 12.4s"
    )


def _ctx_file_dump(rng: random.Random, key: str, secret: str) -> str:
    del key
    return f"$ cat ~/.ssh/id_rsa\n{secret}\n"


_FAMILY_CONTEXTS: dict[str, list[tuple[str, Callable[[random.Random, str, str], str]]]] = {
    "aws": [("AWS_SECRET_ACCESS_KEY", _ctx_env_dump), ("AWS_ACCESS_KEY_ID", _ctx_export_dump)],
    "github": [("GITHUB_TOKEN", _ctx_env_dump), ("token", _ctx_git_remote)],
    "slack": [("SLACK_BOT_TOKEN", _ctx_env_dump), ("Authorization", _ctx_curl)],
    "stripe": [("STRIPE_SECRET_KEY", _ctx_env_dump), ("stripe_key", _ctx_json_config)],
    "openai": [("OPENAI_API_KEY", _ctx_env_dump), ("api_key", _ctx_json_config)],
    "anthropic": [("ANTHROPIC_API_KEY", _ctx_export_dump), ("api_key", _ctx_json_config)],
    "openrouter": [("OPENROUTER_API_KEY", _ctx_env_dump), ("Authorization", _ctx_curl)],
    "google-api": [("apiKey", _ctx_json_config), ("GOOGLE_API_KEY", _ctx_env_dump)],
    "jwt": [("token", _ctx_json_config), ("access_token", _ctx_build_log)],
    "private-key": [("key", _ctx_file_dump), ("private_key", _ctx_stack_trace)],
    "npm": [("//registry.npmjs.org/:_authToken", _ctx_env_dump), ("NPM_TOKEN", _ctx_export_dump)],
    "pypi": [("TWINE_PASSWORD", _ctx_env_dump), ("PYPI_TOKEN", _ctx_export_dump)],
    "database-url": [("DATABASE_URL", _ctx_env_dump), ("db_url", _ctx_json_config)],
    "env-assignment": [
        ("DEPLOY_SECRET", _ctx_env_dump),
        ("APP_CREDENTIAL_TOKEN", _ctx_export_dump),
    ],
    "generic-high-entropy": [
        ("X-Deploy-Signature", _ctx_build_log),
        ("Backup-Encryption-Digest", _ctx_curl),
    ],
}


def _make_positive(rng: random.Random, family: str) -> dict[str, object]:
    secret = _FAMILY_SECRET[family](rng)
    key, ctx_fn = rng.choice(_FAMILY_CONTEXTS[family])
    text = ctx_fn(rng, key, secret)
    return {"text": text, "secret": secret, "family": family}


# --------------------------------------------------------------------------
# Hard negatives: realistic non-secrets that superficially resemble secrets.
# --------------------------------------------------------------------------


def _neg_git_sha(rng: random.Random) -> str:
    sha = _rand_chars(rng, _HEX_LOWER, 40)
    return f"commit {sha}\nAuthor: dev <dev@example.com>\n\n    fix: tighten validation"


def _neg_uuid(rng: random.Random) -> str:
    hexs = _rand_chars(rng, _HEX_LOWER, 32)
    uuid = f"{hexs[0:8]}-{hexs[8:12]}-{hexs[12:16]}-{hexs[16:20]}-{hexs[20:32]}"
    return f'{{"request_id": "{uuid}", "status": "ok"}}'


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


def _neg_base64_image(rng: random.Random) -> str:
    body = _rand_chars(rng, _B64, rng.randint(60, 140))
    kind = rng.choice(["png", "jpeg", "gif"])
    return f'<img src="data:image/{kind};base64,{body}==" alt="logo" />'


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


_NEGATIVE_CATEGORIES: dict[str, Callable[[random.Random], str]] = {
    "git-sha": _neg_git_sha,
    "uuid": _neg_uuid,
    "lockfile-hash": _neg_lockfile_hash,
    "base64-image": _neg_base64_image,
    "file-path": _neg_file_path,
    "hex-color": _neg_hex_color,
    "prose": _neg_prose,
}


def _make_negative(rng: random.Random, category: str) -> dict[str, object]:
    text = _NEGATIVE_CATEGORIES[category](rng)
    return {"text": text, "secret": None, "family": category}


def build_corpus() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []
    for family in sorted(_FAMILY_SECRET):
        for _ in range(POSITIVES_PER_FAMILY):
            rows.append(_make_positive(rng, family))
    for category in sorted(_NEGATIVE_CATEGORIES):
        for _ in range(NEGATIVES_PER_CATEGORY):
            rows.append(_make_negative(rng, category))
    rng.shuffle(rows)
    return rows


def write_corpus(path: Path) -> None:
    rows = build_corpus()
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
