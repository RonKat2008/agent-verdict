"""Demo site Task 3: numbers.json comes only from recorded measurements.

Every value on the site's Numbers band must trace to a sentence in
docs/DECISIONS.md D-033 (written from the M2 gate runs) and carry the
command that produced it. A missing pattern must raise, never default.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "site" / "scripts"))

import build_numbers  # noqa: E402

EXPECTED_IDS = {"stop_p50_ms", "stop_p95_ms", "recorder_p50_ms", "tests", "coverage_pct"}


def test_numbers_come_from_d033() -> None:
    data = build_numbers.build()
    by_id = {n["id"]: n for n in data}
    assert set(by_id) == EXPECTED_IDS
    d033 = build_numbers.d033_text()
    assert by_id["stop_p50_ms"]["value"] == float(re.search(r"Stop p50 ([\d.]+) ms", d033).group(1))
    assert by_id["stop_p95_ms"]["value"] == float(re.search(r"p95 ([\d.]+) ms", d033).group(1))
    assert by_id["tests"]["value"] == 1055
    assert by_id["coverage_pct"]["value"] == 94.38
    assert by_id["recorder_p50_ms"]["value"] == 38.0
    for entry in data:
        assert set(entry) == {
            "id",
            "label",
            "value",
            "unit",
            "source_command",
            "measured_on",
            "note",
        }
        assert entry["source_command"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["measured_on"])
        assert isinstance(entry["value"], (int, float)) and not isinstance(entry["value"], bool)


def test_a_missing_pattern_raises_instead_of_defaulting() -> None:
    with pytest.raises(build_numbers.MeasurementMissing):
        build_numbers.build(text="D-033 with nothing measured in it")


def test_written_file_matches_build(tmp_path: Path) -> None:
    out = tmp_path / "numbers.json"
    assert build_numbers.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == build_numbers.build()
