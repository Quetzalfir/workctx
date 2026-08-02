from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from workctx.adapters.filesystem import ContextLock, RecoveryState, StagedReplacement
from workctx.adapters.sqlite import ProjectionBuildError, RebuildReport, SQLiteProjection
from workctx.transactions import (
    PostconditionRollbackError,
    ProjectionState,
    RecoveryPendingError,
    RecoveryStrategy,
    TransactionEngine,
)
from workctx.transactions.ledger import find_event_by_proposal_id, verify_ledger
from workctx.validation import Severity, ValidationIssue, ValidationReport

from .support import create_operation, initialize_transaction_context, proposal

ReplaceFunction = Callable[[Path, Path], None]


@pytest.mark.parametrize(
    ("strategy", "expected_result", "files_exist"),
    [
        (RecoveryStrategy.COMPLETE, "rolled_back", False),
        (RecoveryStrategy.ROLLBACK, "rolled_back", False),
    ],
)
def test_mid_sequence_failure_is_detected_and_recoverable(
    tmp_path: Path,
    strategy: RecoveryStrategy,
    expected_result: str,
    files_exist: bool,
) -> None:
    root = initialize_transaction_context(tmp_path / strategy.value)
    transaction = proposal(
        f"mid-sequence-{strategy.value}",
        [create_operation("PRJ-one"), create_operation("PRJ-two")],
    )
    replace = _fail_on_second_replace()
    engine = TransactionEngine(
        root,
        stager_factory=lambda path: StagedReplacement(path, replace_function=replace),
    )

    with pytest.raises(RecoveryPendingError) as interrupted:
        engine.apply(transaction, approved=True)

    assert interrupted.value.inspection.state is RecoveryState.PARTIALLY_APPLIED
    assert (root / "98_state" / "staging" / "intent.json").is_file()

    result = TransactionEngine(root).recover(
        strategy,
        proposal=transaction if strategy is RecoveryStrategy.COMPLETE else None,
    )

    assert result.outcome == expected_result
    assert result.ledger_event_hash == result.committed_revision
    assert (root / "02_knowledge" / "PRJ-one.md").exists() is files_exist
    assert (root / "02_knowledge" / "PRJ-two.md").exists() is files_exist
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.action == "recovery"
    assert event.result == expected_result
    assert verify_ledger(root).head_hash == event.event_hash
    assert not (root / "98_state" / "staging" / "intent.json").exists()

    replay = TransactionEngine(root).recover(strategy, transaction_id=transaction.id)
    assert replay.outcome == "already_finalized"
    assert replay.ledger_event_hash == event.event_hash


def test_lock_takeover_aborts_old_holder_and_successor_recovers(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal(
        "takeover",
        [create_operation("PRJ-takeover-one"), create_operation("PRJ-takeover-two")],
    )
    old_lock = ContextLock.acquire(root, session_id="old-holder")
    successor: list[ContextLock] = []
    replacement_count = 0

    def replace_with_takeover(source: Path, target: Path) -> None:
        nonlocal replacement_count
        replacement_count += 1
        if replacement_count == 1:
            old_lock.release()
            successor.append(ContextLock.acquire(root, session_id="successor-holder"))
        os.replace(source, target)

    engine = TransactionEngine(
        root,
        stager_factory=lambda path: StagedReplacement(
            path,
            replace_function=replace_with_takeover,
        ),
        lock_factory=lambda _path, _session: old_lock,
    )

    with pytest.raises(RecoveryPendingError) as interrupted:
        engine.apply(transaction, approved=True)

    assert interrupted.value.inspection.state is RecoveryState.PARTIALLY_APPLIED
    assert successor
    successor[0].release()

    recovered = TransactionEngine(root).recover(
        RecoveryStrategy.COMPLETE,
        proposal=transaction,
    )

    assert recovered.outcome == "rolled_back"
    assert not (root / "02_knowledge" / "PRJ-takeover-one.md").exists()
    assert not (root / "02_knowledge" / "PRJ-takeover-two.md").exists()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.action == "recovery"
    assert event.result == "rolled_back"


def test_postcondition_failure_rolls_back_and_audits_reality(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal("postcondition-rollback", [create_operation("PRJ-rollback")])

    def validator(
        context_root: Path,
        *,
        strict: bool,
        freshness_probe: object,
    ) -> ValidationReport:
        return ValidationReport(
            context_root=context_root,
            context_id="transaction-lab",
            issues=[
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="CTX-POSTCONDITION-FAILURE",
                    message="Injected postcondition failure.",
                    path="02_knowledge/PRJ-rollback.md",
                )
            ],
        )

    with pytest.raises(PostconditionRollbackError) as rolled_back:
        TransactionEngine(root, workspace_validator=validator).apply(
            transaction,
            approved=True,
        )

    receipt = rolled_back.value.result
    assert receipt.outcome == "rolled_back"
    assert receipt.applied_targets == ()
    assert receipt.ledger_event_hash == receipt.committed_revision
    assert not (root / "02_knowledge" / "PRJ-rollback.md").exists()
    assert not (root / "98_state" / "staging" / "intent.json").exists()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.result == "rolled_back"
    assert verify_ledger(root).head_hash == event.event_hash


def test_projection_failure_after_commit_preserves_transaction_and_marks_stale(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal(
        "projection-failure",
        [create_operation("PRJ-projection-failure")],
    )

    receipt = TransactionEngine(root, projection_factory=_FailAfterCommitProjection).apply(
        transaction,
        approved=True,
    )

    assert receipt.committed is True
    assert receipt.projection.state is ProjectionState.STALE
    assert receipt.projection.diagnostic_code == "TXN-PROJECTION-STALE"
    assert receipt.projection.invalidation_confirmed is True
    assert receipt.projection.repair_action
    assert (root / "02_knowledge" / "PRJ-projection-failure.md").is_file()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.result == "committed"
    assert verify_ledger(root).head_hash == receipt.committed_revision
    assert SQLiteProjection(root).readiness_trigger() is not None


def _fail_on_second_replace() -> ReplaceFunction:
    replacement_count = 0

    def replace(source: Path, target: Path) -> None:
        nonlocal replacement_count
        replacement_count += 1
        if replacement_count == 2:
            raise RuntimeError("injected replacement failure")
        os.replace(source, target)

    return replace


class _FailAfterCommitProjection(SQLiteProjection):
    def __init__(self, context_root: Path) -> None:
        super().__init__(context_root)
        self._rebuild_calls = 0

    def rebuild(self) -> RebuildReport:
        self._rebuild_calls += 1
        if self._rebuild_calls == 2:
            raise ProjectionBuildError("injected post-commit projection failure")
        return super().rebuild()
