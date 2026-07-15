#!/usr/bin/env python3
"""Dependency-free validation for the JSON Schema subset used by RPI state."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class SchemaValidationError(ValueError):
    pass


MAX_VALIDATION_DEPTH = 64
MAX_VALIDATION_BYTES = 16 * 1024 * 1024


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _validate(value: Any, schema: dict[str, Any], schemas_dir: Path, location: str, depth: int = 0) -> list[str]:
    if depth > MAX_VALIDATION_DEPTH:
        return [f"{location}: maximum Schema validation depth exceeded"]
    if "$ref" in schema:
        ref = str(schema["$ref"])
        if "://" in ref or ref.startswith(("/", "#")) or ".." in Path(ref).parts:
            return [f"{location}: unsupported or unsafe schema reference {ref}"]
        ref_path = schemas_dir / ref
        try:
            referenced = json.loads(ref_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [f"{location}: cannot load schema reference {ref}: {exc}"]
        return _validate(value, referenced, schemas_dir, location, depth + 1)

    errors: list[str] = []
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected] if isinstance(expected, str) else []
    if expected_types and not any(_matches_type(value, item) for item in expected_types):
        return [f"{location}: expected {'|'.join(expected_types)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: value {value!r} is not in the allowed enum")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{location}: string is shorter than minLength")
        if schema.get("pattern") and re.search(str(schema["pattern"]), value) is None:
            errors.append(f"{location}: value does not match pattern {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{location}: value is below minimum {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{location}: array has fewer than minItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, schemas_dir, f"{location}[{index}]", depth + 1))
    if isinstance(value, dict):
        for key in schema.get("required", []) if isinstance(schema.get("required"), list) else []:
            if key not in value:
                errors.append(f"{location}: missing required property {key}")
        properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        for key, item in value.items():
            if key in properties and isinstance(properties[key], dict):
                errors.extend(_validate(item, properties[key], schemas_dir, f"{location}.{key}", depth + 1))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{location}: unexpected property {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(_validate(item, schema["additionalProperties"], schemas_dir, f"{location}.{key}", depth + 1))
    return errors


def validate(payload: Any, schema_name: str, project_dir: Path, location: str = "$") -> None:
    installed_schemas = Path(__file__).resolve().parents[1] / "schemas"
    schemas_dir = installed_schemas if installed_schemas.exists() else project_dir / ".rpi" / "schemas"
    schema_path = schemas_dir / schema_name
    try:
        encoded_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise SchemaValidationError(f"{location}: payload is not JSON serializable: {exc}") from exc
    if encoded_size > MAX_VALIDATION_BYTES:
        raise SchemaValidationError(f"{location}: payload exceeds {MAX_VALIDATION_BYTES} bytes")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaValidationError(f"cannot load schema {schema_name}: {exc}") from exc
    errors = _validate(payload, schema, schemas_dir, location)
    if errors:
        raise SchemaValidationError("; ".join(errors))


def validate_items(payloads: list[Any], schema_name: str, project_dir: Path, location: str) -> None:
    for index, payload in enumerate(payloads):
        validate(payload, schema_name, project_dir, f"{location}[{index}]")
