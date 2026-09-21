"""Gate G1.7, as redefined by D-026 (docs/DECISIONS.md) in fix round 2.

Two independent held-out probes showed that unlabeled base64 asset blobs
(wasm, fonts, source maps, protobuf) are statistically indistinguishable
from real high-entropy secrets, so no text-only rule separates them. D-026
splits negatives into TEXT and `opaque_blob` categories -- set by the
corpus generator from the CATEGORY, never from the redaction outcome (see
`gen_corpus_negatives.OPAQUE_BLOB_CATEGORIES`) -- and gates each
differently:

- overall recall >= 0.95 (all positives, all contexts)
- bare-context, non-generic-rule recall >= 0.90 for the 15 structured
  families (fix round 1)
- false-positive rate over TEXT negatives (`opaque_blob == False`) <= 0.02
- over-redaction rate over `opaque_blob == True` negatives <= 0.30

All four numbers are printed. The corpus
(tests/fixtures/secrets_corpus.jsonl, from `python3 scripts/gen_redact.py
--corpus` / scripts/gen_corpus.py) must never be hand-tuned to make these
numbers look better than they are, and no existing text category is
reclassified as a blob to move it out of the stricter bucket.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin" / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gen_corpus import STRUCTURED_FAMILIES  # noqa: E402
from verdict_hot import redact  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parent / "fixtures" / "secrets_corpus.jsonl"
MIN_RECALL = 0.95
MIN_BARE_NON_GENERIC_RECALL = 0.90
MAX_TEXT_FPR = 0.02
MAX_BLOB_OVER_REDACTION = 0.30
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


def test_redaction_gate_g1_7() -> None:
    rows = _load_corpus()
    positives = [r for r in rows if r["secret"] is not None]
    negatives = [r for r in rows if r["secret"] is None]
    text_negatives = [r for r in negatives if not r["opaque_blob"]]
    blob_negatives = [r for r in negatives if r["opaque_blob"]]
    assert len(positives) >= 150, "expected at least 150 positives in the corpus"
    assert len(text_negatives) >= 150, "expected at least 150 text negatives"
    assert len(blob_negatives) >= 20, "expected a meaningful opaque_blob sample"

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

    text_fpr, text_fp_count, text_cat_total, text_cat_fp = _over_redaction(text_negatives)
    blob_rate, blob_hit_count, blob_cat_total, blob_cat_hit = _over_redaction(blob_negatives)

    print(f"G1.7 recall={recall:.4f} ({recalled}/{len(positives)})")
    print(f"G1.7 text_fpr={text_fpr:.4f} ({text_fp_count}/{len(text_negatives)})")
    print(f"G1.7 blob_over_redaction={blob_rate:.4f} ({blob_hit_count}/{len(blob_negatives)})")
    print("per-family recall:")
    for family in sorted(family_total):
        total = family_total[family]
        hit = family_hit[family]
        print(f"  {family}: {hit}/{total} = {hit / total:.3f}")
    print("per-category negative table (text):")
    for family in sorted(text_cat_total):
        total = text_cat_total[family]
        fp = text_cat_fp[family]
        print(f"  {family}: {fp}/{total} = {fp / total:.3f}")
    print("per-category negative table (opaque_blob):")
    for family in sorted(blob_cat_total):
        total = blob_cat_total[family]
        fp = blob_cat_hit[family]
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
    text_table = {f: (text_cat_fp[f], text_cat_total[f]) for f in sorted(text_cat_total)}
    assert text_fpr <= MAX_TEXT_FPR, (
        f"text false-positive rate {text_fpr:.4f} above {MAX_TEXT_FPR}; per-category: {text_table}"
    )
    blob_table = {f: (blob_cat_hit[f], blob_cat_total[f]) for f in sorted(blob_cat_total)}
    assert blob_rate <= MAX_BLOB_OVER_REDACTION, (
        f"opaque_blob over-redaction {blob_rate:.4f} above {MAX_BLOB_OVER_REDACTION}; "
        f"per-category: {blob_table}"
    )


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


def test_opaque_blob_flag_is_set_from_category_not_outcome() -> None:
    """D-026: `opaque_blob` must be a per-category constant. Every row in a
    given category has the same flag value (never derived from whether the
    redactor happened to fire on that particular row)."""
    rows = _load_corpus()
    by_category: dict[str, set[bool]] = defaultdict(set)
    for row in rows:
        if row["secret"] is None:
            by_category[str(row["family"])].add(bool(row["opaque_blob"]))
    inconsistent = {cat: flags for cat, flags in by_category.items() if len(flags) != 1}
    assert not inconsistent, f"opaque_blob varies within a category: {inconsistent}"
