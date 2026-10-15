"""Topic-scoped projection reader preserving the shared common layer."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar, cast

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
from workctx.retrieval.protocols import ProjectionReader
from workctx.retrieval.records import DocumentRecord

_TopicRecord = TypeVar(
    "_TopicRecord",
    EntityRecord,
    TaskRecord,
    ClaimRecord,
    ObservationRecord,
    EdgeRecord,
    SearchHit,
)


def topic_matches(topics: tuple[str, ...], topic: str, *, topic_only: bool) -> bool:
    """Return whether canonical topic values belong in one scoped read."""

    return topic in topics or (not topic_only and not topics)


class TopicScopedReader:
    """Filter every typed read while delegating storage and boundary enforcement."""

    def __init__(self, reader: ProjectionReader, topic: str, *, topic_only: bool = False) -> None:
        if not topic:
            raise ValueError("topic must not be empty")
        self._reader = reader
        self._topic = topic
        self._topic_only = topic_only

    @property
    def context_id(self) -> str:
        return self._reader.context_id

    @property
    def usage_enabled(self) -> bool:
        return bool(getattr(self._reader, "usage_enabled", False))

    @property
    def context_root(self) -> Path:
        return cast(Path, cast(Any, self._reader).context_root)

    def metadata(self) -> ProjectionMetadata:
        return self._reader.metadata()

    def get_entity_by_id(self, entity_id: str) -> EntityRecord | None:
        return self._one(self._reader.get_entity_by_id(entity_id))

    def get_entity_by_uri(self, uri: str | WorkctxUri) -> EntityRecord | None:
        return self._one(self._reader.get_entity_by_uri(uri))

    def find_entities_by_alias(self, alias: str) -> tuple[EntityRecord, ...]:
        return self._many(self._reader.find_entities_by_alias(alias))

    def get_document_by_uri(self, uri: str | WorkctxUri) -> DocumentRecord | None:
        record = self._reader.get_document_by_uri(uri)
        return record if record is not None and self._includes(record) else None

    def outbound_edges(
        self,
        source: str | WorkctxUri,
        *,
        relations: frozenset[RelationType] | None = None,
    ) -> tuple[EdgeRecord, ...]:
        return tuple(
            edge
            for edge in self._reader.outbound_edges(source, relations=relations)
            if self._includes(edge) and self._target_in_scope(edge.target)
        )

    def inbound_edges(
        self,
        target: str | WorkctxUri,
        *,
        relations: frozenset[RelationType] | None = None,
    ) -> tuple[EdgeRecord, ...]:
        return self._many(self._reader.inbound_edges(target, relations=relations))

    def get_observation(self, observation: str | WorkctxUri) -> ObservationRecord | None:
        return self._one(self._reader.get_observation(observation))

    def observations_for_parent(
        self,
        parent: str | WorkctxUri,
    ) -> tuple[ObservationRecord, ...]:
        return self._many(self._reader.observations_for_parent(parent))

    def get_claim(self, claim: str | WorkctxUri) -> ClaimRecord | None:
        return self._one(self._reader.get_claim(claim))

    def claims_for_subject(
        self,
        subject: str | WorkctxUri,
        *,
        statuses: frozenset[ClaimStatus] | None = None,
    ) -> tuple[ClaimRecord, ...]:
        return self._many(self._reader.claims_for_subject(subject, statuses=statuses))

    def get_task(self, task: str | WorkctxUri) -> TaskRecord | None:
        return self._one(self._reader.get_task(task))

    def query_tasks(self, query: TaskQuery | None = None) -> tuple[TaskRecord, ...]:
        scoped = replace(
            TaskQuery() if query is None else query,
            topic=self._topic,
            topic_only=self._topic_only,
        )
        return self._reader.query_tasks(scoped)

    def search(
        self,
        query: str,
        *,
        entity_types: frozenset[EntityType] | None = None,
        limit: int = 20,
        topic: str | None = None,
        topic_only: bool = False,
    ) -> tuple[SearchHit, ...]:
        del topic, topic_only
        return self._reader.search(
            query,
            entity_types=entity_types,
            limit=limit,
            topic=self._topic,
            topic_only=self._topic_only,
        )

    def _one(self, record: _TopicRecord | None) -> _TopicRecord | None:
        return record if record is not None and self._includes(record) else None

    def _many(self, records: tuple[_TopicRecord, ...]) -> tuple[_TopicRecord, ...]:
        return tuple(record for record in records if self._includes(record))

    def _includes(self, record: Any) -> bool:
        return topic_matches(
            cast(tuple[str, ...], record.topics),
            self._topic,
            topic_only=self._topic_only,
        )

    def _target_in_scope(self, target: str) -> bool:
        if not target.startswith("workctx://"):
            return True
        try:
            record = self._reader.get_document_by_uri(WorkctxUri.parse(target))
        except ValueError:
            return True
        return record is None or self._includes(record)


__all__ = ["TopicScopedReader", "topic_matches"]
