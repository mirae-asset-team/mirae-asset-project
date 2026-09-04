"""Allow-listed Tool Registry with bounded JSON-compatible input validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping

from .evidence_sufficiency import EvidenceSufficiencyChecker
from .tool_contracts import COMMON_OUTPUT_SCHEMA, ToolResponse, empty_bundle


ToolHandler = Callable[[Mapping[str, object]], ToolResponse]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    handler: ToolHandler

    def public_contract(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
        }


class ToolInputError(ValueError):
    pass


def _is_type(value: object, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return False


def _validate_value(field_name: str, value: object, schema: Mapping[str, object]) -> None:
    expected = schema.get("type")
    allowed_types = [expected] if isinstance(expected, str) else list(expected or [])
    if allowed_types and not any(_is_type(value, item) for item in allowed_types):
        raise ToolInputError(f"invalid_type:{field_name}")
    if "enum" in schema and value not in schema["enum"]:  # type: ignore[operator]
        raise ToolInputError(f"invalid_value:{field_name}")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise ToolInputError(f"string_too_short:{field_name}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ToolInputError(f"string_too_long:{field_name}")
        if schema.get("format") == "date":
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ToolInputError(f"invalid_date:{field_name}") from exc
            if not 1900 <= parsed.year <= 2200:
                raise ToolInputError(f"date_out_of_range:{field_name}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:  # type: ignore[operator]
            raise ToolInputError(f"value_too_small:{field_name}")
        if "maximum" in schema and value > schema["maximum"]:  # type: ignore[operator]
            raise ToolInputError(f"value_too_large:{field_name}")
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            _validate_value(f"{field_name}[{index}]", item, schema["items"])  # type: ignore[arg-type]


def validate_tool_input(payload: object, schema: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ToolInputError("input_must_be_object")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ToolInputError("tool_schema_invalid")
    required = {str(item) for item in schema.get("required", [])}  # type: ignore[union-attr]
    missing = sorted(field for field in required if field not in payload)
    if missing:
        raise ToolInputError("missing_required:" + ",".join(missing))
    unknown = sorted(str(field) for field in payload if field not in properties)
    if unknown and schema.get("additionalProperties") is False:
        raise ToolInputError("unknown_fields:" + ",".join(unknown))
    normalized = dict(payload)
    for field_name, value in normalized.items():
        field_schema = properties.get(field_name)
        if not isinstance(field_schema, Mapping):
            raise ToolInputError(f"tool_schema_field_invalid:{field_name}")
        _validate_value(str(field_name), value, field_schema)
    start = normalized.get("start_date")
    end = normalized.get("end_date")
    if isinstance(start, str) and isinstance(end, str) and start > end:
        raise ToolInputError("invalid_date_range:start_after_end")
    return normalized


class ToolRegistry:
    def __init__(self, *, sufficiency_checker: EvidenceSufficiencyChecker | None = None) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._checker = sufficiency_checker or EvidenceSufficiencyChecker()

    def register(self, definition: ToolDefinition) -> None:
        if not definition.name or definition.name in self._definitions:
            raise ValueError("tool_name_missing_or_duplicate")
        self._definitions[definition.name] = definition

    def list_tools(self) -> list[dict[str, object]]:
        return [self._definitions[name].public_contract() for name in sorted(self._definitions)]

    def dispatch(self, name: str, payload: object) -> dict[str, object]:
        definition = self._definitions.get(name)
        if definition is None:
            return ToolResponse(
                "invalid_request", str(name), {}, empty_bundle(str(name)),
                ["tool_not_registered"], {"validation_errors": ["tool_not_registered"]},
            ).to_dict()
        try:
            request = validate_tool_input(payload, definition.input_schema)
        except ToolInputError as exc:
            return ToolResponse(
                "invalid_request", name, {}, empty_bundle(name, payload if isinstance(payload, Mapping) else None),
                [str(exc)], {"validation_errors": [str(exc)]},
            ).to_dict()
        try:
            response = definition.handler(request)
        except Exception as exc:
            return ToolResponse(
                "error", name, {}, empty_bundle(name, request),
                ["tool_execution_error"], {"error_type": type(exc).__name__},
            ).to_dict()
        if response.tool_name != name:
            return ToolResponse(
                "error", name, {}, empty_bundle(name, request),
                ["tool_response_name_mismatch"], {},
            ).to_dict()
        if response.status in {"invalid_request", "error"}:
            return response.to_dict()
        sufficiency = self._checker.check(name, request, response.evidence_bundle, response.data)
        response.evidence_bundle.sufficiency = sufficiency.status
        response.metadata["sufficiency_check"] = sufficiency.to_dict()
        if sufficiency.status == "partial" and response.status == "success":
            response.status = "partial"
        elif sufficiency.status == "insufficient":
            response.status = "insufficient"
        return response.to_dict()


def object_schema(
    properties: Mapping[str, object],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


__all__ = [
    "COMMON_OUTPUT_SCHEMA",
    "ToolDefinition",
    "ToolInputError",
    "ToolRegistry",
    "object_schema",
    "validate_tool_input",
]
