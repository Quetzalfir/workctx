from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from threading import Event, current_thread, main_thread

import pytest

import workctx.transactions.engine as transaction_engine
from workctx.adapters.filesystem import (
    ContextLock,
    IntentRecord,
    LockMetadata,
    RecoveryState,
    StagedReplacement,
)
from workctx.adapters.sqlite import ProjectionBuildError, RebuildReport, SQLiteProjection
from workctx.domain.transactions import TransactionProposal
from workctx.transactions import (
    ProjectionState,
    RecoveryPendingError,
    RecoveryStrategy,
    TransactionConflictError,
    TransactionEngine,
)
from workctx.transactions.ledger import find_event_by_proposal_id, verify_ledger

from .support import (
    create_operation,
    initialize_transaction_context,
    proposal,
    workspace_snapshot,
)

ReplaceFunction = Callable[[Path, Path], None]


def test_eventless_complete_request_rolls_back_preimages_without_forward_completion(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "eventless-complete")
    transaction = proposal(
        "d031-eventless-complete",
        [create_operation("PRJ-d031-one"), create_operation("PRJ-d031-two")],
    )
    _interrupt_before_audit(root, transaction)

    assert (root / "02_knowledge" / "PRJ-d031-one.md").is_file()
    assert not (root / "02_knowledge" / "PRJ-d031-two.md").exists()
    assert find_event_by_proposal_id(root, transaction.id) is None

    recovery_calls: list[str] = []
    result = TransactionEngine(
        root,
        stager_factory=lambda path: _NoForwardCompletionStager(path, recovery_calls),
    ).recover(
        RecoveryStrategy.COMPLETE,
        transaction_id=transaction.id,
    )

    assert recovery_calls == ["rollback"]
    assert result.outcome == "rolled_back"
    assert result.applied_targets == ()
    assert not (root / "02_knowledge" / "PRJ-d031-one.md").exists()
    assert not (root / "02_knowledge" / "PRJ-d031-two.md").exists()
    assert not _intent_path(root).exists()

    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.action == "recovery"
    assert event.result == "rolled_back"
    assert verify_ledger(root).event_count == 1


def test_matching_verified_event_performs_cleanup_only_without_forward_writes(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "matching-event")
    transaction = proposal(
        "d031-matching-event",
        [create_operation("PRJ-d031-committed")],
    )
    _crash_after_verified_event(root, transaction)
    before_cleanup = workspace_snapshot(root, include_state=False)

    recovery_calls: list[str] = []
    result = TransactionEngine(
        root,
        stager_factory=lambda path: _CleanupOnlyStager(path, recovery_calls),
    ).recover(
        RecoveryStrategy.COMPLETE,
        transaction_id=transaction.id,
    )

    assert recovery_calls == ["finalize"]
    assert result.outcome == "already_finalized"
    assert result.applied_targets == ("02_knowledge/PRJ-d031-committed.md",)
    assert workspace_snapshot(root, include_state=False) == before_cleanup
    assert not _intent_path(root).exists()
    assert verify_ledger(root).event_count == 1


@pytest.mark.parametrize(
    "selector",
    ("TXP-20260801T120000Z-not-the-active-intent", ""),
)
def test_active_recovery_rejects_a_mismatched_transaction_selector(
    tmp_path: Path,
    selector: str,
) -> None:
    root = initialize_transaction_context(tmp_path / "selector-mismatch")
    transaction = proposal(
        "d031-selector-owner",
        [create_operation("PRJ-d031-selector-one"), create_operation("PRJ-d031-selector-two")],
    )
    _interrupt_before_audit(root, transaction)
    before_recovery = workspace_snapshot(root, include_state=True)

    with pytest.raises(TransactionConflictError):
        TransactionEngine(root).recover(
            RecoveryStrategy.ROLLBACK,
            transaction_id=selector,
        )

    assert workspace_snapshot(root, include_state=True) == before_recovery
    inspection = StagedReplacement(root).inspect_recovery()
    assert inspection.state is RecoveryState.PARTIALLY_APPLIED
    assert inspection.intent is not None
    assert inspection.intent.transaction_id == transaction.id
    assert find_event_by_proposal_id(root, transaction.id) is None


