"""Hand-maintained version-1 MCP tool contracts.

The schemas in this module are the public MCP compatibility boundary.  They are
intentionally independent from Pydantic schema generation; runtime validation
uses the same dictionaries so discovery and execution cannot drift silently.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final

from workctx.domain import EntityType, RelationType, TaskStatus

SCHEMA_VERSION: Final = 1

_SCHEMA_VERSION_PROPERTY: dict[str, Any] = {
    "type": "integer",
    "const": SCHEMA_VERSION,
    "description": "Work Context MCP schema version.",
}
_APPROVED_PROPERTY: dict[str, Any] = {
    "type": "boolean",
    "const": True,
    "description": "Explicit approval for this local mutation operation.",
}
_URI_PROPERTY: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 2000,
    "description": "Canonical durable URI.",
}

ERROR_CATEGORIES: Final[tuple[str, ...]] = (
    "user_correctable",
    "usage_configuration",
    "context_boundary",
    "conflict",
    "unavailable_dependency",
    "partial_success",
    "internal_failure",
)


@dataclass(frozen=True, slots=True)
class ToolContract:
    """One discoverable MCP tool contract without an SDK dependency."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    mutation: bool = False

    def copied_input_schema(self) -> dict[str, Any]:
        return deepcopy(self.input_schema)

    def copied_output_schema(self) -> dict[str, Any]:
        return deepcopy(self.output_schema)


