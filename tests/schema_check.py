"""Hand-written validator for schemas/ledger-v1.json.

Deliberately does not depend on a third-party jsonschema package (task-1-brief).
Understands only the small subset of JSON Schema that schemas/ledger-v1.json
actually uses: top-level required/properties, and a list of `allOf` branches
each shaped as `{"if": {"properties": {"event": {"const": ...}}}, "then": {...}}`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "ledger-v1.json"


def load_schema() -> dict[str, Any]:
    return dict(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _matches_type(value: object, type_spec: object) -> bool:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    for t in types:
        if t == "string" and isinstance(value, str):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
        if t == "null" and value is None:
            return True
        if t == "array" and isinstance(value, list):
            return True
        if t == "object" and isinstance(value, dict):
            return True
    return False


def _check_properties(row: dict[str, Any], properties: dict[str, Any]) -> None:
    for key, spec in properties.items():
        if key not in row:
            continue
        value = row[key]
        if "const" in spec:
            assert value == spec["const"], f"{key} must equal {spec['const']!r}, got {value!r}"
        if "enum" in spec:
            assert value in spec["enum"], f"{key} must be one of {spec['enum']}, got {value!r}"
        if "type" in spec:
            assert _matches_type(value, spec["type"]), f"{key} has wrong type: {value!r}"


def validate_row(row: dict[str, Any]) -> None:
    """Validate one ledger row against schemas/ledger-v1.json. Raises AssertionError."""
    schema = load_schema()
    for key in schema.get("required", []):
        assert key in row, f"missing required key: {key!r}"
    _check_properties(row, schema.get("properties", {}))
    for clause in schema.get("allOf", []):
        if_props = clause.get("if", {}).get("properties", {})
        applies = all(
            row.get(field) == cond.get("const")
            for field, cond in if_props.items()
            if "const" in cond
        )
        if not applies:
            continue
        then = clause.get("then", {})
        for key in then.get("required", []):
            assert key in row, f"event {row.get('event')!r} missing required key: {key!r}"
        _check_properties(row, then.get("properties", {}))
