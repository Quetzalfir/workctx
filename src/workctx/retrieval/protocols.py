"""Typed read boundary consumed by deterministic retrieval services."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from workctx.adapters.sqlite import (
    ClaimRecord,
    EdgeRecord,
    EntityRecord,
    ObservationRecord,
    ProjectionMetadata,
    SearchHit,
    TaskQuery,
    TaskRecord,
)
from workctx.domain import ClaimStatus, EntityType, RelationType, WorkctxUri
from workctx.retrieval.records import DocumentRecord


@runtime_checkable
class ProjectionReader(Protocol):
    """Structural protocol for the WP-210 context-bound query surface."""

    @property
    def context_id(self) -> str: ...

    def metadata(self) -> ProjectionMetadata: ...

    def get_entity_by_id(self, entity_id: str) -> EntityRecord | None: ...

    def get_entity_by_uri(self, uri: str | WorkctxUri) -> EntityRecord | None: ...

    def find_entities_by_alias(self, alias: str) -> tuple[EntityRecord, ...]: ...

    def get_document_by_uri(self, uri: str | WorkctxUri) -> DocumentRecord | None: ...

    def outbound_edges(
        self,
        source: str | WorkctxUri,
        *,
        relations: frozenset[RelationType] | None = None,
    ) -> tuple[EdgeRecord, ...]: ...

    def inbound_edges(
        self,
        target: str | WorkctxUri,
        *,
        relations: frozenset[RelationType] | None = None,
    ) -> tuple[EdgeRecord, ...]: ...

    def get_observation(self, observation: str | WorkctxUri) -> ObservationRecord | None: ...

    def observations_for_parent(
        self, parent: str | WorkctxUri
    ) -> tuple[ObservationRecord, ...]: ...

    def get_claim(self, claim: str | WorkctxUri) -> ClaimRecord | None: ...

    def claims_for_subject(
        self,
        subject: str | WorkctxUri,
        *,
        statuses: frozenset[ClaimStatus] | None = None,
    ) -> tuple[ClaimRecord, ...]: ...

    def get_task(self, task: str | WorkctxUri) -> TaskRecord | None: ...

    def query_tasks(self, query: TaskQuery | None = None) -> tuple[TaskRecord, ...]: ...

    def search(
        self,
        query: str,
        *,
        entity_types: frozenset[EntityType] | None = None,
        limit: int = 20,
    ) -> tuple[SearchHit, ...]: ...
