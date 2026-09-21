"""Generate plugin/hooks/verdict_hot/_redact_rules.py from vendor/gitleaks.toml.

Python 3.11+ only (uses `tomllib`). Not part of the hot path: this script runs
at development time, never in the plugin process. Usage:

    python3 scripts/gen_redact.py            # regenerate _redact_rules.py
    python3 scripts/gen_redact.py --corpus   # regenerate the measurement corpus

Translation notes (task-2-brief.md):
- Go RE2's leading `(?i)` becomes a Python scoped-flag group `(?i:...)`.
  Python 3.11 rejects a *global* inline flag anywhere but the start of the
  pattern (3.9 only warns), so any `(?i)` after position 0 is rewritten the
  same way, scoped to its true RE2 enclosing group (`_enclosing_group_end`),
  or to the end of the pattern when it is at the top level. Fix round 1
  found that an earlier version of this always scoped to "end of pattern",
  which mis-balanced parens and silently broke sibling alternatives after
  the flag (`curl-auth-header`'s single-quote branch stopped matching).
- `\\z` (RE2/PCRE "absolute end") becomes `\\Z` (Python's "absolute end";
  Python has no separate "before trailing newline" form).
- The one POSIX class in the vendored file (`[:alnum:]`) is expanded inline.
- The vendored trailing-delimiter construct `_TRAILING_DELIM_OLD` (152
  occurrences) requires the secret be immediately followed by one of a
  narrow set of characters, so `sk_live_XXXX)` or `sk_live_XXXX,` fail to
  match at all (fix round 1, item 5). It is widened to also accept
  `) ] } , . : > <` and rewritten as a lookahead `(?=...)` so the delimiter
  is asserted but never consumed or redacted.
- Every translated pattern is compile-checked under Python 3.9 `re` semantics
  by shelling out to /usr/bin/python3 (falling back to compiling locally with
  the current interpreter if that binary is missing). Rules that still fail
  to compile are skipped and reported; the generator exits 1 if fewer than
  150 of the ~222 vendored rules compile.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOML_PATH = ROOT / "vendor" / "gitleaks.toml"
VERSION_PATH = ROOT / "vendor" / "GITLEAKS_VERSION"
OUTPUT_PATH = ROOT / "plugin" / "hooks" / "verdict_hot" / "_redact_rules.py"
CORPUS_PATH = ROOT / "tests" / "fixtures" / "secrets_corpus.jsonl"
SYSTEM_PYTHON39 = Path("/usr/bin/python3")
MIN_COMPILED = 150

_TRAILING_DELIM_OLD = r"""(?:[\x60'"\s;]|\\[nr]|$)"""
_TRAILING_DELIM_NEW = r"""(?=[\x60'"\s;)\]},.:><&]|\\[nr]|$)"""

_POSIX_CLASSES = {
    "[:alnum:]": "A-Za-z0-9",
    "[:alpha:]": "A-Za-z",
    "[:digit:]": "0-9",
    "[:upper:]": "A-Z",
    "[:lower:]": "a-z",
    "[:xdigit:]": "0-9A-Fa-f",
    "[:space:]": " \\t\\n\\r\\f\\v",
    "[:punct:]": "!-/:-@\\[-`{-~",
}

# Local rules not present in gitleaks. Each tuple entry mirrors the RULES
# shape below: (id, pattern, keywords, entropy_floor). Fix round 1 removed
# the sha*/base64-prefix lookbehinds that used to guard `local-high-entropy`
# -- they exactly mirrored this repo's own negative-corpus generators (a
# held-out probe set with different lockfile/hash formats proved them
# worthless) -- and replaced them with structural checks applied at match
# time in redact.py/_redact_filters.py: standard hex-digest lengths,
# required mixed character classes, dictionary-word-like values, obvious
# placeholders, an ADJACENCY-ONLY hash/digest/asset cue check (fix round 2,
# item 2 -- an earlier version searched an entire lookback window for a cue
# word anywhere, which suppressed real secrets merely mentioned near a hash
# word elsewhere on the line), and SSH-public-key / PEM public-key /
# certificate exclusions (fix round 2, item 4). `local-high-entropy` is
# split into an alnum-only and a base64-with-padding variant so each can
# carry its own (independently raised) entropy floor.
LOCAL_RULES: tuple[tuple[str, str, tuple[str, ...], float | None], ...] = (
    (
        "local-env-secret",
        r"(?m)^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)"
        r"[A-Z0-9_]*\s*=\s*(\S{8,})",
        ("key", "token", "secret", "password", "passwd", "credential"),
        None,
    ),
    (
        "local-password-assignment",
        # Fix round 2, item 3: "a hint about the password prompt." and
        # "password whenever"/"password protected" (ordinary English, no
        # assignment) must not hit. Three alternatives, each with its own
        # capturing group (redact.py unions whichever one participates):
        # (1) an explicit `:`/`=` separator is REQUIRED (not optional
        #     whitespace, as the first cut had it);
        # (2) a `.netrc`-style bare `password <value>` line, but only when
        #     the same line also mentions "machine" or "login";
        # (3) a line that is JUST "password <value>", where <value>
        #     contains a digit or symbol (excludes "password protected").
        # `_redact_filters.looks_like_plain_english_word` additionally
        # rejects a lowercase-alphabetic-only value from ANY branch.
        r"(?im)\bpassword\s*[:=]\s*(\S{6,})"
        r"|^(?=.*\b(?:machine|login)\b).*?\bpassword\s+(\S{6,})"
        r"|^\s*password\s+(\S*[0-9\W]\S*)\s*$",
        ("password",),
        None,
    ),
    (
        "local-high-entropy-alnum",
        # Excludes a run immediately touching "+" or "/" on either side, not
        # just other alnum chars: without that, a 40+ char alnum *substring*
        # inside a larger base64 blob (which uses "+"/"/") matches on its own
        # -- at a start position with no "base64,"/"sha256-"-style vocabulary
        # immediately before it -- silently bypassing the context check that
        # correctly excludes the base64 rule's own (whole-blob) match.
        r"(?<![A-Za-z0-9+/])([A-Za-z0-9]{40,})(?![A-Za-z0-9+/])",
        (),
        4.3,
    ),
    (
        "local-high-entropy-b64",
        # No trailing "=" in the lookbehind exclusion set (unlike the alnum
        # variant): "=" is the single most common assignment separator
        # (`KEY=<value>`), so excluding it would block matching a base64
        # value immediately after one -- found via the required env-dump
        # bare-context test (fix round 1).
        r"(?<![A-Za-z0-9+/])([A-Za-z0-9+/]{40,}={1,2})",
        (),
        4.8,
    ),
    (
        "local-url-credential",
        r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s@]+)@",
        ("://",),
        None,
    ),
    (
        "local-openrouter",
        r"\bsk-or-v1-[a-f0-9]{64}\b",
        ("sk-or-v1-",),
        None,
    ),
    (
        "local-openai-project-key",
        r"\bsk-proj-[A-Za-z0-9_-]{20,200}\b",
        ("sk-proj-",),
        None,
    ),
    (
        "local-twilio-account-sid",
        # Fix round 2, item 6: the keyword prefilter used to be the 2-letter
        # substring "ac", which matches "cache", "backup", "package", ... on
        # essentially every line of real tool output, making the prefilter
        # pointless. The regex's own anchors (`AC` + exactly 32 hex chars,
        # word boundaries) are precise enough on their own; run
        # unconditionally instead of gating on a near-universal substring.
        r"\bAC[0-9a-fA-F]{32}\b",
        (),
        None,
    ),
    (
        "local-huggingface-token",
        # Fix round 2, item 6: the vendored huggingface-access-token rule
        # requires exactly 34 chars after "hf_"; widen local coverage to
        # 30-40 to catch token-format variants.
        r"\bhf_[A-Za-z0-9]{30,40}\b",
        ("hf_",),
        None,
    ),
    (
        "local-telegram-bot-token",
        # Fix round 2, item 5: `https://api.telegram.org/bot<id>:<secret>/...`
        # is missed by the vendored telegram rule, which requires a narrow
        # gap after its keyword. `(?<=/bot)` is a fixed-width (4-char)
        # lookbehind, valid in Python.
        r"(?<=/bot)\d{6,12}:[A-Za-z0-9_-]{35}\b",
        ("telegram.org/bot",),
        None,
    ),
)

