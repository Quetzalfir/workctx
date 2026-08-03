from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from workctx.errors import UserCorrectableError
from workctx.mcp.application import McpToolService
from workctx.mcp.models import ToolResponse

from .support import (
    ALL_TOOL_NAMES,
    MUTATION_TOOL_NAMES,
    TASK_ID,
    TASK_URI,
    boundary_arguments,
    error_codes,
    initialize_mcp_context,
    initialize_mcp_transaction,
    mutation_arguments,
)


@pytest.fixture
def live_service(tmp_path: Path) -> McpToolService:
    root = initialize_mcp_context(tmp_path / "fictional-context")
    return McpToolService(root)


def _assert_success(response: ToolResponse, tool_name: str) -> dict[str, Any]:
    assert response.envelope.ok is True, response.envelope.model_dump(mode="json")
    assert response.envelope.schema_version == 1
    assert response.envelope.context_id == "fictional-context"
    assert response.envelope.errors == ()
    assert response.is_error is False
    assert response.summary.startswith(f"{tool_name} completed")
    return response.envelope.result


@pytest.mark.integration
def test_all_live_read_tools_delegate_to_integrated_engines(
    live_service: McpToolService,
) -> None:
    context = _assert_success(
        live_service.invoke("context_info", {"schema_version": 1}),
        "context_info",
    )
    assert context["context"]["id"] == "fictional-context"
    assert "checks" in context["doctor"]

    validation = live_service.invoke(
        "workspace_validate",
        {"schema_version": 1, "strict": False},
    )
    assert validation.envelope.context_id == "fictional-context"
    assert validation.envelope.result["strict"] is False
    assert "issues" in validation.envelope.result

    search = _assert_success(
        live_service.invoke(
            "search",
            {"schema_version": 1, "query": "authentication", "limit": 10},
        ),
        "search",
    )
    assert search["count"] >= 1
    assert len(search["hits"]) == search["count"]

    shown = _assert_success(
        live_service.invoke("ref_show", {"schema_version": 1, "uri": TASK_URI}),
        "ref_show",
    )
    assert shown["found"] is True
    assert shown["reference"] == TASK_URI

    related = _assert_success(
        live_service.invoke(
            "ref_related",
            {"schema_version": 1, "uri": TASK_URI, "depth": 1},
        ),
        "ref_related",
    )
    assert related["related"]["focal"]["descriptor"]["uri"] == TASK_URI
    assert related["related"]["nodes"]

    traced = _assert_success(
        live_service.invoke(
            "ref_trace",
            {"schema_version": 1, "uri": TASK_URI, "include_history": True},
        ),
        "ref_trace",
    )
    assert traced["trace"]["focal"]["descriptor"]["uri"] == TASK_URI
    assert traced["trace"]["claims"]

    packed = _assert_success(
        live_service.invoke(
            "context_pack",
            {
                "schema_version": 1,
                "uri": TASK_URI,
                "budget": 12000,
                "include_history": True,
            },
        ),
        "context_pack",
    )
    assert packed["pack"]["focal_uri"] == TASK_URI
    assert packed["pack"]["schema_version"] == 1

    tasks = _assert_success(
        live_service.invoke(
            "task_list",
            {"schema_version": 1, "statuses": ["waiting"]},
        ),
        "task_list",
    )
    assert tasks["count"] == 1
    assert tasks["tasks"][0]["id"] == TASK_ID

    task = _assert_success(
        live_service.invoke("task_show", {"schema_version": 1, "task": TASK_ID}),
        "task_show",
    )
    assert task["task"]["uri"] == TASK_URI

    audit = _assert_success(
        live_service.invoke("audit_summary", {"schema_version": 1}),
        "audit_summary",
    )
    assert audit["audit"]["event_count"] == 0

    inbox = _assert_success(
        live_service.invoke("inbox_list", {"schema_version": 1}),
        "inbox_list",
    )
    assert inbox == {"artifacts": [], "count": 0}


@pytest.mark.parametrize("tool_name", MUTATION_TOOL_NAMES)
@pytest.mark.parametrize("approved", [None, False])
def test_every_mutation_requires_literal_true_at_runtime(
    live_service: McpToolService,
    tool_name: str,
    approved: bool | None,
) -> None:
    response = live_service.invoke(
        tool_name,
        mutation_arguments(tool_name, approved=approved),
    )

    assert response.envelope.ok is False
    assert error_codes(response) == {"APPROVAL_REQUIRED"}
    assert response.envelope.errors[0].path == "$.approved"


@pytest.mark.parametrize(
    ("tool_name", "dependency"),
    [
        ("draft_save", "WP-420"),
    ],
)
def test_missing_wave_dependencies_return_structured_placeholders(
    live_service: McpToolService,
    tool_name: str,
    dependency: str,
) -> None:
    response = live_service.invoke(
        tool_name,
        {"schema_version": 1, "approved": True},
    )

    assert response.envelope.ok is False
    assert error_codes(response) == {"NOT-IMPLEMENTED"}
    assert response.envelope.result == {"dependency": dependency, "implemented": False}
    diagnostic = response.envelope.errors[0]
    assert diagnostic.category == "unavailable_dependency"
    assert dependency in diagnostic.message


