"""Generate plugin/hooks/verdict_hot/_redact_rules.py from vendor/gitleaks.toml.

Python 3.11+ only (uses `tomllib`). Not part of the hot path: this script runs
at development time, never in the plugin process. Usage:

    python3 scripts/gen_redact.py            # regenerate _redact_rules.py
    python3 scripts/gen_redact.py --corpus   # regenerate the measurement corpus

Translation notes (task-2-brief.md):
- Go RE2's leading `(?i)` becomes a Python scoped-flag group `(?i:...)`.
  Python 3.11 rejects a *global* inline flag anywhere but the start of the
  pattern (3.9 only warns), so any `(?i)` after position 0 is rewritten the
  same way, scoped from that point to the end of the pattern rather than to
  its true RE2 enclosing group -- a deliberate over-approximation (documented
  in task-2-report.md) that only affects a handful of rules outside the
  families this task's corpus and local rules target.
- `\\z` (RE2/PCRE "absolute end") becomes `\\Z` (Python's "absolute end";
  Python has no separate "before trailing newline" form).
- The one POSIX class in the vendored file (`[:alnum:]`) is expanded inline.
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

# Three local rules not present in gitleaks (task-2-brief.md). Each tuple
# entry mirrors the RULES shape below (id, pattern, keywords, entropy, group).
LOCAL_RULES: tuple[tuple[str, str, tuple[str, ...], float | None, int], ...] = (
    (
        "local-env-secret",
        r"(?m)^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)"
        r"[A-Z0-9_]*\s*=\s*(\S{8,})",
        ("key", "token", "secret", "password", "passwd", "credential"),
        None,
        1,
    ),
    (
        "local-high-entropy",
        r"(?<!sha1-)(?<!sha256-)(?<!sha384-)(?<!sha512-)(?<!sha1:)(?<!sha256:)(?<!sha384:)"
        r"(?<!sha512:)(?<!base64,)(?<!base64:)(?<![A-Za-z0-9+/=])"
        r"([A-Za-z0-9]{40,}|[A-Za-z0-9+/]{40,}={1,2})",
        (),
        4.0,
        1,
    ),
    (
        "local-url-credential",
        r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s@]+)@",
        ("://",),
        None,
        1,
    ),
)


def _expand_posix_classes(pattern: str) -> str:
    for needle, replacement in _POSIX_CLASSES.items():
        pattern = pattern.replace(needle, replacement)
    return pattern


def _translate_inline_flags(pattern: str) -> str:
    """Rewrite bare `(?i)` into a scoped `(?i:...)` group (module docstring)."""
    if pattern.startswith("(?i)"):
        rest = pattern[4:].replace("(?i)", "")
        return "(?i:" + rest + ")"
    idx = pattern.find("(?i)")
    if idx == -1:
        return pattern
    before, after = pattern[:idx], pattern[idx + 4 :]
    return before + "(?i:" + after.replace("(?i)", "") + ")"


def translate_regex(pattern: str) -> str:
    pattern = _expand_posix_classes(pattern)
    pattern = pattern.replace(r"\z", r"\Z")
    pattern = _translate_inline_flags(pattern)
    return pattern


def _has_capturing_group(pattern: str) -> bool:
    """True if `pattern` contains at least one capturing group (named or not).

    Used to pick `secret_group`: 1 when the pattern captures a sub-span
    (almost always the secret itself in vendored rules), else 0 (whole match).
    """
    i, n, in_class = 0, len(pattern), False
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
            i += 1
            continue
        if c == "(":
            nxt = pattern[i + 1 : i + 2]
            if nxt != "?":
                return True
            if pattern[i + 2 : i + 4] == "P<":
                return True
        i += 1
    return False


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
    list[tuple[str, str, tuple[str, ...], float | None, int]],
    list[tuple[str, str]],
    list[str],
    list[str],
]:
    """Returns (compiled RULES entries, skipped (id, reason) pairs, allow regexes, stopwords)."""
    vendored, allow_regexes, stopwords = _load_vendored_rules()
    candidates: list[tuple[str, str, tuple[str, ...], float | None, int]] = []
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
        secret_group = 1 if _has_capturing_group(translated) else 0
        candidates.append((rule_id, translated, keywords, entropy_floor, secret_group))
    candidates.extend(LOCAL_RULES)
    allow_regexes = [translate_regex(r) for r in allow_regexes]

    check_results = _compile_check([c[1] for c in candidates])
    compiled: list[tuple[str, str, tuple[str, ...], float | None, int]] = []
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
    rules: list[tuple[str, str, tuple[str, ...], float | None, int]],
) -> str:
    lines = []
    for rule_id, pattern, keywords, entropy, group in sorted(rules, key=lambda r: r[0]):
        lines.append(f"    ({rule_id!r}, {pattern!r}, {keywords!r}, {entropy!r}, {group!r}),")
    return "\n".join(lines)


def write_rules_file(
    rules: list[tuple[str, str, tuple[str, ...], float | None, int]],
    allow_regexes: list[str],
    stopwords: list[str],
    gitleaks_commit: str,
) -> None:
    body = _format_rules_literal(rules)
    allow_literal = ",\n".join(f"    {r!r}" for r in allow_regexes)
    stop_literal = ",\n".join(f"    {s!r}" for s in stopwords)
    content = f'''"""GENERATED by `python3 scripts/gen_redact.py`. Do not edit by hand.

Source: vendor/gitleaks.toml (MIT license, see vendor/GITLEAKS_LICENSE) at
gitleaks commit {gitleaks_commit}, plus three local rules (task-2-brief.md).
Edit scripts/gen_redact.py or vendor/gitleaks.toml and run
`make gen-redact` to regenerate. Patterns are stored as strings, not
compiled -- see verdict_hot/redact.py for lazy per-rule compilation.
"""

from __future__ import annotations

GITLEAKS_COMMIT = {gitleaks_commit!r}

# (rule_id, pattern, lowercase_keywords, entropy_floor_or_None, secret_group)
RULES: tuple[tuple[str, str, tuple[str, ...], "float | None", int], ...] = (
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
