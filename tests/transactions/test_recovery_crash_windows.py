from __future__ import annotations

from pathlib import Path

import pytest

from workctx.adapters.filesystem import (
    ContextLock,
    IntentRecord,
    RecoveryState,
    StagedReplacement,
)
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain.transactions import TransactionProposal
from workctx.transactions import (
    ProjectionState,
    RecoveryPendingError,
    RecoveryStrategy,
    TransactionEngine,
)
from workctx.transactions.ledger import find_event_by_proposal_id, verify_ledger

from .support import (
    content_hash,
    create_operation,
    entity_document,
    initialize_transaction_context,
    proposal,
)


def test_all_postimages_without_event_restore_preimages_and_delete_creates(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "all-postimages-eventless")
    seed = proposal("crash-window-seed", [create_operation("PRJ-existing")])
    seed_result = TransactionEngine(root).apply(seed, approved=True)
    existing = root / "02_knowledge" / "PRJ-existing.md"
    original = existing.read_bytes()

    transaction = proposal(
        "all-postimages-eventless",
        [
            {
                "op": "update",
                "target": "02_knowledge/PRJ-existing.md",
                "payload": entity_document(
                    "PRJ-existing",
                    body="Uncommitted replacement that must be rolled back.\n",
                ),
                "expected_hash": content_hash(original),
            },
            create_operation("PRJ-uncommitted-create"),
        ],
        base_revision=seed_result.committed_revision,
    )
    _leave_all_postimages_without_event(root, transaction)

    created = root / "02_knowledge" / "PRJ-uncommitted-create.md"
    assert existing.read_bytes() != original
    assert created.is_file()
    assert verify_ledger(root).event_count == 1

    recovery_calls: list[str] = []
    result = TransactionEngine(
        root,
        stager_factory=lambda path: _EventlessRollbackOnlyStager(path, recovery_calls),
    ).recover(
        RecoveryStrategy.COMPLETE,
        transaction_id=transaction.id,
    )

    assert recovery_calls == ["rollback", "finalize_rollback"]
    assert result.strategy is RecoveryStrategy.COMPLETE
    assert result.outcome == "rolled_back"
    assert result.applied_targets == ()
    assert existing.read_bytes() == original
    assert not created.exists()
    assert not _intent_path(root).exists()

    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.action == "recovery"
    assert event.result == "rolled_back"
    assert result.committed_revision == event.event_hash
    assert verify_ledger(root).event_count == 2


def test_rollback_event_then_finalize_failure_replays_cleanup_only(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "rollback-finalize-replay")
    transaction = proposal(
        "rollback-finalize-replay",
        [create_operation("PRJ-rollback-finalize")],
    )
    _leave_all_postimages_without_event(root, transaction)

    with pytest.raises(RecoveryPendingError) as interrupted:
        TransactionEngine(
            root,
            stager_factory=_FailRollbackFinalizerStager,
        ).recover(
            RecoveryStrategy.ROLLBACK,
            transaction_id=transaction.id,
        )

    assert interrupted.value.inspection.state is RecoveryState.PREPARED
    assert _intent_path(root).is_file()
    assert not (root / "02_knowledge" / "PRJ-rollback-finalize.md").exists()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.action == "recovery"
    assert event.result == "rolled_back"
    assert verify_ledger(root).event_count == 1

    cleanup_calls: list[str] = []
    replay = TransactionEngine(
        root,
        stager_factory=lambda path: _RolledBackEventCleanupOnlyStager(
            path,
            cleanup_calls,
        ),
    ).recover(
        RecoveryStrategy.COMPLETE,
        transaction_id=transaction.id,
    )

    assert cleanup_calls == ["finalize_rollback"]
    assert replay.outcome == "already_finalized"
    assert replay.applied_targets == ()
    assert replay.ledger_event_id == event.id
    assert replay.ledger_event_hash == event.event_hash
    assert not _intent_path(root).exists()
    assert verify_ledger(root).event_count == 1


def test_projection_factory_construction_failure_preserves_committed_cleanup(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "committed-projection-construction")
    transaction = proposal(
        "committed-projection-construction",
        [create_operation("PRJ-committed-projection")],
    )
    _leave_committed_event_with_active_intent(root, transaction)

    result = TransactionEngine(
        root,
        projection_factory=_fail_projection_construction,
    ).recover(
        RecoveryStrategy.COMPLETE,
        transaction_id=transaction.id,
    )

    assert result.outcome == "already_finalized"
    assert result.projection is not None
    assert result.projection.state is ProjectionState.STALE
    assert result.projection.invalidation_confirmed is True
    assert (root / "02_knowledge" / "PRJ-committed-projection.md").is_file()
    assert not _intent_path(root).exists()
    assert verify_ledger(root).event_count == 1
    assert SQLiteProjection(root).readiness_trigger() is not None