# Fix round 2, item 5: only kept if it does not raise text FPR -- see
# task-2-report.md "Fix round 2" for the measured outcome. Declared
# separately so it can be omitted from LOCAL_RULES with a one-line change
# and a clear reason, rather than silently dropped.
MAILGUN_RULE: tuple[str, str, tuple[str, ...], float | None] = (
    "local-mailgun-key",
    r"\bkey-[0-9a-f]{32}\b",
    ("key-",),
    None,
)
INCLUDE_MAILGUN_RULE = True
if INCLUDE_MAILGUN_RULE:
    LOCAL_RULES = (*LOCAL_RULES, MAILGUN_RULE)


def _expand_posix_classes(pattern: str) -> str:
    for needle, replacement in _POSIX_CLASSES.items():
        pattern = pattern.replace(needle, replacement)
    return pattern


_MAX_INLINE_FLAG_PASSES = 50


def _sibling_boundary(pattern: str, start: int) -> int:
    """Index of the first `|` or `)` at depth 0 relative to `start`.

    Returns `len(pattern)` if neither is found first. Ignores parens/pipes
    inside character classes and escaped characters.

    A bare `(?i)` scopes to "the rest of its current alternative", not to
    its enclosing group's own close paren: gitleaks rules routinely put a
    separate `(?i)` in *each* branch of a `(...|...)` alternation (e.g.
    `curl-auth-header`'s double- and single-quote branches), and scoping the
    first one all the way to the enclosing group's close paren swallows the
    literal `|'...'` that starts the next branch into the flag's own
    "content" -- which then requires that branch's leading quote character
    to appear inside what was supposed to be an alternative, breaking the
    match entirely (fix round 1, item 4 fallout, found via the required
    curl Basic-auth test).
    """
    depth, i, n, in_class = 0, start, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
            i += 1
            continue
        if c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                return i
            depth -= 1
        elif c == "|" and depth == 0:
            return i
        i += 1
    return n


