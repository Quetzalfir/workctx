from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from drafting.support import PERSON_URI, draft_payload, initialize_drafting_context
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from workctx.drafting import DraftPayload, list_drafts
from workctx.mcp.application import McpToolService
from workctx.mcp.contracts import (
    TOOL_CONTRACT_BY_NAME,
    InputContractError,
    validate_tool_arguments,
)
from workctx.transactions import verify_ledger

FIXTURE = Path(__file__).parent / "fixtures" / "draft-save.json"
SECRET = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _payload(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in arguments.items() if key != "approved"}


def _arguments() -> dict[str, Any]:
    arguments = draft_payload().model_dump(mode="json")
    arguments["approved"] = True
    return arguments


@pytest.mark.parametrize("arguments", _fixture()["positive"])
def test_draft_save_positive_fixtures_align_schema_and_engine_model(
    arguments: dict[str, Any],
) -> None:
    contract = TOOL_CONTRACT_BY_NAME["draft_save"]

    Draft202012Validator(contract.input_schema).validate(arguments)
    assert validate_tool_arguments(contract, arguments) == arguments
    DraftPayload.model_validate(_payload(arguments))


@pytest.mark.parametrize(
    "fixture",
    _fixture()["negative"],
    ids=lambda fixture: fixture["name"],
)
def test_draft_save_negative_fixtures_fail_schema_and_engine_model(
    fixture: dict[str, Any],
) -> None:
    arguments = fixture["data"]
    contract = TOOL_CONTRACT_BY_NAME["draft_save"]

    assert not Draft202012Validator(contract.input_schema).is_valid(arguments)
    with pytest.raises(InputContractError):
        validate_tool_arguments(contract, arguments)
    with pytest.raises(ValidationError):
        DraftPayload.model_validate(_payload(arguments))


@pytest.fixture
def drafting_service(tmp_path: Path) -> tuple[Path, McpToolService]:
    root = initialize_drafting_context(tmp_path / "mcp-drafting")
    return root, McpToolService(root)


def test_mcp_draft_save_is_live_and_returns_transaction_receipt(
    drafting_service: tuple[Path, McpToolService],
) -> None:
    root, service = drafting_service

    response = service.invoke("draft_save", _arguments())

    assert response.envelope.ok is True, response.envelope.errors
    assert response.envelope.result["operation"] == "created"
    assert response.envelope.result["draft"]["delivery_state"] == "unsent"
    assert response.envelope.result["receipt"]["committed"] is True
    assert len(list_drafts(root)) == 1
    assert verify_ledger(root).event_count == 1


@pytest.mark.parametrize("approved", [None, False])
def test_mcp_draft_save_approval_gate_denies_without_mutation(
    drafting_service: tuple[Path, McpToolService],
    approved: bool | None,
) -> None:
    root, service = drafting_service
    arguments = _arguments()
    if approved is None:
        arguments.pop("approved")
    else:
        arguments["approved"] = approved

    response = service.invoke("draft_save", arguments)

    assert response.envelope.ok is False
    assert response.envelope.errors[0].code == "APPROVAL_REQUIRED"
    assert response.envelope.errors[0].path == "$.approved"
    assert list_drafts(root) == ()
    assert verify_ledger(root).event_count == 0


def test_mcp_draft_save_denials_and_secret_sanitization_match_boundary(
    drafting_service: tuple[Path, McpToolService],
) -> None:
    root, service = drafting_service
    foreign = _arguments()
    foreign_uri = "workctx://other-context/person/PER-alex-rivera"
    foreign["recipient_uri"] = foreign_uri

    denied = service.invoke("draft_save", foreign)

    assert denied.envelope.ok is False
    assert denied.envelope.errors[0].code == "REF-CONTEXT-MISMATCH"
    assert foreign_uri not in denied.model_dump_json()

    secret = _arguments()
    secret["body"] = f"api_key={SECRET}\n"
    refused = service.invoke("draft_save", secret)

    assert refused.envelope.ok is False
    assert refused.envelope.errors[0].code == "USER_CORRECTABLE"
    assert SECRET not in refused.model_dump_json()
    assert PERSON_URI not in refused.model_dump_json()
    assert list_drafts(root) == ()
    assert verify_ledger(root).event_count == 0
