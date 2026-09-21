"""Hand-written validators for schemas/ledger-v1.json and schemas/policy-v1.json.

Deliberately does not depend on a third-party jsonschema package (task-1-brief).
`validate_row` understands only the small subset of JSON Schema that
schemas/ledger-v1.json actually uses: top-level required/properties, and a
list of `allOf` branches each shaped as
`{"if": {"properties": {"event": {"const": ...}}}, "then": {...}}`.

`validate_policy` (task-3-brief) walks schemas/policy-v1.json recursively:
nested `object`/`properties`/`required` and `array`/`items`, plus `type`,
`enum`, and `const` at any node. That subset is all schemas/policy-v1.json
needs; it is not a general JSON Schema implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "ledger-v1.json"
POLICY_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "policy-v1.json"


def load_schema() -> dict[str, Any]:
    return dict(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def load_policy_schema() -> dict[str, Any]:
    return dict(json.loads(POLICY_SCHEMA_PATH.read_text(encoding="utf-8")))


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


def _validate_node(value: Any, node_schema: dict[str, Any], path: str) -> None:
    if "const" in node_schema:
        assert value == node_schema["const"], f"{path} must equal {node_schema['const']!r}"
    if "enum" in node_schema:
        assert value in node_schema["enum"], f"{path} must be one of {node_schema['enum']}"
    if "type" in node_schema:
        assert _matches_type(value, node_schema["type"]), f"{path} has wrong type: {value!r}"
    if isinstance(value, dict):
        for key in node_schema.get("required", []):
            assert key in value, f"{path}.{key} is required but missing"
        for key, subschema in node_schema.get("properties", {}).items():
            if key in value:
                _validate_node(value[key], subschema, f"{path}.{key}")
    if isinstance(value, list) and "items" in node_schema:
        for index, item in enumerate(value):
            _validate_node(item, node_schema["items"], f"{path}[{index}]")


def validate_policy(policy_dict: dict[str, Any]) -> None:
    """Validate a policy dict against schemas/policy-v1.json. Raises AssertionError."""
    schema = load_policy_schema()
    _validate_node(policy_dict, schema, "$")
