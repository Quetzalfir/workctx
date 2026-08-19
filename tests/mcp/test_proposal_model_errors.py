from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from transactions.support import create_operation, initialize_transaction_context, proposal

from workctx.mcp.application import McpToolService


@pytest.mark.parametrize(
    "tool_name",
    ("proposal_validate", "transaction_dry_run", "transaction_apply"),
)
def test_mcp_proposal_model_errors_pin_field_diagnostic_shape(
    tmp_path: Path,
    tool_name: str,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    payload: dict[str, Any] = proposal(
        "invalid-model",
        [create_operation("PRJ-invalid-model")],
    ).model_dump(mode="json")
    payload["approval"] = "unsupported"

    response = McpToolService(root).invoke(
        tool_name,
        {
            "schema_version": 1,
            "approved": True,
            "proposal": payload,
        },
    )

    assert response.envelope.ok is False
    assert response.envelope.result == {}
    assert [diagnostic.model_dump(mode="json") for diagnostic in response.envelope.errors] == [
        {
            "code": "PROPOSAL_MODEL_INVALID",
            "category": "usage_configuration",
            "severity": "error",
            "message": "Input should be 'required' or 'not_required'",
            "path": "$.proposal.approval",
            "repair_action": "Correct this proposal field and retry.",
        }
    ]
