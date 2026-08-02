from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from workctx.adapters.filesystem import (
    ContextLock,
    IntentRecord,
    RecoveryState,
    StagedDelete,
    StagedMove,
    StagedReplacement,
    StagedWrite,
)
from workctx.domain.transactions import (
    ZERO_REVISION,
    AuditCreateOperation,
    AuditEvent,
    AuditEventContent,
    SystemActor,
)
from workctx.transactions import (
    PreimageChangedError,
    RecoveryPendingError,
    RecoveryStrategy,
    TransactionEngine,
)
from workctx.transactions.ledger import append_event, find_event_by_proposal_id

from .support import (
    content_hash,
    create_operation,
    entity_document,
    initialize_transaction_context,
    proposal,
)

StagedOperation = StagedWrite | StagedMove | StagedDelete


def test_preimage_change_during_prepare_is_never_overwritten(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    create = proposal("prepare-race-base", [create_operation("PRJ-race")])
    first_receipt = TransactionEngine(root).apply(create, approved=True)
    target = root / "02_knowledge" / "PRJ-race.md"
    original = target.read_bytes()
    external_preimage = original + b"External process note.\n"
    update = proposal(
        "prepare-race-update",
        [
            {
                "op": "update",
                "target": "02_knowledge/PRJ-race.md",
                "payload": entity_document("PRJ-race", body="Proposed replacement.\n"),
                "expected_hash": content_hash(original),
            }
        ],
        base_revision=first_receipt.committed_revision,
    )

    with pytest.raises(PreimageChangedError) as conflict:
        TransactionEngine(
            root,
            stager_factory=lambda path: _MutateBeforePrepare(
                path,
                target=target,
                external_preimage=external_preimage,
            ),
        ).apply(update, approved=True)

    assert conflict.value.code == "TXN-PREIMAGE-CHANGED"
    assert conflict.value.result.outcome == "rolled_back"
    assert conflict.value.result.ledger_event_hash is not None
    assert target.read_bytes() == external_preimage
    assert not (root / "98_state" / "staging" / "intent.json").exists()
    event = find_event_by_proposal_id(root, update.id)
    assert event is not None
    assert event.result == "rolled_back"

    finalized = TransactionEngine(root).recover(
        RecoveryStrategy.ROLLBACK,
        transaction_id=update.id,
    )
    assert finalized.outcome == "already_finalized"
    assert target.read_bytes() == external_preimage


def test_existing_event_must_exactly_match_intent_before_finalization(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction_id = "TXP-20260801T120000Z-intent-event-mismatch"
    lock = ContextLock.acquire(root, session_id="fixture-intent")
    stager = StagedReplacement(root)
    stager.prepare(
        transaction_id,
        lock.nonce,
        [StagedWrite("02_knowledge/PRJ-intent.md", b"staged bytes\n")],
        lock=lock,
    )
    mismatched = AuditEvent.seal(
        AuditEventContent(
            schema_version=1,
            id="AUD-20260801T120000Z-intent-event-mismatch",
            proposal_id=transaction_id,
            context_id="transaction-lab",
            timestamp=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            actor=SystemActor(
                type="system",
                id="workctx-transaction-recovery",
                agent=None,
                model=None,
            ),
            action="recovery",
            result="rolled_back",
            base_revision=ZERO_REVISION,
            source_refs=[],
            operations=[
                AuditCreateOperation(
                    op="create",
                    target="02_knowledge/PRJ-different.md",
                    postimage_hash=f"sha256:{'a' * 64}",
                )
            ],
            prev_hash=ZERO_REVISION,
        )
    )
    append_event(root, mismatched, lock=lock)
    lock.release()

    with pytest.raises(RecoveryPendingError) as pending:
        TransactionEngine(root).recover(RecoveryStrategy.ROLLBACK)

    assert pending.value.inspection.state is RecoveryState.PREPARED
    assert (root / "98_state" / "staging" / "intent.json").is_file()


class _MutateBeforePrepare(StagedReplacement):
    def __init__(self, context_root: Path, *, target: Path, external_preimage: bytes) -> None:
        super().__init__(context_root)
        self._target = target
        self._external_preimage = external_preimage

    def prepare(
        self,
        transaction_id: str,
        nonce: str,
        writes: Iterable[StagedOperation],
        *,
        lock: ContextLock,
    ) -> IntentRecord:
        self._target.write_bytes(self._external_preimage)
        return super().prepare(transaction_id, nonce, writes, lock=lock)
