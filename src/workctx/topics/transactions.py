"""Topic-registry payload adapter over the existing audited transaction engine."""

from __future__ import annotations

from pydantic import Field, SerializeAsAny

from workctx.adapters.filesystem import (
    StagedWrite,
    dump_yaml_bytes,
    has_hand_edits_yaml,
    load_yaml_model,
)
from workctx.domain import TopicRegistry
from workctx.domain.transactions import (
    CreateOperation,
    DocumentPayload,
    TopicRegistryDocumentPayload,
    TransactionOperation,
    TransactionProposal,
    UpdateOperation,
)
from workctx.transactions import TransactionEngine


class TopicRegistryCreateOperation(CreateOperation):
    """Create the canonical registry through a standard create operation."""

    payload: TopicRegistryDocumentPayload  # type: ignore[assignment]


class TopicRegistryUpdateOperation(UpdateOperation):
    """Update the canonical registry through a standard update operation."""

    payload: TopicRegistryDocumentPayload  # type: ignore[assignment]


class TopicTransactionProposal(TransactionProposal):
    """Internal proposal retaining specialized operation subclasses during serialization."""

    operations: list[SerializeAsAny[TransactionOperation]] = Field(min_length=1)


class TopicTransactionEngine(TransactionEngine):
    """Serialize one extra canonical payload while reusing all audited commit behavior."""

    def _prepare_document(self, target: str, payload: DocumentPayload) -> StagedWrite:
        if isinstance(payload, TopicRegistryDocumentPayload):
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
        if isinstance(payload, TopicRegistryDocumentPayload):
            del target
            return has_hand_edits_yaml(current, TopicRegistry)
        return super()._document_has_hand_edits(target, payload, current)

    def _require_stable_update_identity(
        self,
        target: str,
        payload: DocumentPayload,
        current: bytes,
    ) -> None:
        if isinstance(payload, TopicRegistryDocumentPayload):
            del target
            existing = load_yaml_model(current, TopicRegistry)
            if existing.id != payload.document.id:  # pragma: no cover - constant invariant
                raise ValueError("topic registry identity cannot change during update")
            return
        super()._require_stable_update_identity(target, payload, current)


__all__ = [
    "TopicRegistryCreateOperation",
    "TopicRegistryUpdateOperation",
    "TopicTransactionEngine",
    "TopicTransactionProposal",
]
