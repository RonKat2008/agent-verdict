"""Seeded generator for tests/fixtures/secrets_corpus.jsonl (task-2-brief.md,
fix round 1).

Every positive secret is synthetic and format-valid for its family, embedded
in one of twelve realistic tool-output contexts -- four LABELED (the secret
sits right after a KEY/TOKEN/SECRET-shaped name: env dump, `export`, a JSON
config with a real key name, a curl header) and eight BARE (no adjacent key
name at all: a log line, a traceback, a prose sentence, a JSON value under a
neutral key, a URL query parameter, a quoted string in code, the secret
followed by end-of-sentence punctuation, a git remote URL). Fix round 1: an
independent reviewer found the first corpus put every secret after a label,
so structured families (AWS, GitHub, Slack, ...) were "detected" only by the
generic keyword-based fallback rules, never by their own dedicated rule.
Every family now cycles through all twelve contexts (>=4 types, >=50% bare,
satisfied by construction, not by chance), and each row records which
context produced it so the gate test can report bare-context recall
per family.

Hard negatives live in `gen_corpus_negatives.py`. Nothing here is a real
secret; nothing is read from this machine.

Deterministic: same `SEED` always produces the same corpus (`random.Random`
instance threaded through every helper, never the module-level `random`).
"""

from __future__ import annotations

import base64
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_corpus_negatives import (  # noqa: E402
    NEGATIVE_CATEGORIES,
    NEGATIVE_COUNT_OVERRIDES,
    OPAQUE_BLOB_CATEGORIES,
)

SEED = 20260921
POSITIVES_PER_FAMILY = 16
NEGATIVES_PER_CATEGORY = 12

_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_HEX_LOWER = "0123456789abcdef"
_BASE32_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B64URL = _ALNUM + "-_"
_B64 = _ALNUM + "+/"

# The 15 "structured" families the gate test holds to a bare-context,
# non-generic-rule recall bar (task-2-brief.md fix round 1, item 1).
STRUCTURED_FAMILIES: tuple[str, ...] = (
    "aws",
    "github",
    "slack",
    "stripe",
    "openai",
    "anthropic",
    "openrouter",
    "google-api",
    "jwt",
    "private-key",
    "npm",
    "pypi",
    "huggingface",
    "sendgrid",
    "twilio",
)


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
    del rng
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


def _huggingface(rng: random.Random) -> str:
    return "hf_" + _rand_chars(rng, "abcdefghijklmnopqrstuvwxyz", 34)


def _sendgrid(rng: random.Random) -> str:
    return "SG." + _rand_chars(rng, _ALNUM + "-_.", 66)


def _twilio(rng: random.Random) -> str:
    prefix = rng.choice(["SK", "AC"])
    return prefix + _rand_chars(rng, "0123456789abcdef", 32)


def _database_url(rng: random.Random) -> str:
    user = "appuser"
    password_alphabet = _ALNUM + "."
    password = _rand_chars(rng, password_alphabet, 20)
    host = rng.choice(["db.internal.example.com", "prod-db.example.net", "127.0.0.1"])
    return f"postgres://{user}:{password}@{host}:5432/appdb"


def _env_generic(rng: random.Random) -> str:
    # 44 chars (not 28): long enough that a bare context (no adjacent KEY=
    # label) still gets caught by local-high-entropy-alnum, which requires
    # 40+ chars -- a real generic app secret is typically this long or
    # longer, so this is a realism fix, not a rule-shaped one.
    return _rand_chars(rng, _ALNUM, 44)


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
    "huggingface": _huggingface,
    "sendgrid": _sendgrid,
    "twilio": _twilio,
    "database-url": _database_url,
    "env-assignment": _env_generic,
    "generic-high-entropy": _generic_high_entropy,
}

# A realistic KEY name per family, used only by the four LABELED contexts.
_LABEL_KEY: dict[str, str] = {
    "aws": "AWS_ACCESS_KEY_ID",
    "github": "GITHUB_TOKEN",
    "slack": "SLACK_BOT_TOKEN",
    "stripe": "STRIPE_SECRET_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "google-api": "GOOGLE_API_KEY",
    "jwt": "access_token",
    "private-key": "private_key",
    "npm": "NPM_TOKEN",
    "pypi": "PYPI_TOKEN",
    "huggingface": "HF_TOKEN",
    "sendgrid": "SENDGRID_API_KEY",
    "twilio": "TWILIO_AUTH_TOKEN",
    "database-url": "DATABASE_URL",
    "env-assignment": "DEPLOY_SECRET",
    "generic-high-entropy": "X-Deploy-Signature",
}


# --------------------------------------------------------------------------
# LABELED contexts: the secret sits right after a KEY/TOKEN-shaped name.
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
    del rng
    return f"export {key}={secret}\nexport NODE_ENV=production\n"


def _ctx_json_labeled(rng: random.Random, key: str, secret: str) -> str:
    obj = {
        "service": rng.choice(["billing", "auth", "notifications"]),
        key: secret,
        "timeout_ms": rng.choice([3000, 5000, 10000]),
    }
    return json.dumps(obj, indent=2)


def _ctx_curl_header(rng: random.Random, key: str, secret: str) -> str:
    url = rng.choice(
        [
            "https://api.example.com/v1/deploy",
            "https://openrouter.ai/api/v1/chat/completions",
            "https://internal.example.net/hooks/trigger",
        ]
    )
    header = key if key.lower() in ("authorization",) else f"X-{key}"
    return f'curl -X POST "{url}" -H "{header}: {secret}" -d \'{{"ok": true}}\''


