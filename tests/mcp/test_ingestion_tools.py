from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from workctx.ingestion import RegisterRequest
from workctx.mcp.application import McpToolService
from workctx.mcp.contracts import (
    TOOL_CONTRACT_BY_NAME,
    InputContractError,
    validate_tool_arguments,
)
from workctx.services.contexts import initialize_context

FIXTURE = Path(__file__).parent / "fixtures" / "artifact-register.json"
SECRET = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _request_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    mapped = {
        {
            "origin": "source_origin",
            "event_date": "event_at",
        }.get(key, key): value
        for key, value in arguments.items()
        if key not in {"schema_version", "approved"}
    }
    return mapped


@pytest.mark.parametrize("arguments", _fixture()["positive"])
def test_artifact_register_positive_fixtures_align_schema_and_engine_model(
    arguments: dict[str, Any],
) -> None:
    contract = TOOL_CONTRACT_BY_NAME["artifact_register"]

    Draft202012Validator(contract.input_schema).validate(arguments)
    assert validate_tool_arguments(contract, arguments) == arguments
    RegisterRequest.model_validate(_request_payload(arguments))


@pytest.mark.parametrize(
    "fixture",
    _fixture()["negative"],
    ids=lambda fixture: fixture["name"],
)
def test_artifact_register_negative_fixtures_fail_schema_and_engine_model(
    fixture: dict[str, Any],
) -> None:
    arguments = fixture["data"]
    contract = TOOL_CONTRACT_BY_NAME["artifact_register"]

    assert not Draft202012Validator(contract.input_schema).is_valid(arguments)
    with pytest.raises(InputContractError):
        validate_tool_arguments(contract, arguments)
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_request_payload(arguments))


@pytest.fixture
def ingestion_service(tmp_path: Path) -> tuple[Path, McpToolService]:
    root = tmp_path / "mcp-ingestion"
    initialize_context(root, name="Fictional MCP Ingestion", context_id="mcp-ingestion")
    return root, McpToolService(root)


def test_inbox_list_and_artifact_register_are_live_and_map_metadata(
    ingestion_service: tuple[Path, McpToolService],
) -> None:
    root, service = ingestion_service
    raw_path = "00_inbox/raw/mcp-note.txt"
    (root / raw_path).write_text("Fictional MCP evidence.\n", encoding="utf-8")

    before = service.invoke("inbox_list", {"schema_version": 1})
    assert before.envelope.ok is True
    assert before.envelope.result == {"artifacts": [], "count": 0}

    registered = service.invoke(
        "artifact_register",
        {
            "schema_version": 1,
            "approved": True,
            "path": raw_path,
            "source_type": "note",
            "origin": "fictional://mcp/410",
            "event_date": "2026-08-02T20:00:00-06:00",
        },
    )
    assert registered.envelope.ok is True, registered.envelope.errors
    manifest = registered.envelope.result["registration"]["artifact"]["manifest"]
    assert manifest["source_type"] == "note"
    assert manifest["source_origin"] == "fictional://mcp/410"
    assert manifest["event_at"] == "2026-08-02T20:00:00-06:00"

    after = service.invoke("inbox_list", {"schema_version": 1})
    assert after.envelope.ok is True
    assert after.envelope.result["count"] == 1
    assert after.envelope.result["artifacts"][0]["reference"].startswith("artifact://sha256/")


@pytest.mark.parametrize("approved", [None, False])
def test_artifact_register_approval_denial_does_not_mutate(
    ingestion_service: tuple[Path, McpToolService],
    approved: bool | None,
) -> None:
    root, service = ingestion_service
    raw_path = "00_inbox/raw/approval-note.txt"
    source = root / raw_path
    source.write_text("Fictional approval evidence.\n", encoding="utf-8")
    arguments: dict[str, object] = {
        "schema_version": 1,
        "path": raw_path,
        "source_type": "note",
    }
    if approved is not None:
        arguments["approved"] = approved

    denied = service.invoke("artifact_register", arguments)

    assert denied.envelope.ok is False
    assert denied.envelope.errors[0].code == "APPROVAL_REQUIRED"
    assert denied.envelope.errors[0].path == "$.approved"
    assert source.is_file()
    assert service.invoke("inbox_list", {"schema_version": 1}).envelope.result["count"] == 0


def test_artifact_register_denials_and_sanitization_match_mcp_boundary(
    ingestion_service: tuple[Path, McpToolService],
) -> None:
    root, service = ingestion_service
    escaped = service.invoke(
        "artifact_register",
        {
            "schema_version": 1,
            "approved": True,
            "path": "00_inbox/raw/../private.txt",
            "source_type": "note",
        },
    )
    assert escaped.envelope.ok is False
    assert escaped.envelope.errors[0].code == "INVALID_INPUT"
    assert "private.txt" not in escaped.model_dump_json()

    missing_name = f"missing-{SECRET}.txt"
    missing = service.invoke(
        "artifact_register",
        {
            "schema_version": 1,
            "approved": True,
            "path": f"00_inbox/raw/{missing_name}",
            "source_type": "note",
        },
    )
    assert missing.envelope.ok is False
    assert missing.envelope.errors[0].code == "USER_CORRECTABLE"
    assert SECRET not in missing.model_dump_json()

    secret_path = "00_inbox/raw/suspected-secret.txt"
    (root / secret_path).write_text(f"api_key={SECRET}\n", encoding="utf-8")
    quarantined = service.invoke(
        "artifact_register",
        {
            "schema_version": 1,
            "approved": True,
            "path": secret_path,
            "source_type": "note",
        },
    )
    assert quarantined.envelope.ok is True
    assert (
        quarantined.envelope.result["registration"]["artifact"]["manifest"]["status"]
        == "quarantined"
    )
    assert SECRET not in quarantined.model_dump_json()
