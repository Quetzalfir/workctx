from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from workctx.domain.transactions import (
    AuditCreateOperation,
    AuditUpdateOperation,
    TransactionProposal,
)
from workctx.transactions import ProposalValidationError, TransactionEngine
from workctx.transactions.engine import _secret_diagnostics
from workctx.transactions.ledger import find_event_by_proposal_id
from workctx.validation import Severity, validate_workspace
from workctx.validation.secret_acknowledgments import (
    SECRET_SCAN_ACKNOWLEDGMENTS_PATH,
    load_secret_scan_acknowledgments,
)

from .support import content_hash, create_operation, initialize_transaction_context, proposal

SECRET_VALUE = "FICTIONAL_ACKNOWLEDGMENT_VALUE_123456"
TARGET = "02_knowledge/PRJ-acknowledged.md"
ACKNOWLEDGED_AT = datetime(2026, 9, 23, 18, 30, tzinfo=UTC)


def _secret_proposal() -> TransactionProposal:
    return proposal(
        "secret-acknowledgment",
        [
            create_operation(
                "PRJ-acknowledged",
                body=f"api_key = {SECRET_VALUE}\n",
            )
        ],
    )


def test_blocking_transaction_diagnostic_never_echoes_matched_value() -> None:
    diagnostics = _secret_diagnostics(_secret_proposal())
    serialized = json.dumps([diagnostic.model_dump(mode="json") for diagnostic in diagnostics])

    assert any(diagnostic.code == "TXN-POSSIBLE-SECRET" for diagnostic in diagnostics)
    assert SECRET_VALUE not in serialized


def test_acknowledgment_lifecycle_is_atomic_hash_bound_and_value_free(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = _secret_proposal()
    engine = TransactionEngine(root, clock=lambda: ACKNOWLEDGED_AT)

    with pytest.raises(ProposalValidationError) as blocked:
        engine.apply(transaction, approved=True)

    blocked_payload = blocked.value.result.model_dump_json()
    assert "TXN-POSSIBLE-SECRET" in blocked_payload
    assert SECRET_VALUE not in blocked_payload
    assert not (root / SECRET_SCAN_ACKNOWLEDGMENTS_PATH).exists()

    receipt = engine.apply(
        transaction,
        approved=True,
        acknowledge_possible_secret=(TARGET,),
    )

    acknowledgment_path = root / SECRET_SCAN_ACKNOWLEDGMENTS_PATH
    record = load_secret_scan_acknowledgments(acknowledgment_path.read_text(encoding="utf-8"))
    entry = record.by_path()[TARGET]
    target_bytes = (root / TARGET).read_bytes()
    assert entry.acknowledged_at == ACKNOWLEDGED_AT
    assert entry.content_hash == f"sha256:{hashlib.sha256(target_bytes).hexdigest()}"
    assert TARGET in receipt.applied_targets
    assert SECRET_SCAN_ACKNOWLEDGMENTS_PATH in receipt.applied_targets

    report = validate_workspace(root)
    secret_issues = [issue for issue in report.issues if issue.code == "CTX-POSSIBLE-SECRET"]
    assert report.ok is True
    assert secret_issues
    assert {issue.severity for issue in secret_issues} == {Severity.ADVISORY}
    assert all("acknowledgment matches" in issue.message for issue in secret_issues)

    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.acknowledged_paths == [TARGET]
    assert {
        operation.target
        for operation in event.operations
        if isinstance(operation, (AuditCreateOperation, AuditUpdateOperation))
    } >= {
        TARGET,
        SECRET_SCAN_ACKNOWLEDGMENTS_PATH,
    }
    assert SECRET_VALUE.encode() not in acknowledgment_path.read_bytes()
    ledger = root / "99_meta" / "audit" / "ledger.jsonl"
    assert SECRET_VALUE.encode() not in ledger.read_bytes()

    (root / TARGET).write_bytes(target_bytes + b"\nOrdinary edit.\n")
    stale = validate_workspace(root)
    stale_issues = [issue for issue in stale.issues if issue.code == "CTX-POSSIBLE-SECRET"]
    assert stale.ok is False
    assert {issue.severity for issue in stale_issues} == {Severity.ERROR}
    assert all("acknowledgment is missing or stale" in issue.message for issue in stale_issues)
    assert SECRET_VALUE not in json.dumps(
        [
            {
                "message": issue.message,
                "path": issue.path,
                "repair_action": issue.repair_action,
            }
            for issue in (*report.issues, *stale.issues)
        ]
    )


def test_acknowledgment_rejects_clean_unwritten_and_unapproved_paths(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    clean = proposal("clean-acknowledgment", [create_operation("PRJ-clean-ack")])
    clean_preview = TransactionEngine(root).dry_run(
        clean,
        acknowledge_possible_secret=("02_knowledge/PRJ-clean-ack.md",),
    )
    assert clean_preview.valid is False
    assert any(
        diagnostic.code == "CTX-SECRET-ACKNOWLEDGMENT-INVALID"
        for diagnostic in clean_preview.diagnostics
    )

    secret = _secret_proposal()
    unwritten_preview = TransactionEngine(root).dry_run(
        secret,
        acknowledge_possible_secret=("02_knowledge/PRJ-not-written.md",),
    )
    assert unwritten_preview.valid is False
    assert any(
        diagnostic.code == "CTX-SECRET-ACKNOWLEDGMENT-INVALID"
        for diagnostic in unwritten_preview.diagnostics
    )

    with pytest.raises(ProposalValidationError) as unapproved:
        TransactionEngine(root).apply(
            secret,
            approved=False,
            acknowledge_possible_secret=(TARGET,),
        )
    assert any(
        diagnostic.code == "TXN-APPROVAL-REQUIRED"
        for diagnostic in unapproved.value.result.diagnostics
    )
    assert not (root / TARGET).exists()
    assert not (root / SECRET_SCAN_ACKNOWLEDGMENTS_PATH).exists()


def test_acknowledgment_file_cannot_be_written_directly_by_a_proposal(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    source = root / "99_meta" / "candidate-acknowledgments.yaml"
    source.write_text(
        "schema_version: 1\n"
        "acknowledgments:\n"
        "- path: 02_knowledge/PRJ-fictional.md\n"
        f"  content_hash: sha256:{'a' * 64}\n"
        "  acknowledged_at: '2026-09-23T18:00:00Z'\n",
        encoding="utf-8",
    )
    transaction = proposal(
        "direct-acknowledgment-file",
        [
            {
                "op": "move",
                "source": "99_meta/candidate-acknowledgments.yaml",
                "destination": SECRET_SCAN_ACKNOWLEDGMENTS_PATH,
                "expected_hash": content_hash(source.read_bytes()),
            }
        ],
    )

    preview = TransactionEngine(root).dry_run(transaction)

    assert preview.valid is False
    assert any(
        diagnostic.code == "CTX-SECRET-ACKNOWLEDGMENT-INVALID" for diagnostic in preview.diagnostics
    )
    assert not (root / SECRET_SCAN_ACKNOWLEDGMENTS_PATH).exists()