def _translate_inline_flags(pattern: str) -> str:
    """Rewrite each bare `(?i)` into a scoped `(?i:...)` group (module docstring)."""
    for _ in range(_MAX_INLINE_FLAG_PASSES):
        idx = pattern.find("(?i)")
        if idx == -1:
            break
        close = _sibling_boundary(pattern, idx + 4)
        pattern = pattern[:idx] + "(?i:" + pattern[idx + 4 : close] + ")" + pattern[close:]
    return pattern


def translate_regex(pattern: str) -> str:
    pattern = _expand_posix_classes(pattern)
    pattern = pattern.replace(r"\z", r"\Z")
    pattern = pattern.replace(_TRAILING_DELIM_OLD, _TRAILING_DELIM_NEW)
    pattern = _translate_inline_flags(pattern)
    return pattern


def _compile_check(patterns: list[str]) -> list[dict[str, Any]]:
    """Compile-check every pattern under Python 3.9 `re` semantics.

    Shells out to /usr/bin/python3 when present (this generator runs on
    3.11+, but the hot path runs on 3.9 and 3.9/3.11 differ on inline-flag
    placement); compiles locally otherwise.
    """
    checker = (
        "import json, re, sys\n"
        "patterns = json.loads(sys.stdin.read())\n"
        "results = []\n"
        "for p in patterns:\n"
        "    try:\n"
        "        re.compile(p)\n"
        "        results.append({'ok': True})\n"
        "    except Exception as exc:\n"
        "        results.append({'ok': False, 'error': str(exc)})\n"
        "print(json.dumps(results))\n"
    )
    if SYSTEM_PYTHON39.exists():
        proc = subprocess.run(
            [str(SYSTEM_PYTHON39), "-c", checker],
            input=json.dumps(patterns),
            capture_output=True,
            text=True,
            check=True,
        )
        result: list[dict[str, Any]] = json.loads(proc.stdout)
        return result
    results: list[dict[str, Any]] = []
    for p in patterns:
        try:
            re.compile(p)
            results.append({"ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"ok": False, "error": str(exc)})
    return results


def _load_vendored_rules() -> tuple[list[dict[str, Any]], list[str], list[str]]:
    with TOML_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    rules: list[dict[str, Any]] = data["rules"]
    allowlist = data.get("allowlist", {})
    return rules, list(allowlist.get("regexes", [])), list(allowlist.get("stopwords", []))


def build_rules() -> tuple[
    list[tuple[str, str, tuple[str, ...], float | None]],
    list[tuple[str, str]],
    list[str],
    list[str],
]:
    """Returns (compiled RULES entries, skipped (id, reason) pairs, allow regexes, stopwords)."""
    vendored, allow_regexes, stopwords = _load_vendored_rules()
    candidates: list[tuple[str, str, tuple[str, ...], float | None]] = []
    path_only: list[tuple[str, str]] = []
    for rule in vendored:
        rule_id = rule["id"]
        if "regex" not in rule:
            path_only.append((rule_id, "no content regex (path-only rule, not applicable)"))
            continue
        translated = translate_regex(rule["regex"])
        keywords = tuple(sorted(str(k).lower() for k in rule.get("keywords", ())))
        entropy = rule.get("entropy")
        entropy_floor = float(entropy) if entropy is not None else None
        candidates.append((rule_id, translated, keywords, entropy_floor))
    candidates.extend(LOCAL_RULES)
    allow_regexes = [translate_regex(r) for r in allow_regexes]

    check_results = _compile_check([c[1] for c in candidates])
    compiled: list[tuple[str, str, tuple[str, ...], float | None]] = []
    skipped: list[tuple[str, str]] = []
    for entry, result in zip(candidates, check_results, strict=True):
        if result["ok"]:
            compiled.append(entry)
        else:
            skipped.append((entry[0], result.get("error", "unknown error")))
    skipped = path_only + skipped

    allow_check = _compile_check(allow_regexes)
    good_allow_regexes = [
        pattern for pattern, result in zip(allow_regexes, allow_check, strict=True) if result["ok"]
    ]
    for pattern, result in zip(allow_regexes, allow_check, strict=True):
        if not result["ok"]:
            skipped.append((f"[allowlist] {pattern!r}", result.get("error", "unknown error")))

    return compiled, skipped, good_allow_regexes, stopwords


def _format_rules_literal(
    rules: list[tuple[str, str, tuple[str, ...], float | None]],
) -> str:
    lines = []
    for rule_id, pattern, keywords, entropy in sorted(rules, key=lambda r: r[0]):
        lines.append(f"    ({rule_id!r}, {pattern!r}, {keywords!r}, {entropy!r}),")
    return "\n".join(lines)


def write_rules_file(
    rules: list[tuple[str, str, tuple[str, ...], float | None]],
    allow_regexes: list[str],
    stopwords: list[str],
    gitleaks_commit: str,
) -> None:
    body = _format_rules_literal(rules)
    allow_literal = ",\n".join(f"    {r!r}" for r in allow_regexes)
    stop_literal = ",\n".join(f"    {s!r}" for s in stopwords)
    content = f'''"""GENERATED by `python3 scripts/gen_redact.py`. Do not edit by hand.

Source: vendor/gitleaks.toml (MIT license, see vendor/GITLEAKS_LICENSE) at
gitleaks commit {gitleaks_commit}, plus local rules (task-2-brief.md, fix
rounds 1-2). Edit scripts/gen_redact.py or vendor/gitleaks.toml and run
`make gen-redact` to regenerate. Patterns are stored as strings, not
compiled -- see verdict_hot/redact.py for lazy per-rule compilation.
"""

from __future__ import annotations

GITLEAKS_COMMIT = {gitleaks_commit!r}

# (rule_id, pattern, lowercase_keywords, entropy_floor_or_None). A match's
# secret is the union of every participating capturing group, or the whole
# match if none participated (verdict_hot/redact.py:_match_spans) -- fix
# round 2 removed the unused `secret_group` index this tuple used to carry,
# since group selection has been dynamic (per-match) since fix round 1.
RULES: tuple[tuple[str, str, tuple[str, ...], "float | None"], ...] = (
{body}
)

# Global gitleaks [allowlist]: a candidate secret is discarded when it fully
# matches one of these regexes, or contains one of these stopwords
# (case-insensitive). Per-rule allowlists are not ported (task-2-report.md).
ALLOWLIST_REGEXES: tuple[str, ...] = (
{allow_literal}
)

STOPWORDS: tuple[str, ...] = (
{stop_literal}
)
'''
    OUTPUT_PATH.write_text(content, encoding="utf-8")


def generate() -> int:
    gitleaks_commit = VERSION_PATH.read_text(encoding="utf-8").strip()
    rules, skipped, allow_regexes, stopwords = build_rules()
    write_rules_file(rules, allow_regexes, stopwords, gitleaks_commit)

    rule_skips = [s for s in skipped if not s[0].startswith("[allowlist] ")]
    total = len(rules) + len(rule_skips)
    print(f"compiled {len(rules)} of {total}")
    if skipped:
        print("skipped rules:")
        for rule_id, reason in skipped:
            print(f"  - {rule_id}: {reason}")
    if len(rules) < MIN_COMPILED:
        print(f"FAIL: only {len(rules)} rules compiled, need at least {MIN_COMPILED}")
        return 1
    return 0


def main() -> int:
    if "--corpus" in sys.argv:
        # Imported lazily: gen_corpus.py runs at generation time only and
        # must not be on the hot-path import graph.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from gen_corpus import write_corpus

        write_corpus(CORPUS_PATH)
        print(f"wrote {CORPUS_PATH}")
        return 0
    return generate()


if __name__ == "__main__":
    sys.exit(main())
