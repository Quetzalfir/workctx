from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from transactions.support import create_operation, initialize_transaction_context, proposal
from typer.testing import CliRunner

from workctx.cli import app

runner = CliRunner()


@pytest.mark.parametrize(
    ("arguments", "command"),
    (
        (("proposal", "validate"), "proposal.validate"),
        (("transaction", "apply", "--yes"), "transaction.apply"),
    ),
)
def test_cli_proposal_model_errors_pin_field_diagnostic_shape(
    tmp_path: Path,
    arguments: tuple[str, ...],
    command: str,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    payload: dict[str, Any] = proposal(
        "invalid-model",
        [create_operation("PRJ-invalid-model")],
    ).model_dump(mode="json")
    payload["approval"] = "unsupported"
    proposal_path = tmp_path / "invalid-proposal.json"
    proposal_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        [*arguments, str(proposal_path), "--context", str(root), "--json"],
    )

    assert result.exit_code == 1, result.output
    envelope = json.loads(result.stdout)
    assert envelope["command"] == command
    assert envelope["context_id"] == "transaction-lab"
    assert envelope["result"] == {}
    assert envelope["errors"] == [
        {
            "code": "PROPOSAL_MODEL_INVALID",
            "message": "Input should be 'required' or 'not_required'",
            "path": "$.approval",
            "repair_action": "Correct this proposal field and retry.",
        }
    ]


def test_cli_human_proposal_error_lists_field_path_and_message(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    payload: dict[str, Any] = proposal(
        "invalid-human",
        [create_operation("PRJ-invalid-human")],
    ).model_dump(mode="json")
    payload["approval"] = "unsupported"
    proposal_path = tmp_path / "invalid-human.json"
    proposal_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        ["proposal", "validate", str(proposal_path), "--context", str(root)],
    )

    assert result.exit_code == 1
    assert "$.approval" in result.stderr
    assert "Input should be 'required' or 'not_required'" in result.stderr