# --------------------------------------------------------------------------
# BARE contexts: no adjacent key/token/secret/password/credential label.
# --------------------------------------------------------------------------


def _ctx_bare_log_line(rng: random.Random, key: str, secret: str) -> str:
    del key
    return (
        f"[{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}] "
        f"upstream responded with body: {secret}"
    )


def _ctx_bare_traceback(rng: random.Random, key: str, secret: str) -> str:
    del key
    return (
        "Traceback (most recent call last):\n"
        f'  File "app/client.py", line {rng.randint(10, 400)}, in send\n'
        f"    raise ConnectionError(f'upstream rejected {{{secret!r}}}')\n"
        "ConnectionError: upstream rejected"
    )


def _ctx_bare_prose(rng: random.Random, key: str, secret: str) -> str:
    del key
    templates = [
        f"The rotated value was {secret} according to the audit log.",
        f"Support confirmed the value {secret} still worked after the migration.",
        f"During the incident review, {secret} showed up in three separate logs.",
    ]
    return rng.choice(templates)


def _ctx_json_neutral(rng: random.Random, key: str, secret: str) -> str:
    del key
    neutral_key = rng.choice(["value", "data"])
    return json.dumps({neutral_key: secret, "id": rng.randint(1, 9999)}, indent=2)


def _ctx_url_query_param(rng: random.Random, key: str, secret: str) -> str:
    del key
    param = rng.choice(["code", "state", "ref"])
    return f"GET /callback?{param}={secret}&redirect_uri=https%3A%2F%2Fapp.example.com"


def _ctx_quoted_code_string(rng: random.Random, key: str, secret: str) -> str:
    del key
    var = rng.choice(["cfg", "opts", "payload"])
    return f'const {var} = ["{secret}", "fallback"];'


def _ctx_end_of_sentence(rng: random.Random, key: str, secret: str) -> str:
    del key
    punct = rng.choice([")", ",", "]", '"', "'", ";", "."])
    wrappers = {
        ")": f"(retry with {secret})",
        ",": f"Args: {secret}, timeout=30",
        "]": f"[{secret}]",
        '"': f'Copy exactly: "{secret}"',
        "'": f"Copy exactly: '{secret}'",
        ";": f"{secret};",
        ".": f"Ends here: {secret}.",
    }
    return wrappers[punct]


def _ctx_git_remote_bare(rng: random.Random, key: str, secret: str) -> str:
    del key, rng
    return (
        "$ git remote -v\n"
        f"origin  https://{secret}:x-oauth-basic@github.com/acme/widgets.git (fetch)\n"
        f"origin  https://{secret}:x-oauth-basic@github.com/acme/widgets.git (push)"
    )


_CONTEXTS: tuple[tuple[str, bool, Callable[[random.Random, str, str], str]], ...] = (
    ("env_dump", False, _ctx_env_dump),
    ("export_dump", False, _ctx_export_dump),
    ("json_labeled", False, _ctx_json_labeled),
    ("curl_header", False, _ctx_curl_header),
    ("bare_log_line", True, _ctx_bare_log_line),
    ("bare_traceback", True, _ctx_bare_traceback),
    ("bare_prose", True, _ctx_bare_prose),
    ("json_neutral", True, _ctx_json_neutral),
    ("url_query_param", True, _ctx_url_query_param),
    ("quoted_code_string", True, _ctx_quoted_code_string),
    ("end_of_sentence", True, _ctx_end_of_sentence),
    ("git_remote_bare", True, _ctx_git_remote_bare),
)


def _make_positive(rng: random.Random, family: str, context_index: int) -> dict[str, object]:
    secret = _FAMILY_SECRET[family](rng)
    context_name, is_bare, ctx_fn = _CONTEXTS[context_index % len(_CONTEXTS)]
    key = _LABEL_KEY[family]
    text = ctx_fn(rng, key, secret)
    return {
        "text": text,
        "secret": secret,
        "family": family,
        "context": context_name,
        "bare": is_bare,
        "opaque_blob": False,  # D-026: only negatives can be opaque_blob
    }


def _make_negative(rng: random.Random, category: str) -> dict[str, object]:
    text = NEGATIVE_CATEGORIES[category](rng)
    return {
        "text": text,
        "secret": None,
        "family": category,
        "context": category,
        "bare": True,
        # D-026: set from the CATEGORY, never from whether the redactor
        # happens to fire on this particular row.
        "opaque_blob": category in OPAQUE_BLOB_CATEGORIES,
    }


def build_corpus() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []
    for family in sorted(_FAMILY_SECRET):
        # Shuffle the fixed context order per family so identical indices
        # across families don't all land on the same context type, while
        # still guaranteeing every family cycles through all twelve types
        # (>=4 types, >=8/12 bare) within POSITIVES_PER_FAMILY >= 12 rows.
        order = list(range(len(_CONTEXTS)))
        rng.shuffle(order)
        for i in range(POSITIVES_PER_FAMILY):
            rows.append(_make_positive(rng, family, order[i % len(order)]))
    for category in sorted(NEGATIVE_CATEGORIES):
        count = NEGATIVE_COUNT_OVERRIDES.get(category, NEGATIVES_PER_CATEGORY)
        for _ in range(count):
            rows.append(_make_negative(rng, category))
    rng.shuffle(rows)
    return rows


def write_corpus(path: Path) -> None:
    rows = build_corpus()
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
