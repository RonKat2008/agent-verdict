"""Gate G1.7, as redefined by D-027 (docs/DECISIONS.md; amends D-026).

A third held-out probe passed recall but failed the single "text FPR"
number: every false hit was a random-looking PUBLIC token (CSP nonce, CSRF
value, pagination cursor, idempotency key, bcrypt hash, JWKS modulus,
publishable key) the corpus had no category for. D-027 splits false
positives by HARM rather than by "is it text": a negative is EVIDENCE TEXT
if a person could read meaning from the redacted span (prose, errors,
commands, paths, word-shaped identifiers, placeholders, ordinary env
lines, secret names, version strings, URLs with slugs, standard-shape
digests, SSH/PEM public material); it is an OPAQUE TOKEN if it is a
random-looking string of 20+ chars with no word structure (nonces, CSRF
values, cursors, idempotency keys, request/trace ids, bcrypt/JWKS
material, publishable keys, base64 blobs). The class is assigned from the
CATEGORY by the corpus generator (`gen_corpus_negatives.
neg_class_for_category`), never from the redaction outcome.

Gate:
- overall recall >= 0.95
- bare-context, non-generic-rule recall >= 0.90 (structured families)
- evidence-text false-positive rate <= 0.02
- opaque-token over-redaction: PRINTED with a per-category table, no
  assertion beyond printing (redacting a public, meaningless-on-its-own
  token removes no evidence, so this is reported, not gated)

All four numbers are printed as labeled scalars. The corpus
(tests/fixtures/secrets_corpus.jsonl, from `python3 scripts/gen_redact.py
--corpus` / scripts/gen_corpus.py) must never be hand-tuned to make these
numbers look better than they are, and no evidence-text category is
reclassified as an opaque token to move it out of the stricter bucket.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gen_corpus import STRUCTURED_FAMILIES  # noqa: E402
from gen_corpus_negatives import EVIDENCE_TEXT, OPAQUE_TOKEN  # noqa: E402
from verdict_hot import redact  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parent / "fixtures" / "secrets_corpus.jsonl"
MIN_RECALL = 0.95
MIN_BARE_NON_GENERIC_RECALL = 0.90
MAX_EVIDENCE_TEXT_FPR = 0.02
_GENERIC_RULE_IDS = frozenset(
    {"generic-api-key", "local-high-entropy-alnum", "local-high-entropy-b64"}
)


def _load_corpus() -> list[dict[str, object]]:
    rows = []
    with CORPUS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _over_redaction(
    sample: list[dict[str, object]],
) -> tuple[float, int, Counter[str], Counter[str]]:
    hit_count = 0
    cat_total: Counter[str] = Counter()
    cat_hit: Counter[str] = Counter()
    for row in sample:
        text = str(row["text"])
        family = str(row["family"])
        _cleaned, hits = redact.redact(text)
        cat_total[family] += 1
        if hits > 0:
            hit_count += 1
            cat_hit[family] += 1
    rate = hit_count / len(sample) if sample else float("nan")
    return rate, hit_count, cat_total, cat_hit


def test_redaction_gate_g1_7() -> None:
    rows = _load_corpus()
    positives = [r for r in rows if r["secret"] is not None]
    negatives = [r for r in rows if r["secret"] is None]
    evidence_negatives = [r for r in negatives if r["neg_class"] == EVIDENCE_TEXT]
    opaque_negatives = [r for r in negatives if r["neg_class"] == OPAQUE_TOKEN]
    assert len(positives) >= 150, "expected at least 150 positives in the corpus"
    assert len(evidence_negatives) >= 150, "expected at least 150 evidence-text negatives"
    assert len(opaque_negatives) >= 20, "expected a meaningful opaque-token sample"

    family_total: Counter[str] = Counter()
    family_hit: Counter[str] = Counter()
    bare_total: Counter[str] = Counter()
    bare_non_generic_hit: Counter[str] = Counter()
    recalled = 0
    for row in positives:
        text = str(row["text"])
        secret = str(row["secret"])
        family = str(row["family"])
        is_bare = bool(row["bare"])
        cleaned, rule_ids = redact.redact_detail(text)
        family_total[family] += 1
        if secret not in cleaned:
            recalled += 1
            family_hit[family] += 1
        if is_bare and family in STRUCTURED_FAMILIES:
            bare_total[family] += 1
            if any(r not in _GENERIC_RULE_IDS for r in rule_ids):
                bare_non_generic_hit[family] += 1
    recall = recalled / len(positives)

    evidence_fpr, evidence_fp_count, evidence_cat_total, evidence_cat_fp = _over_redaction(
        evidence_negatives
    )
    opaque_rate, opaque_hit_count, opaque_cat_total, opaque_cat_hit = _over_redaction(
        opaque_negatives
    )

    print(f"G1.7 recall={recall:.4f} ({recalled}/{len(positives)})")
    print(
        f"G1.7 evidence_text_fpr={evidence_fpr:.4f} ({evidence_fp_count}/{len(evidence_negatives)})"
    )
    n_opaque = len(opaque_negatives)
    print(f"G1.7 opaque_token_over_redaction={opaque_rate:.4f} ({opaque_hit_count}/{n_opaque})")
    print("per-family recall:")
    for family in sorted(family_total):
        total = family_total[family]
        hit = family_hit[family]
        print(f"  {family}: {hit}/{total} = {hit / total:.3f}")
    print("per-category negative table (evidence_text):")
    for family in sorted(evidence_cat_total):
        total = evidence_cat_total[family]
        fp = evidence_cat_fp[family]
        print(f"  {family}: {fp}/{total} = {fp / total:.3f}")
    print("per-category negative table (opaque_token):")
    for family in sorted(opaque_cat_total):
        total = opaque_cat_total[family]
        fp = opaque_cat_hit[family]
        print(f"  {family}: {fp}/{total} = {fp / total:.3f}")
    print("bare-context non-generic recall (structured families):")
    bare_recall_table: dict[str, tuple[int, int]] = {}
    for family in STRUCTURED_FAMILIES:
        total = bare_total[family]
        hit = bare_non_generic_hit[family]
        bare_recall_table[family] = (hit, total)
        rate = hit / total if total else float("nan")
        print(f"  {family}: {hit}/{total} = {rate:.3f}")

    assert recall >= MIN_RECALL, (
        f"recall {recall:.4f} below {MIN_RECALL}; per-family: "
        f"{ {f: (family_hit[f], family_total[f]) for f in sorted(family_total)} }"
    )
    bare_misses = {
        family: (hit, total)
        for family, (hit, total) in bare_recall_table.items()
        if total and hit / total < MIN_BARE_NON_GENERIC_RECALL
    }
    assert not bare_misses, (
        f"bare-context non-generic recall below {MIN_BARE_NON_GENERIC_RECALL} for: {bare_misses}"
    )
    evidence_table = {
        f: (evidence_cat_fp[f], evidence_cat_total[f]) for f in sorted(evidence_cat_total)
    }
    assert evidence_fpr <= MAX_EVIDENCE_TEXT_FPR, (
        f"evidence-text false-positive rate {evidence_fpr:.4f} above {MAX_EVIDENCE_TEXT_FPR}; "
        f"per-category: {evidence_table}"
    )
    # opaque_token_over_redaction is printed above; D-027 requires reporting
    # only, no assertion (redacting a meaningless-on-its-own public token
    # removes no evidence).


def test_redact_detail_exposes_firing_rule_ids() -> None:
    text = "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstT3BlbkFJabcdefghijklmnopqrst"
    cleaned, rule_ids = redact.redact_detail(text)
    assert rule_ids
    assert all(isinstance(r, str) for r in rule_ids)
    assert "[REDACTED:" in cleaned


def test_bare_context_family_counts_are_nonzero() -> None:
    """Sanity check that the corpus actually contains bare positives per family
    (a zero-total family would make the gate's bare assertion vacuous)."""
    rows = _load_corpus()
    bare_family_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["secret"] is not None and row["bare"]:
            bare_family_counts[str(row["family"])] += 1
    for family in STRUCTURED_FAMILIES:
        assert bare_family_counts.get(family, 0) >= 4, (
            f"{family} has too few bare positives to measure bare recall"
        )


def test_neg_class_is_set_from_category_not_outcome() -> None:
    """D-027: `neg_class` must be a per-category constant. Every row in a
    given category has the same class (never derived from whether the
    redactor happened to fire on that particular row)."""
    rows = _load_corpus()
    by_category: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["secret"] is None:
            by_category[str(row["family"])].add(str(row["neg_class"]))
    inconsistent = {cat: classes for cat, classes in by_category.items() if len(classes) != 1}
    assert not inconsistent, f"neg_class varies within a category: {inconsistent}"


def test_neg_class_values_are_valid() -> None:
    rows = _load_corpus()
    for row in rows:
        if row["secret"] is None:
            assert row["neg_class"] in (EVIDENCE_TEXT, OPAQUE_TOKEN)