@pytest.mark.integration
def test_transaction_tools_validate_dry_run_apply_and_rebuild_end_to_end(
    tmp_path: Path,
) -> None:
    root, transaction = initialize_mcp_transaction(tmp_path / "transaction-context")
    service = McpToolService(root)
    payload = transaction.model_dump(mode="json")

    validated = service.invoke(
        "proposal_validate",
        {"schema_version": 1, "approved": True, "proposal": payload},
    )
    assert validated.envelope.ok is True
    assert validated.envelope.result["validation"]["valid"] is True

    dry_run = service.invoke(
        "transaction_dry_run",
        {"schema_version": 1, "approved": True, "proposal": payload},
    )
    assert dry_run.envelope.ok is True
    assert dry_run.envelope.result["dry_run"]["valid"] is True
    assert dry_run.envelope.result["dry_run"]["effects"][0]["target"] == (
        "02_knowledge/PRJ-mcp-apply.md"
    )
    assert not (root / "02_knowledge" / "PRJ-mcp-apply.md").exists()

    applied = service.invoke(
        "transaction_apply",
        {"schema_version": 1, "approved": True, "proposal": payload},
    )
    assert applied.envelope.ok is True
    receipt = applied.envelope.result["receipt"]
    assert receipt["committed"] is True
    assert receipt["applied_targets"] == ["02_knowledge/PRJ-mcp-apply.md"]
    assert (root / "02_knowledge" / "PRJ-mcp-apply.md").is_file()

    rebuilt = service.invoke(
        "index_rebuild",
        {"schema_version": 1, "approved": True},
    )
    assert rebuilt.envelope.ok is True
    assert rebuilt.envelope.result["rebuild"]["counts"]["entities"] >= 1

    audit = service.invoke("audit_summary", {"schema_version": 1})
    assert audit.envelope.ok is True
    assert audit.envelope.result["audit"]["event_count"] == 1


@pytest.mark.parametrize("tool_name", ALL_TOOL_NAMES)
@pytest.mark.parametrize(
    ("hostile_value", "boundary_code"),
    [
        ("workctx://other-context/task/TASK-2026-999", "REF-CONTEXT-MISMATCH"),
        ("../other-context/private.txt", "CTX-PATH-ESCAPE"),
    ],
)
def test_every_tool_refuses_cross_context_and_path_escape_attempts(
    live_service: McpToolService,
    tool_name: str,
    hostile_value: str,
    boundary_code: str,
) -> None:
    response = live_service.invoke(tool_name, boundary_arguments(tool_name, hostile_value))

    assert response.envelope.ok is False
    if tool_name in {
        "context_info",
        "workspace_validate",
        "inbox_list",
        "audit_summary",
        "artifact_register",
        "index_rebuild",
        "draft_save",
    }:
        assert error_codes(response) == {"INVALID_INPUT"}
    else:
        assert error_codes(response) == {boundary_code}
        assert response.envelope.errors[0].category == "context_boundary"
    assert hostile_value not in response.model_dump_json()


def test_invalid_and_unknown_calls_fail_with_structured_diagnostics(
    live_service: McpToolService,
) -> None:
    invalid = live_service.invoke("context_info", {"schema_version": 2})
    unknown = live_service.invoke("not_an_adr_tool", {"schema_version": 1})

    assert error_codes(invalid) == {"INVALID_INPUT"}
    assert invalid.envelope.errors[0].path == "$.schema_version"
    assert error_codes(unknown) == {"USAGE_CONFIGURATION"}
    assert unknown.envelope.errors[0].category == "usage_configuration"


@pytest.mark.parametrize(
    "raised",
    [
        RuntimeError("Traceback (most recent call last): password=fake-password-value"),
        UserCorrectableError("api_key=ghp_abcdefghijklmnopqrstuvwxyz123456"),
    ],
)
def test_exception_boundary_never_leaks_tracebacks_or_secret_values(
    live_service: McpToolService,
    raised: Exception,
) -> None:
    secret_fragments = ("fake-password-value", "ghp_abcdefghijklmnopqrstuvwxyz123456")

    def fail(_arguments: dict[str, Any]) -> ToolResponse:
        raise raised

    live_service._handlers["context_info"] = fail
    response = live_service.invoke("context_info", {"schema_version": 1})
    serialized = response.model_dump_json()

    assert response.envelope.ok is False
    assert "traceback" not in serialized.casefold()
    assert all(secret not in serialized for secret in secret_fragments)
    assert all("\n" not in diagnostic.message for diagnostic in response.envelope.errors)


def test_success_results_are_recursively_redacted(live_service: McpToolService) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    unmarked_secret = "fictional-sensitive-value-1234567890"

    def leak(_arguments: dict[str, Any]) -> ToolResponse:
        return live_service._success(
            "context_info",
            {
                "api_key": secret,
                "api-key": unmarked_secret,
                "clientSecret": unmarked_secret,
                "service_api_key": unmarked_secret,
                "nested": {
                    "note": (f"Authorization: Bearer {secret}; service_api_key={unmarked_secret}")
                },
            },
        )

    live_service._handlers["context_info"] = leak
    response = live_service.invoke("context_info", {"schema_version": 1})
    serialized = response.model_dump_json()

    assert response.envelope.ok is True
    assert response.envelope.result["api_key"] == "[REDACTED]"
    assert response.envelope.result["api-key"] == "[REDACTED]"
    assert response.envelope.result["clientSecret"] == "[REDACTED]"
    assert response.envelope.result["service_api_key"] == "[REDACTED]"
    assert secret not in serialized
    assert unmarked_secret not in serialized
    assert "Authorization: [REDACTED]" in serialized
