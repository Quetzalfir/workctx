from __future__ import annotations

from pathlib import Path

import pytest

from workctx.adapters.filesystem import render_markdown_bytes
from workctx.domain.transactions import ZERO_REVISION
from workctx.transactions import (
    DuplicateProposalError,
    ProposalValidationError,
    StaleRevisionError,
    TransactionEngine,
)
from workctx.transactions.ledger import find_event_by_proposal_id

from .support import (
    content_hash,
    create_operation,
    entity_document,
    initialize_transaction_context,
    proposal,
    workspace_snapshot,
)


def test_dry_run_reports_exact_effect_without_any_write(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal(
        "dry-run",
        [create_operation("PRJ-dry-run")],
        preconditions=[{"kind": "path_absent", "path": "02_knowledge/PRJ-dry-run.md"}],
        postconditions=[
            {
                "kind": "reference_exists",
                "reference": "workctx://transaction-lab/project/PRJ-dry-run",
            }
        ],
    )
    before = workspace_snapshot(root, include_state=True)

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is True
    assert result.diagnostics == ()
    assert len(result.effects) == 1
    effect = result.effects[0]
    operation = transaction.operations[0]
    expected = render_markdown_bytes(operation.payload.document, operation.payload.body)  # type: ignore[union-attr]
    assert effect.target == "02_knowledge/PRJ-dry-run.md"
    assert effect.preimage_hash is None
    assert effect.postimage_hash == content_hash(expected)
    assert workspace_snapshot(root, include_state=True) == before
    assert not (root / "98_state" / "lock.json").exists()
    assert not (root / "99_meta" / "audit" / "ledger.jsonl").exists()


def test_apply_commits_cross_referenced_documents_and_receipt(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    project_uri = "workctx://transaction-lab/project/PRJ-core"
    transaction = proposal(
        "multi-entity",
        [
            create_operation("PRJ-core"),
            create_operation(
                "SYS-service",
                "system",
                references=[
                    {
                        "relation": "depends_on",
                        "target": project_uri,
                        "confidence": "high",
                    }
                ],
            ),
        ],
        postconditions=[{"kind": "reference_exists", "reference": project_uri}],
    )

    receipt = TransactionEngine(root).apply(transaction, approved=True)

    assert receipt.committed is True
    assert receipt.base_revision == ZERO_REVISION
    assert receipt.committed_revision == receipt.ledger_event_hash
    assert receipt.applied_targets == (
        "02_knowledge/PRJ-core.md",
        "02_knowledge/SYS-service.md",
    )
    assert receipt.projection.state == "fresh"
    assert (root / "02_knowledge" / "PRJ-core.md").is_file()
    assert (root / "02_knowledge" / "SYS-service.md").is_file()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.result == "committed"
    assert event.action == "apply"
    assert event.prev_hash == ZERO_REVISION
    assert event.event_hash == receipt.committed_revision
    assert not (root / "98_state" / "staging" / "intent.json").exists()


def test_secret_proposal_is_location_only_and_canonical_tree_is_unchanged(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    secret_value = "fictional-password-value"
    transaction = proposal(
        "secret-rejected",
        [
            create_operation(
                "PRJ-secret",
                body=f"password={secret_value}\n",
            )
        ],
    )
    before = workspace_snapshot(root, include_state=False)

    with pytest.raises(ProposalValidationError) as captured:
        TransactionEngine(root).apply(transaction, approved=True)

    result = captured.value.result
    secret_diagnostics = [
        diagnostic for diagnostic in result.diagnostics if diagnostic.code == "TXN-POSSIBLE-SECRET"
    ]
    assert secret_diagnostics
    serialized = result.model_dump_json()
    assert secret_value not in serialized
    assert secret_diagnostics[0].path == "$.operations[0].payload.body"
    assert workspace_snapshot(root, include_state=False) == before
    assert not (root / "99_meta" / "audit" / "ledger.jsonl").exists()


def test_duplicate_and_stale_revision_are_distinct_conflicts(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    first = proposal("first", [create_operation("PRJ-first")])
    TransactionEngine(root).apply(first, approved=True)

    with pytest.raises(DuplicateProposalError) as duplicate:
        TransactionEngine(root).apply(first, approved=True)
    assert duplicate.value.code == "TXN-DUPLICATE-PROPOSAL"

    stale = proposal("stale", [create_operation("PRJ-stale")])
    with pytest.raises(StaleRevisionError) as conflict:
        TransactionEngine(root).apply(stale, approved=True)
    assert conflict.value.code == "TXN-STALE-REVISION"
    assert not (root / "02_knowledge" / "PRJ-stale.md").exists()


def test_required_approval_is_runtime_authoritative(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal("approval", [create_operation("PRJ-approval")])

    with pytest.raises(ProposalValidationError) as captured:
        TransactionEngine(root).apply(transaction)

    assert any(
        diagnostic.code == "TXN-APPROVAL-REQUIRED"
        for diagnostic in captured.value.result.diagnostics
    )
    assert not (root / "02_knowledge" / "PRJ-approval.md").exists()


def test_move_and_generated_delete_use_typed_effects(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    inbox = root / "00_inbox" / "fictional.txt"
    inbox.write_bytes(b"Fictional raw evidence.\n")
    generated = root / "04_views" / "generated.md"
    generated.write_bytes(b"Generated view.\n")
    transaction = proposal(
        "move-delete",
        [
            {
                "op": "move",
                "source": "00_inbox/fictional.txt",
                "destination": "01_processed/fictional.txt",
                "expected_hash": content_hash(inbox.read_bytes()),
            },
            {
                "op": "delete_generated",
                "target": "04_views/generated.md",
                "expected_hash": content_hash(generated.read_bytes()),
            },
        ],
    )

    receipt = TransactionEngine(root).apply(transaction, approved=True)

    assert receipt.applied_targets == (
        "00_inbox/fictional.txt",
        "01_processed/fictional.txt",
        "04_views/generated.md",
    )
    assert not inbox.exists()
    assert (root / "01_processed" / "fictional.txt").read_bytes() == (b"Fictional raw evidence.\n")
    assert not generated.exists()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert [operation.op for operation in event.operations] == ["move", "delete_generated"]


def test_update_warns_before_canonicalizing_hand_edits(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    first = proposal("create-update-base", [create_operation("PRJ-edit")])
    first_receipt = TransactionEngine(root).apply(first, approved=True)
    target = root / "02_knowledge" / "PRJ-edit.md"
    target.write_bytes(target.read_bytes().replace(b"---\n\n", b"---\n\n\n", 1))
    current_hash = content_hash(target.read_bytes())
    update = proposal(
        "update-hand-edit",
        [
            {
                "op": "update",
                "target": "02_knowledge/PRJ-edit.md",
                "payload": entity_document("PRJ-edit", body="Updated fictional body.\n"),
                "expected_hash": current_hash,
            }
        ],
        base_revision=first_receipt.committed_revision,
    )

    result = TransactionEngine(root).dry_run(update)

    assert result.valid is True
    assert result.effects[0].hand_edits is True
    assert any(diagnostic.code == "TXN-HAND-EDITS" for diagnostic in result.diagnostics)


def test_reference_precondition_uses_pre_state_not_staged_overlay(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    reference = "workctx://transaction-lab/project/PRJ-precondition-new"
    transaction = proposal(
        "reference-pre-state",
        [create_operation("PRJ-precondition-new")],
        preconditions=[{"kind": "reference_exists", "reference": reference}],
        postconditions=[{"kind": "reference_exists", "reference": reference}],
    )

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is False
    assert [diagnostic.code for diagnostic in result.diagnostics].count("TXN-CONDITION-FAILED") == 1


def test_directory_path_condition_fails_closed_without_raw_os_error(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal(
        "directory-condition",
        [create_operation("PRJ-directory-condition")],
        preconditions=[{"kind": "path_exists", "path": "02_knowledge"}],
    )

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is False
    assert any(diagnostic.code == "TXN-CONDITION-INVALID" for diagnostic in result.diagnostics)


def test_external_payload_reference_is_syntax_checked_not_local_lookup(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal(
        "external-reference",
        [
            create_operation(
                "PRJ-external-reference",
                references=[
                    {
                        "relation": "related_to",
                        "target": "https://example.invalid/fictional-evidence",
                        "confidence": "medium",
                    }
                ],
            )
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is True
    assert not any(diagnostic.code == "TXN-REFERENCE-MISSING" for diagnostic in result.diagnostics)


def test_stale_projection_record_cannot_satisfy_local_reference(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    seed = proposal("stale-projection-seed", [create_operation("PRJ-seed")])
    seed_receipt = TransactionEngine(root).apply(seed, approved=True)
    (root / "02_knowledge" / "PRJ-seed.md").unlink()
    referencing = proposal(
        "stale-projection-reference",
        [
            create_operation(
                "PRJ-referencing",
                references=[
                    {
                        "relation": "depends_on",
                        "target": "workctx://transaction-lab/project/PRJ-seed",
                    }
                ],
            )
        ],
        base_revision=seed_receipt.committed_revision,
    )

    result = TransactionEngine(root).dry_run(referencing)

    assert result.valid is False
    assert any(diagnostic.code == "TXN-REFERENCE-MISSING" for diagnostic in result.diagnostics)


def test_transaction_can_repair_existing_unresolved_relation(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    target_uri = "workctx://transaction-lab/project/PRJ-repair-target"
    initial = proposal(
        "repair-initial",
        [
            create_operation(
                "PRJ-repair-source",
                references=[{"relation": "depends_on", "target": target_uri}],
            ),
            create_operation("PRJ-repair-target"),
        ],
    )
    initial_receipt = TransactionEngine(root).apply(initial, approved=True)
    target = root / "02_knowledge" / "PRJ-repair-target.md"
    target.unlink()
    repair = proposal(
        "repair-missing-target",
        [create_operation("PRJ-repair-target")],
        base_revision=initial_receipt.committed_revision,
        postconditions=[{"kind": "reference_exists", "reference": target_uri}],
    )

    receipt = TransactionEngine(root).apply(repair, approved=True)

    assert receipt.committed is True
    assert target.is_file()
    assert receipt.projection.state == "fresh"


def test_duplicate_artifact_manifest_id_is_rejected_before_intent(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    preserved = root / "00_inbox" / "fictional-artifact.txt"
    preserved.write_bytes(b"Fictional artifact.\n")
    artifact_id = "ART-20260801-fictional-artifact-01"
    manifest = {
        "kind": "artifact_manifest",
        "document": {
            "schema_version": 1,
            "id": artifact_id,
            "content_hash": content_hash(preserved.read_bytes()),
            "original_name": "fictional-artifact.txt",
            "media_type": "text/plain",
            "source_type": "note",
            "source_origin": None,
            "event_at": None,
            "event_at_inferred": False,
            "ingested_at": "2026-08-01T12:00:00Z",
            "language": "en",
            "participants": [],
            "classification": "internal",
            "status": "pending",
            "preserved_path": "00_inbox/fictional-artifact.txt",
            "sidecars": [],
            "duplicate_of": None,
            "notes": None,
        },
    }
    first = proposal(
        "artifact-first",
        [
            {
                "op": "create",
                "target": f"00_inbox/manifests/{artifact_id}.json",
                "payload": manifest,
            }
        ],
    )
    first_receipt = TransactionEngine(root).apply(first, approved=True)
    duplicate = proposal(
        "artifact-duplicate",
        [
            {
                "op": "create",
                "target": f"00_inbox/manifests/{artifact_id}.yaml",
                "payload": manifest,
            }
        ],
        base_revision=first_receipt.committed_revision,
    )
    before = workspace_snapshot(root, include_state=False)

    with pytest.raises(ProposalValidationError):
        TransactionEngine(root).apply(duplicate, approved=True)

    assert workspace_snapshot(root, include_state=False) == before
    assert not (root / "98_state" / "staging" / "intent.json").exists()
    assert find_event_by_proposal_id(root, duplicate.id) is None