def test_exact_event_finalizer_failure_is_pending_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "finalizer-replay")
    transaction = proposal(
        "d031-finalizer-replay",
        [create_operation("PRJ-d031-finalizer")],
    )
    _crash_after_verified_event(root, transaction)
    committed_snapshot = workspace_snapshot(root, include_state=False)

    with pytest.raises(RecoveryPendingError) as interrupted:
        TransactionEngine(
            root,
            stager_factory=_FailRecoveryFinalizerStager,
        ).recover(
            RecoveryStrategy.ROLLBACK,
            transaction_id=transaction.id,
        )

    assert interrupted.value.inspection.state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
    assert _intent_path(root).is_file()
    assert workspace_snapshot(root, include_state=False) == committed_snapshot
    assert verify_ledger(root).event_count == 1

    replay = TransactionEngine(root).recover(
        RecoveryStrategy.ROLLBACK,
        transaction_id=transaction.id,
    )

    assert replay.outcome == "already_finalized"
    assert replay.applied_targets == ("02_knowledge/PRJ-d031-finalizer.md",)
    assert workspace_snapshot(root, include_state=False) == committed_snapshot
    assert not _intent_path(root).exists()
    assert verify_ledger(root).event_count == 1


def test_recovery_failure_is_normalized_when_intent_remains(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "rollback-failure")
    transaction = proposal(
        "d031-rollback-failure",
        [create_operation("PRJ-d031-failure-one"), create_operation("PRJ-d031-failure-two")],
    )
    _interrupt_before_audit(root, transaction)

    with pytest.raises(RecoveryPendingError) as interrupted:
        TransactionEngine(
            root,
            stager_factory=_FailRollbackStager,
        ).recover(
            RecoveryStrategy.ROLLBACK,
            transaction_id=transaction.id,
        )

    assert interrupted.value.inspection.state is RecoveryState.PARTIALLY_APPLIED
    assert _intent_path(root).is_file()
    assert (root / "02_knowledge" / "PRJ-d031-failure-one.md").is_file()
    assert not (root / "02_knowledge" / "PRJ-d031-failure-two.md").exists()
    assert find_event_by_proposal_id(root, transaction.id) is None


def test_projection_failure_after_event_gated_cleanup_preserves_commit(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "cleanup-projection-failure")
    transaction = proposal(
        "d031-cleanup-projection",
        [create_operation("PRJ-d031-projection")],
    )
    _crash_after_verified_event(root, transaction)

    result = TransactionEngine(
        root,
        projection_factory=_FailingRecoveryProjection,
    ).recover(
        RecoveryStrategy.COMPLETE,
        transaction_id=transaction.id,
    )

    assert result.outcome == "already_finalized"
    assert result.projection is not None
    assert result.projection.state is ProjectionState.STALE
    assert result.projection.diagnostic_code == "TXN-PROJECTION-STALE"
    assert result.projection.invalidation_confirmed is True
    assert result.projection.repair_action
    assert not _intent_path(root).exists()
    assert (root / "02_knowledge" / "PRJ-d031-projection.md").is_file()
    assert verify_ledger(root).event_count == 1
    assert SQLiteProjection(root).readiness_trigger() is not None


def test_recovery_refreshes_heartbeat_periodically_during_an_opaque_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = initialize_transaction_context(tmp_path / "periodic-heartbeat")
    transaction = proposal(
        "d031-periodic-heartbeat",
        [create_operation("PRJ-d031-heartbeat-one"), create_operation("PRJ-d031-heartbeat-two")],
    )
    _interrupt_before_audit(root, transaction)

    periodic_heartbeats = Event()
    background_heartbeat_count = 0

    def lock_factory(context_root: Path, session_id: str) -> ContextLock:
        lock = ContextLock.acquire(context_root, session_id=session_id)
        heartbeat = lock.heartbeat

        def counted_heartbeat() -> LockMetadata:
            nonlocal background_heartbeat_count
            metadata = heartbeat()
            if current_thread() is not main_thread():
                background_heartbeat_count += 1
                if background_heartbeat_count >= 2:
                    periodic_heartbeats.set()
            return metadata

        monkeypatch.setattr(lock, "heartbeat", counted_heartbeat)
        return lock

    monkeypatch.setattr(transaction_engine, "_HEARTBEAT_INTERVAL_SECONDS", 0.005)
    result = TransactionEngine(
        root,
        lock_factory=lock_factory,
        stager_factory=lambda path: _WaitForPeriodicHeartbeatStager(
            path,
            periodic_heartbeats,
        ),
    ).recover(
        RecoveryStrategy.ROLLBACK,
        transaction_id=transaction.id,
    )

    assert result.outcome == "rolled_back"
    assert periodic_heartbeats.is_set()
    assert background_heartbeat_count >= 2


