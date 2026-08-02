from __future__ import annotations

from pathlib import Path
from typing import Any

from retrieval.support import create_context_pack_projection
from transactions.support import create_operation, initialize_transaction_context, proposal

from workctx.domain.transactions import TransactionProposal
from workctx.mcp.models import ToolResponse
from workctx.services.contexts import initialize_context

READ_TOOL_NAMES = (
    "context_info",
    "workspace_validate",
    "search",
    "ref_show",
    "ref_related",
    "ref_trace",
    "context_pack",
    "task_list",
    "task_show",
    "inbox_list",
    "audit_summary",
)

MUTATION_TOOL_NAMES = (
    "artifact_register",
    "proposal_validate",
    "transaction_dry_run",
    "transaction_apply",
    "index_rebuild",
    "draft_save",
)

ALL_TOOL_NAMES = READ_TOOL_NAMES + MUTATION_TOOL_NAMES

CONTEXT_ID = "fictional-context"
TASK_ID = "TASK-2026-001"
TASK_URI = f"workctx://{CONTEXT_ID}/task/{TASK_ID}"


def initialize_mcp_context(root: Path) -> Path:
    """Create a complete context plus the shared retrieval/projection fixture."""

    initialize_context(root, name="Fictional MCP Context", context_id=CONTEXT_ID)
    create_context_pack_projection(root)
    return root


def initialize_mcp_transaction(root: Path) -> tuple[Path, TransactionProposal]:
    """Create a transaction context and one valid create proposal."""

    initialized = initialize_transaction_context(root)
    transaction = proposal("mcp-apply", [create_operation("PRJ-mcp-apply")])
    return initialized, transaction


def error_codes(response: ToolResponse) -> set[str]:
    return {diagnostic.code for diagnostic in response.envelope.errors}


def mutation_arguments(name: str, *, approved: bool | None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"schema_version": 1}
    if approved is not None:
        arguments["approved"] = approved
    if name in {"proposal_validate", "transaction_dry_run", "transaction_apply"}:
        arguments["proposal"] = {}
    return arguments


def boundary_arguments(name: str, value: str) -> dict[str, Any]:
    """Put a hostile selector in a schema-valid location where one exists."""

    arguments: dict[str, Any] = {"schema_version": 1}
    if name in MUTATION_TOOL_NAMES:
        arguments["approved"] = True

    if name == "search":
        arguments["query"] = value
    elif name in {"ref_show", "ref_related", "ref_trace", "context_pack"}:
        arguments["uri"] = value
    elif name == "task_list":
        arguments["owner"] = value
    elif name == "task_show":
        arguments["task"] = value
    elif name in {"proposal_validate", "transaction_dry_run", "transaction_apply"}:
        arguments["proposal"] = {"boundary_probe": value}
    else:
        # Zero-argument and boolean-only tools must reject the unexpected selector
        # structurally before a backing engine can run.
        arguments["boundary_probe"] = value
    return arguments
