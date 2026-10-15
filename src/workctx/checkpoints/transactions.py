"""Checkpoint payload adapter over the audited transaction engine."""

from __future__ import annotations

from pydantic import Field, SerializeAsAny

from workctx.adapters.filesystem import (
    StagedWrite,
    dump_yaml_bytes,
    has_hand_edits_yaml,
    load_yaml_model,
)
from workctx.domain import Checkpoint
from workctx.domain.transactions import (
    CheckpointDocumentPayload,
    CreateOperation,
    DocumentPayload,
    TransactionOperation,
    TransactionProposal,
    UpdateOperation,
)
from workctx.transactions import TransactionEngine


class CheckpointCreateOperation(CreateOperation):
    payload: CheckpointDocumentPayload  # type: ignore[assignment]


class CheckpointUpdateOperation(UpdateOperation):
    payload: CheckpointDocumentPayload  # type: ignore[assignment]


class CheckpointTransactionProposal(TransactionProposal):
    operations: list[SerializeAsAny[TransactionOperation]] = Field(min_length=1)


class CheckpointTransactionEngine(TransactionEngine):
    def _prepare_document(self, target: str, payload: DocumentPayload) -> StagedWrite:
        if isinstance(payload, CheckpointDocumentPayload):
            self._resolve_operation_path(target)
            write = StagedWrite(target, dump_yaml_bytes(payload.document))
            self._remember_prepared_target(write.target)
            return write
        return super()._prepare_document(target, payload)

    def _document_has_hand_edits(
        self,
        target: str,
        payload: DocumentPayload,
        current: bytes,
    ) -> bool:
        if isinstance(payload, CheckpointDocumentPayload):
            del target
            return has_hand_edits_yaml(current, Checkpoint)
        return super()._document_has_hand_edits(target, payload, current)

    def _require_stable_update_identity(
        self,
        target: str,
        payload: DocumentPayload,
        current: bytes,
    ) -> None:
        if isinstance(payload, CheckpointDocumentPayload):
            del target
            existing = load_yaml_model(current, Checkpoint)
            if existing.id != payload.document.id:
                raise ValueError("checkpoint identity cannot change during update")
            return
        super()._require_stable_update_identity(target, payload, current)


__all__ = [
    "CheckpointCreateOperation",
    "CheckpointTransactionEngine",
    "CheckpointTransactionProposal",
    "CheckpointUpdateOperation",
]
