from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from workctx.mcp.contracts import (
    OUTPUT_SCHEMA,
    TOOL_CONTRACTS,
    InputContractError,
    validate_tool_arguments,
)

from .support import ALL_TOOL_NAMES, MUTATION_TOOL_NAMES, READ_TOOL_NAMES

EXPECTED_PROPERTIES = {
    "context_info": {"schema_version"},
    "workspace_validate": {"schema_version", "strict"},
    "search": {"schema_version", "query", "entity_types", "limit"},
    "ref_show": {"schema_version", "uri"},
    "ref_related": {"schema_version", "uri", "direction", "depth", "relations"},
    "ref_trace": {"schema_version", "uri", "include_history"},
    "context_pack": {
        "schema_version",
        "uri",
        "budget",
        "query",
        "include_history",
        "include_architecture",
    },
    "task_list": {
        "schema_version",
        "statuses",
        "owner",
        "waiting_on",
        "root_task",
        "parent_task",
    },
    "task_show": {"schema_version", "task"},
    "inbox_list": {"schema_version"},
    "audit_summary": {"schema_version"},
    "artifact_register": {"schema_version", "approved"},
    "proposal_validate": {"schema_version", "proposal", "approved"},
    "transaction_dry_run": {"schema_version", "proposal", "approved"},
    "transaction_apply": {"schema_version", "proposal", "approved"},
    "index_rebuild": {"schema_version", "approved"},
    "draft_save": {"schema_version", "approved"},
}

EXPECTED_REQUIRED = {
    "context_info": {"schema_version"},
    "workspace_validate": {"schema_version"},
    "search": {"schema_version", "query"},
    "ref_show": {"schema_version", "uri"},
    "ref_related": {"schema_version", "uri"},
    "ref_trace": {"schema_version", "uri"},
    "context_pack": {"schema_version", "uri"},
    "task_list": {"schema_version"},
    "task_show": {"schema_version", "task"},
    "inbox_list": {"schema_version"},
    "audit_summary": {"schema_version"},
    "artifact_register": {"schema_version", "approved"},
    "proposal_validate": {"schema_version", "proposal", "approved"},
    "transaction_dry_run": {"schema_version", "proposal", "approved"},
    "transaction_apply": {"schema_version", "proposal", "approved"},
    "index_rebuild": {"schema_version", "approved"},
    "draft_save": {"schema_version", "approved"},
}


def test_discovery_contract_is_exactly_the_adr_0012_surface_in_order() -> None:
    assert tuple(contract.name for contract in TOOL_CONTRACTS) == ALL_TOOL_NAMES
    assert len(TOOL_CONTRACTS) == 17
    assert sum(not contract.mutation for contract in TOOL_CONTRACTS) == 11
    assert sum(contract.mutation for contract in TOOL_CONTRACTS) == 6
    assert tuple(contract.name for contract in TOOL_CONTRACTS if not contract.mutation) == (
        READ_TOOL_NAMES
    )
    assert tuple(contract.name for contract in TOOL_CONTRACTS if contract.mutation) == (
        MUTATION_TOOL_NAMES
    )


@pytest.mark.parametrize("contract", TOOL_CONTRACTS, ids=lambda contract: contract.name)
def test_every_tool_has_a_closed_version_one_input_and_output_schema(contract: object) -> None:
    assert hasattr(contract, "input_schema")
    typed_contract = next(item for item in TOOL_CONTRACTS if item is contract)
    input_schema = typed_contract.input_schema
    output_schema = typed_contract.output_schema

    assert input_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert input_schema["type"] == "object"
    assert input_schema["additionalProperties"] is False
    assert set(input_schema["properties"]) == EXPECTED_PROPERTIES[typed_contract.name]
    assert set(input_schema["required"]) == EXPECTED_REQUIRED[typed_contract.name]
    assert input_schema["properties"]["schema_version"]["const"] == 1

    assert output_schema == OUTPUT_SCHEMA
    assert output_schema is not OUTPUT_SCHEMA
    assert output_schema["properties"]["schema_version"]["const"] == 1
    assert output_schema["additionalProperties"] is False
    assert set(output_schema["required"]) == {
        "schema_version",
        "ok",
        "context_id",
        "result",
        "warnings",
        "errors",
    }

    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)


@pytest.mark.parametrize("contract", TOOL_CONTRACTS, ids=lambda contract: contract.name)
def test_approval_is_structural_only_for_mutations(contract: object) -> None:
    typed_contract = next(item for item in TOOL_CONTRACTS if item is contract)
    properties = typed_contract.input_schema["properties"]
    required = typed_contract.input_schema["required"]

    if typed_contract.name in MUTATION_TOOL_NAMES:
        assert properties["approved"] == {
            "type": "boolean",
            "const": True,
            "description": "Explicit approval for this local mutation operation.",
        }
        assert "approved" in required
    else:
        assert "approved" not in properties
        assert "approved" not in required


def test_contract_schema_copies_cannot_mutate_the_discovery_boundary() -> None:
    contract = TOOL_CONTRACTS[0]
    input_copy = contract.copied_input_schema()
    output_copy = contract.copied_output_schema()
    original_input = deepcopy(contract.input_schema)
    original_output = deepcopy(contract.output_schema)

    input_copy["properties"].clear()
    output_copy["properties"].clear()

    assert contract.input_schema == original_input
    assert contract.output_schema == original_output


@pytest.mark.parametrize(
    ("arguments", "path"),
    [
        ({}, "$.schema_version"),
        ({"schema_version": 2}, "$.schema_version"),
        ({"schema_version": 1, "unexpected": True}, "$.unexpected"),
    ],
)
def test_runtime_contract_validator_rejects_invalid_input(
    arguments: dict[str, object],
    path: str,
) -> None:
    contract = TOOL_CONTRACTS[0]

    with pytest.raises(InputContractError) as captured:
        validate_tool_arguments(contract, arguments)

    assert captured.value.path == path


def test_runtime_contract_validator_requires_literal_true_approval() -> None:
    contract = next(item for item in TOOL_CONTRACTS if item.name == "index_rebuild")

    with pytest.raises(InputContractError) as captured:
        validate_tool_arguments(contract, {"schema_version": 1, "approved": False})

    assert captured.value.path == "$.approved"
