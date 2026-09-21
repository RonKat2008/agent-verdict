import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_review_finding_has_one_ledger_row_with_a_disposition() -> None:
    review = json.loads((ROOT / "docs/research/plan-review-2026-09-20.json").read_text())
    expected = [f["id"] for lens in review.values() for f in lens["findings"]]
    ledger = (ROOT / "docs/FINDINGS_LEDGER.md").read_text()
    rows = re.findall(r"^\| ([A-Z]+-\d+) \|(.*)\|$", ledger, re.M)
    first_table = rows[: len(expected)]
    assert sorted(i for i, _ in first_table) == sorted(expected)
    for finding_id, rest in first_table:
        cells = [c.strip() for c in rest.split("|")]
        assert cells[4], f"{finding_id} has no disposition"
