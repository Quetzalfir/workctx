from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from workctx.drafting import DraftService
from workctx.tasks import TaskService
from workctx.transactions import ProposalValidationError, apply, verify_ledger

from .support import (
    DRAFT_TIME,
    TASK_TIME,
    acceptance_draft_payload,
    acceptance_task,
    build_entity_create_proposal,
    create_operational_context,
    human_actor,
    initialize_acceptance_context,
    invoke_envelope,
    invoke_mcp,
    write_raw_note,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.integration]

MUTATION_TOOLS = (
    "artifact_register",
    "proposal_validate",
    "transaction_dry_run",
    "transaction_apply",
    "index_rebuild",
    "draft_save",
)


def test_public_mutation_apis_and_cli_preview_require_explicit_approval(tmp_path: Path) -> None:
    operational = create_operational_context(tmp_path / "approval-services")
    root = operational.root
    proposal = build_entity_create_proposal(root)
    target = root / "02_knowledge" / "systems" / "SYS-approval-gate.md"
    ledger_before = verify_ledger(root)

    proposal_file = tmp_path / "approval-proposal.json"
    proposal_file.write_text(
        json.dumps(proposal.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    preview = invoke_envelope(
        [
            "transaction",
            "apply",
            str(proposal_file),
            "--context",
            str(root),
            "--json",
        ],
        command="transaction.apply",
    )
    assert preview["result"]["dry_run"] is True
    assert preview["result"]["confirmation_required"] is True
    assert not target.exists()

    with pytest.raises(ProposalValidationError) as transaction_denial:
        apply(root, proposal)
    assert {item.code for item in transaction_denial.value.result.diagnostics} >= {
        "TXN-APPROVAL-REQUIRED"
    }
    assert not target.exists()

    unapproved_task = acceptance_task(task_id="TASK-2026-002")
    with pytest.raises(ProposalValidationError) as task_denial:
        TaskService(root, actor=human_actor(), clock=lambda: TASK_TIME).create_task(unapproved_task)
    assert {item.code for item in task_denial.value.result.diagnostics} >= {"TXN-APPROVAL-REQUIRED"}
    assert not (root / "03_work" / "tasks" / "TASK-2026-002.md").exists()

    with pytest.raises(ProposalValidationError) as draft_denial:
        DraftService(root, clock=lambda: DRAFT_TIME).save_draft(
            acceptance_draft_payload(),
            approved=False,
        )
    assert {item.code for item in draft_denial.value.result.diagnostics} >= {
        "TXN-APPROVAL-REQUIRED"
    }
    assert not (root / "05_outbox" / "DRAFT-20260802-launch-readiness-01.md").exists()

    ledger_after = verify_ledger(root)
    assert ledger_after == ledger_before


def test_in_process_mcp_server_requires_literal_true_for_every_mutation_tool(
    tmp_path: Path,
) -> None:
    root = initialize_acceptance_context(tmp_path / "approval-mcp")
    database = root / "98_state" / "index.sqlite3"
    if database.exists():
        database.unlink()
    raw = write_raw_note(root, name="mcp-approval-note.txt")
    calls: list[tuple[str, dict[str, Any]]] = []
    for tool_name in MUTATION_TOOLS:
        arguments: dict[str, Any] = {"schema_version": 1}
        if tool_name == "artifact_register":
            arguments.update(
                {
                    "path": "00_inbox/raw/mcp-approval-note.txt",
                    "source_type": "note",
                }
            )
        elif tool_name in {
            "proposal_validate",
            "transaction_dry_run",
            "transaction_apply",
        }:
            arguments["proposal"] = {}
        calls.append((tool_name, arguments))

    discovered, missing_approval = invoke_mcp(root, calls)
    _, false_approval = invoke_mcp(
        root,
        [(tool_name, {**arguments, "approved": False}) for tool_name, arguments in calls],
    )

    for tool_name in MUTATION_TOOLS:
        contract = discovered[tool_name]
        assert contract["read_only_hint"] is False
        assert "approved" in contract["input_schema"]["required"]
        assert contract["input_schema"]["properties"]["approved"]["const"] is True
        for responses in (missing_approval, false_approval):
            response = responses[tool_name]
            assert response["is_error"] is True
            envelope = response["structured_content"]
            assert envelope["ok"] is False
            assert {item["code"] for item in envelope["errors"]} == {"APPROVAL_REQUIRED"}
            assert envelope["errors"][0]["path"] == "$.approved"

    assert raw.is_file()
    assert not tuple((root / "00_inbox" / "manifests").glob("ART-*.json"))
    assert not database.exists()
    assert not tuple((root / "05_outbox").glob("DRAFT-*.md"))
    assert verify_ledger(root).event_count == 0