def _interrupt_before_audit(root: Path, transaction: TransactionProposal) -> None:
    engine = TransactionEngine(
        root,
        stager_factory=lambda path: StagedReplacement(
            path,
            replace_function=_fail_on_second_replace(),
        ),
    )

    with pytest.raises(RecoveryPendingError) as interrupted:
        engine.apply(transaction, approved=True)

    assert interrupted.value.inspection.state is RecoveryState.PARTIALLY_APPLIED
    assert _intent_path(root).is_file()
    assert find_event_by_proposal_id(root, transaction.id) is None


def _crash_after_verified_event(root: Path, transaction: TransactionProposal) -> None:
    with pytest.raises(RecoveryPendingError) as interrupted:
        TransactionEngine(
            root,
            stager_factory=_CrashAfterAuditStager,
        ).apply(transaction, approved=True)

    assert interrupted.value.inspection.state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
    assert _intent_path(root).is_file()
    event = find_event_by_proposal_id(root, transaction.id)
    assert event is not None
    assert event.action == "apply"
    assert event.result == "committed"
    assert verify_ledger(root).head_hash == event.event_hash


def _fail_on_second_replace() -> ReplaceFunction:
    replacement_count = 0

    def replace(source: Path, target: Path) -> None:
        nonlocal replacement_count
        replacement_count += 1
        if replacement_count == 2:
            raise RuntimeError("injected mid-sequence replacement failure")
        os.replace(source, target)

    return replace


def _intent_path(root: Path) -> Path:
    return root / "98_state" / "staging" / "intent.json"


class _CrashAfterAuditStager(StagedReplacement):
    def finalize_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        raise RuntimeError("injected crash after verified ledger append")


class _NoForwardCompletionStager(StagedReplacement):
    def __init__(self, context_root: Path, calls: list[str]) -> None:
        super().__init__(context_root)
        self._calls = calls

    def complete_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        self._calls.append("complete")
        raise AssertionError("D-031 forbids forward completion without a ledger event")

    def rollback_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        self._calls.append("rollback")
        return super().rollback_recovery(intent, lock=lock)


class _CleanupOnlyStager(_NoForwardCompletionStager):
    def rollback_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        self._calls.append("rollback")
        raise AssertionError("A verified committed event permits cleanup only")

    def finalize_recovery_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        self._calls.append("finalize")
        super().finalize_recovery_after_audit(transaction_id, lock=lock)


class _FailRecoveryFinalizerStager(StagedReplacement):
    def finalize_recovery_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        raise RuntimeError("injected recovery finalizer failure")


class _FailRollbackStager(StagedReplacement):
    def rollback_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        raise RuntimeError("injected recovery rollback failure")


class _WaitForPeriodicHeartbeatStager(StagedReplacement):
    def __init__(self, context_root: Path, periodic_heartbeats: Event) -> None:
        super().__init__(context_root)
        self._periodic_heartbeats = periodic_heartbeats

    def rollback_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        if not self._periodic_heartbeats.wait(timeout=1.0):
            raise AssertionError("periodic heartbeat did not run during recovery")
        return super().rollback_recovery(intent, lock=lock)


class _FailingRecoveryProjection(SQLiteProjection):
    def rebuild(self) -> RebuildReport:
        raise ProjectionBuildError("injected projection failure after recovery cleanup")
