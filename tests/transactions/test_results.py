from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from workctx.errors import ConflictError, UserCorrectableError, WorkctxError
from workctx.transactions.errors import (
    DuplicateProposalError,
    LedgerIntegrityError,
    PostconditionRollbackError,
    ProposalValidationError,
    RecoveryPendingError,
    StaleRevisionError,
    TransactionConflictError,
    TransactionError,
)
from workctx.transactions.models import (
    ApplyResult,
    DiagnosticSeverity,
    DryRunResult,
    OperationEffect,
    ProjectionState,
    ProjectionStatus,
    ProposalValidationResult,
    RecoveryResult,
    RecoveryStrategy,
    TransactionDiagnostic,
)

PROPOSAL_ID = "TXP-20260801T120000Z-fictional-update"
CONTEXT_ID = "fictional-context"
ZERO_HASH = "0" * 64
EVENT_HASH = "a" * 64
ZERO_CONTENT_HASH = f"sha256:{ZERO_HASH}"
CONTENT_HASH = f"sha256:{EVENT_HASH}"


def _diagnostic() -> TransactionDiagnostic:
    return TransactionDiagnostic(
        code="TXN-REF-UNRESOLVED",
        severity=DiagnosticSeverity.ERROR,
        message="A transaction reference does not resolve.",
        path="operations/0/payload/references/0/target",
        repair_action="Reference an entity that exists in this context.",
    )


def _projection(state: ProjectionState = ProjectionState.FRESH) -> ProjectionStatus:
    if state is ProjectionState.FRESH:
        return ProjectionStatus(state=state)
    return ProjectionStatus(
        state=state,
        diagnostic_code="PROJECTION-STALE",
        repair_action="Rebuild the SQLite projection from canonical files.",
        invalidation_confirmed=True,
    )


def test_validation_and_dry_run_records_are_json_serializable() -> None:
    diagnostic = _diagnostic()
    validation = ProposalValidationResult(
        proposal_id=PROPOSAL_ID,
        context_id=CONTEXT_ID,
        base_revision=ZERO_HASH,
        valid=False,
        diagnostics=(diagnostic,),
    )
    effect = OperationEffect(
        order=0,
        op="update",
        target="03_work/tasks/TASK-2026-001.md",
        destination=None,
        preimage_hash=ZERO_CONTENT_HASH,
        postimage_hash=CONTENT_HASH,
        hand_edits=False,
    )
    dry_run = DryRunResult(
        proposal_id=PROPOSAL_ID,
        context_id=CONTEXT_ID,
        base_revision=ZERO_HASH,
        valid=True,
        effects=(effect,),
    )

    validation_payload = validation.model_dump(mode="json")
    dry_run_payload = dry_run.model_dump(mode="json")
    assert validation_payload["diagnostics"][0]["severity"] == "error"
    assert dry_run_payload["effects"][0]["postimage_hash"] == CONTENT_HASH
    json.dumps(validation_payload)
    json.dumps(dry_run_payload)


def test_apply_result_is_a_complete_committed_receipt() -> None:
    result = ApplyResult(
        proposal_id=PROPOSAL_ID,
        context_id=CONTEXT_ID,
        base_revision=ZERO_HASH,
        committed_revision=EVENT_HASH,
        applied_targets=("03_work/tasks/TASK-2026-001.md",),
        ledger_event_id="AUD-20260801T120000Z-fictional-update",
        ledger_event_hash=EVENT_HASH,
        ledger_source_refs=("workctx://fictional-context/task/TASK-2026-001",),
        projection=_projection(),
    )

    payload = result.model_dump(mode="json")
    assert payload["schema_version"] == 1
    assert payload["committed"] is True
    assert payload["projection"] == {
        "state": "fresh",
        "diagnostic_code": None,
        "repair_action": None,
        "invalidation_confirmed": None,
        "skipped_documents": 0,
    }
    json.dumps(payload)

    with pytest.raises(ValidationError):
        ApplyResult.model_validate({**payload, "committed": False})
    with pytest.raises(ValidationError):
        ApplyResult.model_validate({**payload, "schema_version": 2})


def test_projection_and_recovery_statuses_preserve_stale_receipts() -> None:
    projection = _projection(ProjectionState.STALE)
    recovery = RecoveryResult(
        strategy=RecoveryStrategy.COMPLETE,
        outcome="committed",
        transaction_id=PROPOSAL_ID,
        committed_revision=EVENT_HASH,
        applied_targets=("03_work/tasks/TASK-2026-001.md",),
        ledger_event_id="AUD-20260801T120100Z-recovery",
        ledger_event_hash=EVENT_HASH,
        projection=projection,
        diagnostics=(),
    )

    payload = recovery.model_dump(mode="json")
    assert payload["strategy"] == "complete"
    assert payload["projection"]["state"] == "stale"
    assert payload["projection"]["invalidation_confirmed"] is True
    json.dumps(payload)


def test_diagnostics_reject_multiline_or_extra_content() -> None:
    with pytest.raises(ValidationError, match="single-line"):
        TransactionDiagnostic(
            code="TXN-INVALID",
            severity="error",
            message="unsafe\nsecond line",
        )

    with pytest.raises(ValidationError, match="Extra inputs"):
        TransactionDiagnostic.model_validate(
            {
                "code": "TXN-INVALID",
                "severity": "error",
                "message": "Safe message.",
                "raw_value": "must not be retained",
            }
        )


def test_transaction_errors_have_stable_types_codes_and_sanitized_messages() -> None:
    validation = ProposalValidationResult(
        proposal_id=PROPOSAL_ID,
        context_id=CONTEXT_ID,
        base_revision=ZERO_HASH,
        valid=False,
        diagnostics=(_diagnostic(),),
    )
    validation_error = ProposalValidationError(validation)
    inspection = object()
    recovery_error = RecoveryPendingError(inspection)
    rollback_result = RecoveryResult(
        strategy=RecoveryStrategy.ROLLBACK,
        outcome="rolled_back",
        transaction_id=PROPOSAL_ID,
        committed_revision=EVENT_HASH,
        ledger_event_id="AUD-20260801T120100Z-rollback",
        ledger_event_hash=EVENT_HASH,
        projection=_projection(),
    )
    rollback_error = PostconditionRollbackError(rollback_result)

    assert isinstance(validation_error, (TransactionError, UserCorrectableError, WorkctxError))
    assert validation_error.result is validation
    assert str(validation_error) == "Transaction proposal validation failed."
    assert isinstance(recovery_error, (TransactionConflictError, ConflictError))
    assert recovery_error.code == "TXN-RECOVERY-PENDING"
    assert recovery_error.inspection is inspection
    assert StaleRevisionError().code == "TXN-STALE-REVISION"
    assert DuplicateProposalError().code == "TXN-DUPLICATE-PROPOSAL"
    assert isinstance(LedgerIntegrityError(), UserCorrectableError)
    assert isinstance(rollback_error, UserCorrectableError)
    assert rollback_error.result is rollback_result

    untrusted = TransactionConflictError("secret=value\n")
    assert untrusted.code == "TXN-CONFLICT"
    assert "secret" not in str(untrusted)