class InputContractError(ValueError):
    """Sanitized structural input failure that never retains a supplied value."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.safe_message = message
        super().__init__(message)


def _input_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    mutation: bool = False,
) -> dict[str, Any]:
    declared: dict[str, Any] = {"schema_version": deepcopy(_SCHEMA_VERSION_PROPERTY)}
    required_fields = ["schema_version", *required]
    if mutation:
        declared["approved"] = deepcopy(_APPROVED_PROPERTY)
        required_fields.append("approved")
    declared.update(properties or {})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": declared,
        "required": required_fields,
    }


_DIAGNOSTIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "code": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": r"^[A-Z][A-Z0-9_-]*$",
        },
        "category": {"type": "string", "enum": list(ERROR_CATEGORIES)},
        "severity": {"type": "string", "enum": ["error", "warning", "advisory"]},
        "message": {"type": "string", "minLength": 1, "maxLength": 500},
        "path": {"type": ["string", "null"], "maxLength": 500},
        "repair_action": {"type": ["string", "null"], "maxLength": 500},
    },
    "required": ["code", "category", "severity", "message", "path", "repair_action"],
}

OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": deepcopy(_SCHEMA_VERSION_PROPERTY),
        "ok": {"type": "boolean"},
        "context_id": {
            "type": "string",
            "minLength": 2,
            "maxLength": 64,
            "pattern": r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        },
        "result": {"type": "object", "additionalProperties": True},
        "warnings": {"type": "array", "items": deepcopy(_DIAGNOSTIC_SCHEMA)},
        "errors": {"type": "array", "items": deepcopy(_DIAGNOSTIC_SCHEMA)},
    },
    "required": ["schema_version", "ok", "context_id", "result", "warnings", "errors"],
}


def _contract(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    mutation: bool = False,
) -> ToolContract:
    return ToolContract(
        name=name,
        description=description,
        input_schema=_input_schema(properties, required=required, mutation=mutation),
        output_schema=deepcopy(OUTPUT_SCHEMA),
        mutation=mutation,
    )


_ENTITY_TYPE_ARRAY = {
    "type": "array",
    "items": {"type": "string", "enum": [item.value for item in EntityType]},
    "uniqueItems": True,
}
_RELATION_ARRAY = {
    "type": "array",
    "items": {"type": "string", "enum": [item.value for item in RelationType]},
    "uniqueItems": True,
}
_TASK_STATUS_ARRAY = {
    "type": "array",
    "items": {"type": "string", "enum": [item.value for item in TaskStatus]},
    "uniqueItems": True,
}
_TASK_ID_PROPERTY = {
    "type": "string",
    "pattern": r"^TASK-[0-9]{4}-[0-9]{3}(?:-ST[0-9]{2})?$",
}


TOOL_CONTRACTS: Final[tuple[ToolContract, ...]] = (
    _contract(
        "context_info",
        "Return the bound context configuration and local doctor summary.",
    ),
    _contract(
        "workspace_validate",
        "Validate canonical workspace integrity without repairing it.",
        {"strict": {"type": "boolean", "default": False}},
    ),
    _contract(
        "search",
        "Search the bound context's SQLite full-text projection.",
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 2000},
            "entity_types": deepcopy(_ENTITY_TYPE_ARRAY),
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 20},
        },
        required=("query",),
    ),
    _contract(
        "ref_show",
        "Resolve one canonical workctx://, artifact://, or repo:// reference.",
        {"uri": deepcopy(_URI_PROPERTY)},
        required=("uri",),
    ),
    _contract(
        "ref_related",
        "Traverse typed relations around one canonical Work Context reference.",
        {
            "uri": deepcopy(_URI_PROPERTY),
            "direction": {
                "type": "string",
                "enum": ["outbound", "inbound", "both"],
                "default": "both",
            },
            "depth": {"type": "integer", "minimum": 0, "maximum": 20, "default": 1},
            "relations": deepcopy(_RELATION_ARRAY),
        },
        required=("uri",),
    ),
    _contract(
        "ref_trace",
        "Trace claims and observations to exact source locators.",
        {
            "uri": deepcopy(_URI_PROPERTY),
            "include_history": {"type": "boolean", "default": False},
        },
        required=("uri",),
    ),
    _contract(
        "context_pack",
        "Build a deterministic, bounded ten-section context pack.",
        {
            "uri": deepcopy(_URI_PROPERTY),
            "budget": {"type": "integer", "minimum": 0, "default": 12000},
            "query": {"type": ["string", "null"], "maxLength": 2000, "default": None},
            "include_history": {"type": "boolean", "default": False},
            "include_architecture": {"type": "boolean", "default": False},
        },
        required=("uri",),
    ),
    _contract(
        "task_list",
        "List projected tasks using deterministic context-bound filters.",
        {
            "statuses": deepcopy(_TASK_STATUS_ARRAY),
            "owner": {"type": ["string", "null"], "maxLength": 2000},
            "waiting_on": {"type": ["string", "null"], "maxLength": 2000},
            "root_task": {**deepcopy(_TASK_ID_PROPERTY), "type": ["string", "null"]},
            "parent_task": {**deepcopy(_TASK_ID_PROPERTY), "type": ["string", "null"]},
        },
    ),
    _contract(
        "task_show",
        "Return one projected task by task ID or canonical task URI.",
        {
            "task": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
            }
        },
        required=("task",),
    ),
    _contract(
        "inbox_list",
        "List registered inbox artifacts when the ingestion engine is installed.",
    ),
    _contract(
        "audit_summary",
        "Return the verified canonical transaction-ledger summary.",
    ),
    _contract(
        "artifact_register",
        "Register an inbox artifact when the ingestion engine is installed.",
        mutation=True,
    ),
    _contract(
        "proposal_validate",
        "Validate a typed transaction proposal without applying it.",
        {"proposal": {"type": "object", "additionalProperties": True}},
        required=("proposal",),
        mutation=True,
    ),
    _contract(
        "transaction_dry_run",
        "Compute exact transaction effects without canonical mutation.",
        {"proposal": {"type": "object", "additionalProperties": True}},
        required=("proposal",),
        mutation=True,
    ),
    _contract(
        "transaction_apply",
        "Atomically apply one reviewed transaction proposal in the bound context.",
        {"proposal": {"type": "object", "additionalProperties": True}},
        required=("proposal",),
        mutation=True,
    ),
    _contract(
        "index_rebuild",
        "Rebuild the disposable SQLite/FTS projection from canonical files.",
        mutation=True,
    ),
    _contract(
        "draft_save",
        "Persist a local outbox draft when the Wave 4 draft engine is installed.",
        mutation=True,
    ),
)

TOOL_CONTRACT_BY_NAME: Final[dict[str, ToolContract]] = {
    contract.name: contract for contract in TOOL_CONTRACTS
}


def validate_tool_arguments(contract: ToolContract, arguments: object) -> dict[str, Any]:
    """Validate arguments against the exact schema subset used by the contracts."""

    _validate_value(arguments, contract.input_schema, "$", required=True)
    if not isinstance(arguments, dict):  # established by the validator
        raise InputContractError("$", "Tool arguments must be an object.")
    return arguments


def _validate_value(value: object, schema: dict[str, Any], path: str, *, required: bool) -> None:
    if value is None and not required and _accepts_null(schema):
        return

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        raise InputContractError(path, "Tool input has the wrong JSON type.")

    if "const" in schema and value != schema["const"]:
        raise InputContractError(path, "Tool input does not match the required constant.")
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        raise InputContractError(path, "Tool input is outside the allowed vocabulary.")

    if isinstance(value, dict):
        _validate_object(value, schema, path)
    elif isinstance(value, list):
        _validate_array(value, schema, path)
    elif isinstance(value, str):
        _validate_string(value, schema, path)
    elif type(value) is int:
        _validate_integer(value, schema, path)


def _validate_object(value: dict[object, object], schema: dict[str, Any], path: str) -> None:
    if not all(isinstance(key, str) for key in value):
        raise InputContractError(path, "Tool input object keys must be strings.")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required_names = schema.get("required", [])
    if isinstance(required_names, list):
        for name in required_names:
            if isinstance(name, str) and name not in value:
                raise InputContractError(f"{path}.{name}", "Required tool input is missing.")
    if schema.get("additionalProperties") is False:
        for name in value:
            if name not in properties:
                raise InputContractError(
                    f"{path}.{name}",
                    "Unexpected tool input is not allowed.",
                )
    for name, item in value.items():
        child_schema = properties.get(name)
        if isinstance(child_schema, dict):
            _validate_value(item, child_schema, f"{path}.{name}", required=True)


def _validate_array(value: list[object], schema: dict[str, Any], path: str) -> None:
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(value) < minimum:
        raise InputContractError(path, "Tool input array is too short.")
    if isinstance(maximum, int) and len(value) > maximum:
        raise InputContractError(path, "Tool input array is too long.")
    if schema.get("uniqueItems") is True:
        serialized = [repr(item) for item in value]
        if len(serialized) != len(set(serialized)):
            raise InputContractError(path, "Tool input array items must be unique.")
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value):
            _validate_value(item, item_schema, f"{path}[{index}]", required=True)


def _validate_string(value: str, schema: dict[str, Any], path: str) -> None:
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and len(value) < minimum:
        raise InputContractError(path, "Tool input string is too short.")
    if isinstance(maximum, int) and len(value) > maximum:
        raise InputContractError(path, "Tool input string is too long.")
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
        raise InputContractError(path, "Tool input string has an invalid format.")


def _validate_integer(value: int, schema: dict[str, Any], path: str) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int) and value < minimum:
        raise InputContractError(path, "Tool input integer is below the allowed minimum.")
    if isinstance(maximum, int) and value > maximum:
        raise InputContractError(path, "Tool input integer is above the allowed maximum.")


def _accepts_null(schema: dict[str, Any]) -> bool:
    expected = schema.get("type")
    return isinstance(expected, list) and "null" in expected


def _matches_type(value: object, expected: object) -> bool:
    kinds = expected if isinstance(expected, list) else [expected]
    return any(_matches_one_type(value, kind) for kind in kinds)


def _matches_one_type(value: object, expected: object) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return type(value) is int
    if expected == "boolean":
        return type(value) is bool
    if expected == "null":
        return value is None
    return True


__all__ = [
    "ERROR_CATEGORIES",
    "OUTPUT_SCHEMA",
    "SCHEMA_VERSION",
    "TOOL_CONTRACTS",
    "TOOL_CONTRACT_BY_NAME",
    "InputContractError",
    "ToolContract",
    "validate_tool_arguments",
]