def test_projection_factory_construction_failure_preserves_eventless_rollback(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "rollback-projection-construction")
    transaction = proposal(
        "rollback-projection-construction",
        [create_operation("PRJ-rollback-projection")],
    )
    _leave_all_postimages_without_event(root, transaction)

    result = TransactionEngine(
        root,
        projection_factory=_fail_projection_construction,
    ).recover(
        RecoveryStrategy.ROLLBACK,
        transaction_id=transaction.id,
    )

    assert result.outcome == "rolled_back"
    assert result.projection is not None
    assert result.projection.state is ProjectionState.STALE
    assert result.projection.invalidation_confirmed is True
    assert not (root / "02_knowledge" / "PRJ-rollback-projection.md").exists()
    assert not _intent_path(root).exists()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.result == "rolled_back"
    assert verify_ledger(root).event_count == 1
    assert SQLiteProjection(root).readiness_trigger() is not None


def test_stager_factory_construction_failure_releases_recovery_lock(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "stager-construction-lock")

    with pytest.raises(RuntimeError, match="stager construction failure"):
        TransactionEngine(
            root,
            stager_factory=_fail_stager_construction,
        ).recover(
            RecoveryStrategy.ROLLBACK,
            transaction_id="TXP-20260801T120000Z-stager-construction-lock",
        )

    assert not (root / "98_state" / "lock.json").exists()
    successor = ContextLock.acquire(root, session_id="successor-after-construction-failure")
    try:
        successor.verify_fence()
    finally:
        successor.release()


def _leave_all_postimages_without_event(
    root: Path,
    transaction: TransactionProposal,
) -> None:
    with pytest.raises(RecoveryPendingError) as interrupted:
        TransactionEngine(
            root,
            stager_factory=_CrashAfterAllPostimagesStager,
        ).apply(transaction, approved=True)

    assert interrupted.value.inspection.state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
    assert _intent_path(root).is_file()
    assert find_event_by_proposal_id(root, transaction.id) is None


def _leave_committed_event_with_active_intent(
    root: Path,
    transaction: TransactionProposal,
) -> None:
    with pytest.raises(RecoveryPendingError) as interrupted:
        TransactionEngine(
            root,
            stager_factory=_CrashAfterCommittedEventStager,
        ).apply(transaction, approved=True)

    assert interrupted.value.inspection.state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
    assert _intent_path(root).is_file()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.result == "committed"


def _intent_path(root: Path) -> Path:
    return root / "98_state" / "staging" / "intent.json"


def _fail_projection_construction(_context_root: Path) -> SQLiteProjection:
    raise RuntimeError("injected projection construction failure")


def _fail_stager_construction(_context_root: Path) -> StagedReplacement:
    raise RuntimeError("injected stager construction failure")


class _CrashAfterAllPostimagesStager(StagedReplacement):
    def apply(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        super().apply(intent, lock=lock)
        raise RuntimeError("injected crash after every postimage")


class _CrashAfterCommittedEventStager(StagedReplacement):
    def finalize_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        raise RuntimeError("injected crash after committed ledger event")


class _EventlessRollbackOnlyStager(StagedReplacement):
    def __init__(self, context_root: Path, calls: list[str]) -> None:
        super().__init__(context_root)
        self._calls = calls

    def complete_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        raise AssertionError("eventless recovery must never complete staged postimages")

    def finalize_recovery_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        raise AssertionError("eventless recovery must never use committed cleanup")

    def rollback_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        self._calls.append("rollback")
        return super().rollback_recovery(intent, lock=lock)

    def finalize_rollback_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        self._calls.append("finalize_rollback")
        super().finalize_rollback_after_audit(transaction_id, lock=lock)


class _FailRollbackFinalizerStager(StagedReplacement):
    def finalize_rollback_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        raise RuntimeError("injected rollback finalizer failure")


class _RolledBackEventCleanupOnlyStager(StagedReplacement):
    def __init__(self, context_root: Path, calls: list[str]) -> None:
        super().__init__(context_root)
        self._calls = calls

    def complete_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        raise AssertionError("verified rollback event must never apply postimages")

    def rollback_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        raise AssertionError("verified rollback event replay must be cleanup only")

    def finalize_recovery_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        raise AssertionError("rolled-back event cannot use committed cleanup")

    def finalize_rollback_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        self._calls.append("finalize_rollback")
        super().finalize_rollback_after_audit(transaction_id, lock=lock)
