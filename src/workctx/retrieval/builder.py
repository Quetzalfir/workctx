"""Deterministic assembly and budget trimming for ten-section context packs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import JsonValue

import workctx.usage as usage
from workctx.adapters.sqlite import (
    ClaimRecord,
    EdgeRecord,
    EntityRecord,
    ObservationRecord,
    ProjectionMetadata,
    TaskQuery,
    TaskRecord,
)
from workctx.domain import (
    ClaimStatus,
    Confidence,
    EntityType,
    ObservationKind,
    RelationType,
    SourceLocator,
    TaskPriority,
)
from workctx.domain.references import validate_durable_reference
from workctx.errors import WorkctxError
from workctx.retrieval.graph import related
from workctx.retrieval.models import (
    BudgetAndTruncation,
    ContextPack,
    ContextPackSections,
    OmittedItem,
    PackItem,
    PackItemKind,
    PackSection,
    PackSectionName,
    RankMetadata,
)
from workctx.retrieval.protocols import ProjectionReader
from workctx.retrieval.ranking import RankedCandidate, RankingCandidate, rank
from workctx.retrieval.records import (
    DocumentRecord,
    MissingObservationReason,
    RelatedResult,
    ResolutionStatus,
    TracedObservation,
    TraversedEdge,
    WorkctxReferenceDescriptor,
)
from workctx.retrieval.references import ResolvableReference, resolve
from workctx.retrieval.security import sanitize_json, sanitize_named_value, sanitize_text
from workctx.retrieval.tracing import TraceResult, trace

DEFAULT_BUDGET_UNITS = 12_000
_BUDGET_UNIT: Literal["approx_tokens_chars_div_4"] = "approx_tokens_chars_div_4"
_CURRENT_STATUSES = frozenset({ClaimStatus.CURRENT, ClaimStatus.UNCERTAIN})
_HISTORICAL_STATUSES = frozenset({ClaimStatus.SUPERSEDED, ClaimStatus.RETRACTED})
_ARCHITECTURE_TYPES = frozenset(
    {
        EntityType.PROJECT,
        EntityType.SYSTEM,
        EntityType.SERVICE,
        EntityType.MODULE,
        EntityType.FLOW,
        EntityType.INTEGRATION,
    }
)
_DECISION_RISK_QUESTION_TYPES = frozenset(
    {EntityType.DECISION, EntityType.RISK, EntityType.QUESTION}
)


class PackBuildStatus(StrEnum):
    BUILT = "built"
    NOT_FOUND = "not_found"
    UNSUPPORTED_REFERENCE = "unsupported_reference"


class ProjectionChangedError(WorkctxError):
    """Raised when a projection changes during both bounded assembly attempts."""


@dataclass(frozen=True, slots=True)
class PackBuildResult:
    status: PackBuildStatus
    reference: str
    pack: ContextPack | None
    message: str | None = None

    @property
    def built(self) -> bool:
        return self.status is PackBuildStatus.BUILT


@dataclass(frozen=True, slots=True)
class _Entry:
    section: PackSectionName
    item: PackItem
    ranking: RankingCandidate
    drop_tier: int


@dataclass(frozen=True, slots=True)
class _RankedEntry:
    section: PackSectionName
    item: PackItem
    score: int
    rank_order: int
    drop_tier: int


def build_pack(
    reader: ProjectionReader,
    reference: ResolvableReference,
    *,
    budget: int = DEFAULT_BUDGET_UNITS,
    query: str | None = None,
    include_history: bool = False,
    include_architecture: bool = False,
) -> PackBuildResult:
    """Build a deterministic pack without reading outside the typed projection API."""

    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a non-negative integer")
    normalized_query = None if query is None or not query.strip() else query.strip()

    initial = resolve(reader, reference)
    if getattr(reader, "usage_enabled", False):
        usage.record(cast(Any, reader).context_root, "build_pack", initial.reference)
    if initial.status is ResolutionStatus.NOT_FOUND:
        return PackBuildResult(
            status=PackBuildStatus.NOT_FOUND,
            reference=initial.reference,
            pack=None,
            message="The focal Work Context entity was not found.",
        )
    if not isinstance(initial.descriptor, WorkctxReferenceDescriptor) or initial.record is None:
        return PackBuildResult(
            status=PackBuildStatus.UNSUPPORTED_REFERENCE,
            reference=initial.reference,
            pack=None,
            message="Context packs require a projected workctx:// focal entity.",
        )

    for _attempt in range(2):
        before = reader.metadata()
        focal = resolve(reader, initial.descriptor.uri)
        if focal.status is ResolutionStatus.NOT_FOUND or focal.record is None:
            return PackBuildResult(
                status=PackBuildStatus.NOT_FOUND,
                reference=focal.reference,
                pack=None,
                message="The focal Work Context entity was not found.",
            )
        pack = _assemble_pack(
            reader,
            focal.record,
            before,
            budget=budget,
            query=normalized_query,
            include_history=include_history,
            include_architecture=include_architecture,
        )
        after = reader.metadata()
        if _same_projection(before, after):
            return PackBuildResult(
                status=PackBuildStatus.BUILT,
                reference=focal.reference,
                pack=pack,
            )
    raise ProjectionChangedError("Projection changed repeatedly during context-pack assembly")


def estimate_pack_item_units(item: PackItem) -> int:
    """Estimate units as the ceiling of compact JSON character count divided by four."""

    compact = json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (len(compact) + 3) // 4


def _assemble_pack(
    reader: ProjectionReader,
    focal_record: DocumentRecord,
    metadata: ProjectionMetadata,
    *,
    budget: int,
    query: str | None,
    include_history: bool,
    include_architecture: bool,
) -> ContextPack:
    focal_uri = str(focal_record.uri)
    traversal = related(reader, focal_uri, depth=1)
    # Compact historical and superseding metadata still needs its exact evidence.
    # Claim-body inclusion is controlled independently by _claim_entries.
    source_trace = trace(reader, focal_uri, include_history=True)
    observation_by_uri = {
        str(item.observation.uri): item.observation for item in source_trace.observations
    }
    node_by_reference = {node.reference: node.record for node in traversal.nodes}

    entries: list[_Entry] = []
    claim_subject = (
        focal_record.subject if isinstance(focal_record, ClaimRecord) else focal_record.uri
    )
    claims = reader.claims_for_subject(claim_subject)
    entries.extend(
        _claim_entries(
            claims,
            observation_by_uri,
            include_history=include_history,
        )
    )
    entries.extend(
        _relationship_entries(
            traversal,
            node_by_reference,
            observation_by_uri,
        )
    )
    entries.extend(_observation_entries(source_trace))
    entries.extend(
        _related_task_entries(
            reader,
            focal_record,
            traversal,
            observation_by_uri,
        )
    )
    entries.extend(
        _people_entries(
            reader,
            focal_record,
            traversal,
            observation_by_uri,
        )
    )
    entries.extend(_decision_risk_question_entries(traversal, observation_by_uri))
    entries.extend(
        _contradictory_entries(
            traversal,
            claims,
            observation_by_uri,
        )
    )
    if include_architecture:
        entries.extend(_architecture_entries(traversal, observation_by_uri))

    ranked_by_section = _rank_and_group(entries, query)
    full_focal = _record_item(focal_record)
    minimal_focal = _record_item(focal_record, minimal=True)
    sections = _apply_budget(
        ranked_by_section,
        full_focal=full_focal,
        minimal_focal=minimal_focal,
        requested_units=budget,
    )
    return ContextPack(
        schema_version=1,
        context_id=metadata.context_id,
        focal_uri=focal_uri,
        source_updated_at=metadata.context_updated_at,
        source_fingerprint=metadata.source_fingerprint,
        query=query,
        include_history=include_history,
        include_architecture=include_architecture,
        sections=sections,
    )


def _claim_entries(
    claims: tuple[ClaimRecord, ...],
    observations: dict[str, ObservationRecord],
    *,
    include_history: bool,
) -> list[_Entry]:
    entries: list[_Entry] = []
    selected = (
        claims
        if include_history
        else tuple(claim for claim in claims if claim.status in _CURRENT_STATUSES)
    )
    for claim in selected:
        source_observation = _first_claim_observation(claim, observations)
        tier = 1 if claim.status in _HISTORICAL_STATUSES else 8
        entries.append(
            _entry_for_record(
                PackSectionName.CLAIMS_AND_STATUS_HISTORY,
                _record_item(claim),
                claim,
                drop_tier=tier,
                relation=RelationType.EVIDENCED_BY,
                source_observation=source_observation,
                depth=0,
            )
        )

    historical = tuple(claim for claim in claims if claim.status in _HISTORICAL_STATUSES)
    if historical and not include_history:
        data = _sanitized_mapping(
            {
                "count": len(historical),
                "claims": [
                    {
                        "id": claim.id,
                        "status": claim.status.value,
                        "observed_at": claim.observed_at.isoformat(),
                        "supersedes": claim.supersedes,
                        "superseded_by": claim.superseded_by,
                    }
                    for claim in historical
                ],
            }
        )
        item = PackItem(
            id="claim-history-summary",
            kind=PackItemKind.STATUS_HISTORY,
            uri=None,
            title="Superseded claim history",
            summary=(f"{len(historical)} historical claim(s); request history for full bodies."),
            data=data,
            rank=None,
        )
        entries.append(
            _make_entry(
                PackSectionName.CLAIMS_AND_STATUS_HISTORY,
                item,
                drop_tier=1,
                claim_status=ClaimStatus.SUPERSEDED,
                confidence=_strongest_confidence(claim.confidence for claim in historical),
                depth=0,
                search_text="superseded claim history",
                entity_type=EntityType.CLAIM,
            )
        )
    return entries


def _relationship_entries(
    traversal: RelatedResult,
    node_by_reference: dict[str, DocumentRecord | None],
    observations: dict[str, ObservationRecord],
) -> list[_Entry]:
    return [
        _edge_entry(
            PackSectionName.DIRECT_RELATIONSHIPS,
            traversed,
            node_by_reference.get(_neighbor_reference(traversed)),
            observations,
            drop_tier=5,
        )
        for traversed in traversal.edges
    ]


def _observation_entries(source_trace: TraceResult) -> list[_Entry]:
    entries: list[_Entry] = []
    for traced in source_trace.observations:
        observation = traced.observation
        item = _observation_item(traced)
        entries.append(
            _entry_for_record(
                PackSectionName.SOURCE_OBSERVATIONS,
                item,
                observation,
                drop_tier=7,
                relation=RelationType.EVIDENCED_BY,
                depth=1,
            )
        )
    for missing in source_trace.missing_observations:
        item = PackItem(
            id=f"missing:{_digest(missing.reference)}",
            kind=PackItemKind.OBSERVATION,
            uri=_missing_observation_uri(missing.reference, missing.reason),
            title="Unavailable source observation",
            summary="An authored observation reference could not be resolved.",
            data=_sanitized_mapping(
                {
                    "reference": missing.reference,
                    "reason": missing.reason.value,
                    "referenced_by": list(missing.referenced_by),
                }
            ),
            rank=None,
        )
        entries.append(
            _make_entry(
                PackSectionName.SOURCE_OBSERVATIONS,
                item,
                drop_tier=7,
                relation=RelationType.EVIDENCED_BY,
                depth=1,
                search_text=item.summary,
                entity_type=EntityType.OBSERVATION,
            )
        )
    return entries


def _related_task_entries(
    reader: ProjectionReader,
    focal: DocumentRecord,
    traversal: RelatedResult,
    observations: dict[str, ObservationRecord],
) -> list[_Entry]:
    section = PackSectionName.RELATED_TASKS_AND_DEPENDENCIES
    entries: list[_Entry] = []
    relation_by_reference = _neighbor_relations(traversal)
    for node in traversal.nodes:
        if isinstance(node.record, TaskRecord):
            traversed_relation = relation_by_reference.get(node.reference)
            entries.append(
                _entry_for_record(
                    section,
                    _record_item(node.record),
                    node.record,
                    drop_tier=4,
                    relation=(
                        None if traversed_relation is None else traversed_relation.edge.relation
                    ),
                    source_observation=_first_edge_observation(
                        traversed_relation,
                        observations,
                    ),
                    depth=node.depth,
                )
            )

    if not isinstance(focal, TaskRecord):
        return _dedupe_entries(entries)

    for subtask in reader.query_tasks(TaskQuery(parent_task=focal.id)):
        entries.append(
            _entry_for_record(
                section,
                _record_item(subtask),
                subtask,
                drop_tier=4,
                relation=RelationType.PARENT_OF,
                depth=1,
            )
        )
    for task_id in (focal.parent_task, focal.root_task):
        if task_id is None or task_id == focal.id:
            continue
        related_task = reader.get_task(task_id)
        if related_task is not None:
            entries.append(
                _entry_for_record(
                    section,
                    _record_item(related_task),
                    related_task,
                    drop_tier=4,
                    relation=RelationType.PARENT_OF,
                    depth=1,
                )
            )

    task_fields = (
        ("dependency", RelationType.DEPENDS_ON, focal.dependencies),
        ("blocker", RelationType.BLOCKS, focal.blockers),
    )
    for label, field_relation, values in task_fields:
        for position, value in enumerate(values):
            resolved_task = _resolve_task(reader, value)
            if resolved_task is not None:
                entries.append(
                    _entry_for_record(
                        section,
                        _record_item(resolved_task),
                        resolved_task,
                        drop_tier=4,
                        relation=field_relation,
                        depth=1,
                    )
                )
            item = PackItem(
                id=f"{label}:{focal.id}:{position}",
                kind=PackItemKind.DEPENDENCY,
                uri=str(resolved_task.uri) if resolved_task is not None else None,
                title=label.capitalize(),
                summary=sanitize_text(value),
                data=_sanitized_mapping({"relation": field_relation.value, "value": value}),
                rank=None,
            )
            entries.append(
                _make_entry(
                    section,
                    item,
                    drop_tier=4,
                    relation=field_relation,
                    confidence=focal.confidence,
                    depth=1,
                    search_text=f"{label} {value}",
                    entity_type=EntityType.TASK,
                    task_priority=(None if resolved_task is None else resolved_task.priority),
                )
            )
    return _dedupe_entries(entries)


def _people_entries(
    reader: ProjectionReader,
    focal: DocumentRecord,
    traversal: RelatedResult,
    observations: dict[str, ObservationRecord],
) -> list[_Entry]:
    section = PackSectionName.PEOPLE_AND_INTERACTIONS
    entries: list[_Entry] = []
    relation_by_reference = _neighbor_relations(traversal)
    person_references: set[str] = set()
    for node in traversal.nodes:
        if _entity_type(node.record) is not EntityType.PERSON or node.record is None:
            continue
        person_references.add(node.reference)
        traversed = relation_by_reference.get(node.reference)
        entries.append(
            _entry_for_record(
                section,
                _record_item(node.record),
                node.record,
                drop_tier=2,
                relation=None if traversed is None else traversed.edge.relation,
                source_observation=_first_edge_observation(traversed, observations),
                depth=node.depth,
            )
        )

    if isinstance(focal, TaskRecord):
        structured_people: list[tuple[str, RelationType]] = []
        if focal.owner is not None:
            structured_people.append((focal.owner, RelationType.OWNED_BY))
        if focal.requester is not None:
            structured_people.append((focal.requester, RelationType.REQUESTED_BY))
        structured_people.extend((value, RelationType.WAITING_ON) for value in focal.waiting_on)
        for value, relation in structured_people:
            people = _resolve_people(reader, value)
            if people:
                for person in people:
                    person_references.add(str(person.uri))
                    entries.append(
                        _entry_for_record(
                            section,
                            _record_item(person),
                            person,
                            drop_tier=2,
                            relation=relation,
                            depth=1,
                        )
                    )
            else:
                item = PackItem(
                    id=f"interaction:{_digest(value)}",
                    kind=PackItemKind.INTERACTION,
                    uri=None,
                    title="Relevant person or waiting-on value",
                    summary=sanitize_text(value),
                    data=_sanitized_mapping({"value": value}),
                    rank=None,
                )
                entries.append(
                    _make_entry(
                        section,
                        item,
                        drop_tier=2,
                        relation=relation,
                        depth=1,
                        search_text=value,
                        entity_type=EntityType.PERSON,
                    )
                )

    for traversed in traversal.edges:
        neighbor = _neighbor_reference(traversed)
        if neighbor not in person_references:
            continue
        for observation_reference in traversed.edge.source_observations:
            observation = observations.get(observation_reference)
            if observation is None:
                continue
            item = PackItem(
                id=f"interaction:{_digest(f'{neighbor}|{observation_reference}')}",
                kind=PackItemKind.INTERACTION,
                uri=neighbor,
                title="Latest authored interaction evidence",
                summary=sanitize_text(observation.statement),
                data=_sanitized_mapping(
                    {
                        "relation": traversed.edge.relation.value,
                        "observation_uri": observation_reference,
                        "observed_at": _iso(observation.observed_at),
                        "source_ref": str(observation.source_ref),
                        "locator": observation.locator.model_dump(mode="json"),
                    }
                ),
                rank=None,
            )
            entries.append(
                _make_entry(
                    section,
                    item,
                    drop_tier=2,
                    relation=traversed.edge.relation,
                    timestamp=observation.observed_at,
                    confidence=observation.confidence,
                    depth=traversed.depth,
                    search_text=observation.statement,
                    entity_type=EntityType.PERSON,
                    observation_kind=observation.kind,
                    source_locator=observation.locator,
                )
            )
    return _dedupe_entries(entries)


def _decision_risk_question_entries(
    traversal: RelatedResult,
    observations: dict[str, ObservationRecord],
) -> list[_Entry]:
    section = PackSectionName.DECISIONS_RISKS_AND_QUESTIONS
    relations = _neighbor_relations(traversal)
    entries: list[_Entry] = []
    for node in traversal.nodes:
        entity_type = _entity_type(node.record)
        if node.record is None or entity_type not in _DECISION_RISK_QUESTION_TYPES:
            continue
        traversed = relations.get(node.reference)
        entries.append(
            _entry_for_record(
                section,
                _record_item(node.record),
                node.record,
                drop_tier=3,
                relation=None if traversed is None else traversed.edge.relation,
                source_observation=_first_edge_observation(traversed, observations),
                depth=node.depth,
            )
        )
    return _dedupe_entries(entries)


def _contradictory_entries(
    traversal: RelatedResult,
    claims: tuple[ClaimRecord, ...],
    observations: dict[str, ObservationRecord],
) -> list[_Entry]:
    section = PackSectionName.CONTRADICTORY_OR_SUPERSEDING_EVIDENCE
    entries = [
        _edge_entry(
            section,
            traversed,
            _neighbor_record(traversal, traversed),
            observations,
            drop_tier=6,
        )
        for traversed in traversal.edges
        if traversed.edge.relation in {RelationType.CONTRADICTS, RelationType.SUPERSEDES}
    ]
    for claim in claims:
        if claim.status not in _HISTORICAL_STATUSES:
            continue
        source_observation = _first_claim_observation(claim, observations)
        item = PackItem(
            id=f"historical:{claim.id}",
            kind=PackItemKind.STATUS_HISTORY,
            uri=str(claim.uri),
            title=f"{claim.status.value.capitalize()} claim",
            summary="Historical claim metadata; full content requires history.",
            data=_sanitized_mapping(
                {
                    "claim_id": claim.id,
                    "status": claim.status.value,
                    "observed_at": claim.observed_at.isoformat(),
                    "supersedes": claim.supersedes,
                    "superseded_by": claim.superseded_by,
                    "source_observations": [str(uri) for uri in claim.source_observations],
                }
            ),
            rank=None,
        )
        entries.append(
            _make_entry(
                section,
                item,
                drop_tier=6,
                relation=RelationType.SUPERSEDES,
                timestamp=(
                    source_observation.observed_at if source_observation is not None else None
                ),
                claim_status=claim.status,
                confidence=claim.confidence,
                depth=1,
                search_text=f"{claim.predicate} {claim.status.value}",
                entity_type=EntityType.CLAIM,
                observation_kind=(
                    source_observation.kind if source_observation is not None else None
                ),
                source_locator=(
                    source_observation.locator if source_observation is not None else None
                ),
            )
        )
    return _dedupe_entries(entries)


def _architecture_entries(
    traversal: RelatedResult,
    observations: dict[str, ObservationRecord],
) -> list[_Entry]:
    section = PackSectionName.ARCHITECTURE_ENTITIES
    relations = _neighbor_relations(traversal)
    entries: list[_Entry] = []
    for node in traversal.nodes:
        entity_type = _entity_type(node.record)
        if node.record is None or entity_type not in _ARCHITECTURE_TYPES:
            continue
        traversed = relations.get(node.reference)
        entries.append(
            _entry_for_record(
                section,
                _record_item(node.record),
                node.record,
                drop_tier=0,
                relation=None if traversed is None else traversed.edge.relation,
                source_observation=_first_edge_observation(traversed, observations),
                depth=node.depth,
            )
        )
    return _dedupe_entries(entries)


def _edge_entry(
    section: PackSectionName,
    traversed: TraversedEdge,
    neighbor_record: DocumentRecord | None,
    observations: dict[str, ObservationRecord],
    *,
    drop_tier: int,
) -> _Entry:
    edge = traversed.edge
    neighbor = _neighbor_reference(traversed)
    item = PackItem(
        id=f"edge:{_digest(_edge_identity(edge))}",
        kind=PackItemKind.RELATIONSHIP,
        uri=neighbor,
        title=edge.relation.value.replace("_", " ").title(),
        summary=sanitize_text(
            edge.note or f"{edge.source_uri} {edge.relation.value} {edge.target}"
        ),
        data=_sanitized_mapping(
            {
                "direction": traversed.direction.value,
                "source": str(edge.source_uri),
                "relation": edge.relation.value,
                "target": edge.target,
                "confidence": (None if edge.confidence is None else edge.confidence.value),
                "source_observations": list(edge.source_observations),
                "valid_from": _iso(edge.valid_from),
                "valid_to": _iso(edge.valid_to),
                "source_path": edge.source_path,
                "ordinal": edge.ordinal,
            }
        ),
        rank=None,
    )
    source_observation = _first_edge_observation(traversed, observations)
    return _make_entry(
        section,
        item,
        drop_tier=drop_tier,
        relation=edge.relation,
        timestamp=(source_observation.observed_at if source_observation is not None else None),
        confidence=edge.confidence,
        depth=traversed.depth,
        search_text=f"{item.title} {item.summary}",
        entity_type=_entity_type(neighbor_record),
        task_priority=(
            neighbor_record.priority if isinstance(neighbor_record, TaskRecord) else None
        ),
        observation_kind=(source_observation.kind if source_observation is not None else None),
        source_locator=(source_observation.locator if source_observation is not None else None),
    )


def _entry_for_record(
    section: PackSectionName,
    item: PackItem,
    record: DocumentRecord,
    *,
    drop_tier: int,
    relation: RelationType | None = None,
    source_observation: ObservationRecord | None = None,
    depth: int,
) -> _Entry:
    return _make_entry(
        section,
        item,
        drop_tier=drop_tier,
        relation=relation,
        timestamp=(
            record.observed_at
            if isinstance(record, ObservationRecord)
            else source_observation.observed_at
            if source_observation is not None
            else None
        ),
        claim_status=record.status if isinstance(record, ClaimRecord) else None,
        confidence=_record_confidence(record),
        depth=depth,
        search_text=_record_search_text(record),
        entity_type=_entity_type(record),
        task_priority=record.priority if isinstance(record, TaskRecord) else None,
        observation_kind=(
            record.kind
            if isinstance(record, ObservationRecord)
            else source_observation.kind
            if source_observation is not None
            else None
        ),
        source_locator=(
            record.locator
            if isinstance(record, ObservationRecord)
            else source_observation.locator
            if source_observation is not None
            else None
        ),
    )


def _make_entry(
    section: PackSectionName,
    item: PackItem,
    *,
    drop_tier: int,
    relation: RelationType | None = None,
    timestamp: datetime | None = None,
    claim_status: ClaimStatus | None = None,
    confidence: Confidence | None = None,
    depth: int,
    search_text: str,
    entity_type: EntityType | None = None,
    task_priority: TaskPriority | None = None,
    observation_kind: ObservationKind | None = None,
    source_locator: SourceLocator | None = None,
) -> _Entry:
    key = f"{section.value}\x1f{item.id}"
    return _Entry(
        section=section,
        item=item,
        ranking=RankingCandidate(
            key=key,
            relation=relation,
            timestamp=timestamp,
            claim_status=claim_status,
            confidence=confidence,
            depth=depth,
            search_text=sanitize_text(search_text),
            entity_type=entity_type,
            task_priority=task_priority,
            observation_kind=observation_kind,
            source_locator=source_locator,
        ),
        drop_tier=drop_tier,
    )


def _rank_and_group(
    entries: Iterable[_Entry],
    query: str | None,
) -> dict[PackSectionName, list[_RankedEntry]]:
    deduped: dict[str, _Entry] = {}
    for entry in entries:
        deduped.setdefault(entry.ranking.key, entry)
    ranked = rank(
        (entry.ranking for entry in deduped.values()),
        query=query,
    )
    score_by_key = {
        item.candidate.key: (rank_order, item) for rank_order, item in enumerate(ranked)
    }
    grouped: dict[PackSectionName, list[_RankedEntry]] = {name: [] for name in PackSectionName}
    for key, entry in deduped.items():
        rank_order, scored = score_by_key[key]
        item = entry.item.model_copy(update={"rank": _rank_metadata(scored)})
        grouped[entry.section].append(
            _RankedEntry(
                section=entry.section,
                item=item,
                score=scored.total_score,
                rank_order=rank_order,
                drop_tier=entry.drop_tier,
            )
        )
    for section_entries in grouped.values():
        section_entries.sort(key=lambda entry: entry.rank_order)
    return grouped


def _apply_budget(
    entries: dict[PackSectionName, list[_RankedEntry]],
    *,
    full_focal: PackItem,
    minimal_focal: PackItem,
    requested_units: int,
) -> ContextPackSections:
    retained = {section: list(values) for section, values in entries.items()}
    costs = {
        (entry.section, entry.item.id): estimate_pack_item_units(entry.item)
        for values in retained.values()
        for entry in values
    }
    focal = full_focal
    focal_cost = estimate_pack_item_units(focal)
    minimum_units = estimate_pack_item_units(minimal_focal)
    used_units = focal_cost + sum(costs.values())
    omitted: list[OmittedItem] = []
    omitted_by_section = {section: 0 for section in PackSectionName}

    removal_order = sorted(
        (entry for values in retained.values() for entry in values),
        key=lambda entry: (entry.drop_tier, -entry.rank_order, entry.item.id),
    )
    for entry in removal_order:
        if used_units <= requested_units:
            break
        retained[entry.section].remove(entry)
        item_units = costs[(entry.section, entry.item.id)]
        used_units -= item_units
        omitted_by_section[entry.section] += 1
        omitted.append(
            OmittedItem(
                section=entry.section,
                item_id=entry.item.id,
                units=item_units,
                reason="budget_priority",
            )
        )

    if used_units > requested_units and focal != minimal_focal:
        saved_units = focal_cost - minimum_units
        focal = minimal_focal
        used_units -= saved_units
        omitted_by_section[PackSectionName.FOCAL_ENTITY] += 1
        omitted.append(
            OmittedItem(
                section=PackSectionName.FOCAL_ENTITY,
                item_id=full_focal.id,
                units=saved_units,
                reason="focal_details_compacted",
            )
        )

    budget = BudgetAndTruncation(
        requested_units=requested_units,
        used_units=used_units,
        minimum_units=minimum_units,
        unit=_BUDGET_UNIT,
        truncated=bool(omitted),
        within_budget=used_units <= requested_units,
        over_budget_by=max(0, used_units - requested_units),
        omitted_count=len(omitted),
        omitted_items=omitted,
    )
    return ContextPackSections(
        focal_entity=PackSection(
            items=[focal],
            omitted_count=omitted_by_section[PackSectionName.FOCAL_ENTITY],
        ),
        claims_and_status_history=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.CLAIMS_AND_STATUS_HISTORY,
        ),
        direct_relationships=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.DIRECT_RELATIONSHIPS,
        ),
        source_observations=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.SOURCE_OBSERVATIONS,
        ),
        related_tasks_and_dependencies=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.RELATED_TASKS_AND_DEPENDENCIES,
        ),
        people_and_interactions=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.PEOPLE_AND_INTERACTIONS,
        ),
        decisions_risks_and_questions=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.DECISIONS_RISKS_AND_QUESTIONS,
        ),
        contradictory_or_superseding_evidence=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.CONTRADICTORY_OR_SUPERSEDING_EVIDENCE,
        ),
        architecture_entities=_pack_section(
            retained,
            omitted_by_section,
            PackSectionName.ARCHITECTURE_ENTITIES,
        ),
        budget_and_truncation=budget,
    )


def _pack_section(
    retained: dict[PackSectionName, list[_RankedEntry]],
    omitted: dict[PackSectionName, int],
    section: PackSectionName,
) -> PackSection:
    return PackSection(
        items=[entry.item for entry in retained[section]],
        omitted_count=omitted[section],
    )


def _rank_metadata(scored: RankedCandidate) -> RankMetadata:
    factors = scored.factors
    return RankMetadata(
        relation_semantics=factors.relation_semantics,
        recency=factors.recency,
        current_state=factors.claim_state,
        confidence=factors.confidence,
        directness=factors.directness,
        query_match=factors.query_match,
        entity_importance=factors.entity_importance,
        source_quality=factors.source_quality,
        total=scored.total_score,
    )


def _record_item(record: DocumentRecord, *, minimal: bool = False) -> PackItem:
    if isinstance(record, TaskRecord):
        data: dict[str, object] = {
            "entity_type": record.entity_type.value,
            "status": record.status.value,
        }
        if not minimal:
            data.update(
                {
                    "aliases": list(record.aliases),
                    "tags": list(record.tags),
                    "confidence": (None if record.confidence is None else record.confidence.value),
                    "task_type": record.task_type.value,
                    "parent_task": record.parent_task,
                    "root_task": record.root_task,
                    "priority": record.priority.value,
                    "owner": record.owner,
                    "requester": record.requester,
                    "waiting_on": list(record.waiting_on),
                    "due_at": _iso(record.due_at),
                    "next_action": record.next_action,
                    "dependencies": list(record.dependencies),
                    "blockers": list(record.blockers),
                    "source_observations": list(record.source_observations),
                    "source_path": record.source_path,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
            )
        return PackItem(
            id=record.id,
            kind=PackItemKind.TASK,
            uri=str(record.uri),
            title=sanitize_text(record.title),
            summary="" if minimal else sanitize_text(record.body.strip()),
            data=_sanitized_mapping(data),
            rank=None,
        )
    if isinstance(record, EntityRecord):
        data = {
            "entity_type": record.entity_type.value,
            "status": record.status,
        }
        if not minimal:
            data.update(
                {
                    "aliases": list(record.aliases),
                    "tags": list(record.tags),
                    "confidence": (None if record.confidence is None else record.confidence.value),
                    "source_path": record.source_path,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
            )
        return PackItem(
            id=record.id,
            kind=PackItemKind(record.entity_type.value),
            uri=str(record.uri),
            title=sanitize_text(record.title),
            summary="" if minimal else sanitize_text(record.body.strip()),
            data=_sanitized_mapping(data),
            rank=None,
        )
    if isinstance(record, ClaimRecord):
        object_summary = _short_json(record.object)
        data = {"entity_type": EntityType.CLAIM.value, "status": record.status.value}
        if not minimal:
            data.update(
                {
                    "subject": str(record.subject),
                    "predicate": record.predicate,
                    "object": sanitize_named_value(record.predicate, record.object),
                    "observed_at": record.observed_at.isoformat(),
                    "valid_from": _iso(record.valid_from),
                    "valid_to": _iso(record.valid_to),
                    "supersedes": record.supersedes,
                    "superseded_by": record.superseded_by,
                    "confidence": record.confidence.value,
                    "source_observations": [str(uri) for uri in record.source_observations],
                    "source_path": record.source_path,
                }
            )
        return PackItem(
            id=record.id,
            kind=PackItemKind.CLAIM,
            uri=str(record.uri),
            title=sanitize_text(f"{record.predicate}: {object_summary}"),
            summary="" if minimal else sanitize_text(record.body.strip()),
            data=_sanitized_mapping(data),
            rank=None,
        )
    data = {"entity_type": EntityType.OBSERVATION.value, "kind": record.kind.value}
    if not minimal:
        data.update(
            {
                "confidence": record.confidence.value,
                "parent_entity_uri": (
                    None if record.parent_entity_uri is None else str(record.parent_entity_uri)
                ),
                "source_ref": str(record.source_ref),
                "locator": record.locator.model_dump(mode="json"),
                "observed_at": _iso(record.observed_at),
                "valid_from": _iso(record.valid_from),
                "valid_to": _iso(record.valid_to),
                "derived_from": list(record.derived_from),
                "source_path": record.source_path,
            }
        )
    return PackItem(
        id=record.id,
        kind=PackItemKind.OBSERVATION,
        uri=str(record.uri),
        title=sanitize_text(record.statement),
        summary="" if minimal else sanitize_text(record.body.strip()),
        data=_sanitized_mapping(data),
        rank=None,
    )


def _observation_item(traced: TracedObservation) -> PackItem:
    observation = traced.observation
    return PackItem(
        id=observation.id,
        kind=PackItemKind.OBSERVATION,
        uri=str(observation.uri),
        title=sanitize_text(observation.statement),
        summary=sanitize_text(observation.body.strip()),
        data=_sanitized_mapping(
            {
                "kind": observation.kind.value,
                "confidence": observation.confidence.value,
                "source_ref": str(observation.source_ref),
                "locator": observation.locator.model_dump(mode="json"),
                "observed_at": _iso(observation.observed_at),
                "valid_from": _iso(observation.valid_from),
                "valid_to": _iso(observation.valid_to),
                "derived_from": list(observation.derived_from),
                "referenced_by": list(traced.referenced_by),
                "source_path": observation.source_path,
            }
        ),
        rank=None,
    )


def _record_confidence(record: DocumentRecord) -> Confidence | None:
    return record.confidence


def _record_search_text(record: DocumentRecord) -> str:
    if isinstance(record, (EntityRecord, TaskRecord)):
        return f"{record.title} {record.body}"
    if isinstance(record, ClaimRecord):
        return f"{record.predicate} {_short_json(record.object)} {record.body}"
    return f"{record.statement} {record.body}"


def _entity_type(record: DocumentRecord | None) -> EntityType | None:
    if record is None:
        return None
    if isinstance(record, ClaimRecord):
        return EntityType.CLAIM
    if isinstance(record, ObservationRecord):
        return EntityType.OBSERVATION
    return record.entity_type


def _neighbor_reference(traversed: TraversedEdge) -> str:
    if traversed.direction.value == "outbound":
        return traversed.edge.target
    return str(traversed.edge.source_uri)


def _neighbor_record(
    traversal: RelatedResult,
    traversed: TraversedEdge,
) -> DocumentRecord | None:
    neighbor = _neighbor_reference(traversed)
    return next(
        (node.record for node in traversal.nodes if node.reference == neighbor),
        None,
    )


def _neighbor_relations(traversal: RelatedResult) -> dict[str, TraversedEdge]:
    result: dict[str, TraversedEdge] = {}
    for traversed in traversal.edges:
        result.setdefault(_neighbor_reference(traversed), traversed)
    return result


def _first_edge_observation(
    traversed: TraversedEdge | None,
    observations: dict[str, ObservationRecord],
) -> ObservationRecord | None:
    if traversed is None:
        return None
    for reference in traversed.edge.source_observations:
        observation = observations.get(reference)
        if observation is not None:
            return observation
    return None


def _first_claim_observation(
    claim: ClaimRecord,
    observations: dict[str, ObservationRecord],
) -> ObservationRecord | None:
    for reference in claim.source_observations:
        observation = observations.get(str(reference))
        if observation is not None:
            return observation
    return None


def _missing_observation_uri(
    reference: str,
    reason: MissingObservationReason,
) -> str | None:
    if reason is not MissingObservationReason.NOT_FOUND:
        return None
    try:
        return validate_durable_reference(reference)
    except ValueError:
        return None


def _resolve_task(reader: ProjectionReader, value: str) -> TaskRecord | None:
    if value.startswith("workctx://"):
        resolution = resolve(reader, value)
        return resolution.record if isinstance(resolution.record, TaskRecord) else None
    try:
        return reader.get_task(value)
    except ValueError:
        return None


def _resolve_people(reader: ProjectionReader, value: str) -> tuple[EntityRecord, ...]:
    if value.startswith("workctx://"):
        resolution = resolve(reader, value)
        record = resolution.record
        if isinstance(record, EntityRecord) and record.entity_type is EntityType.PERSON:
            return (record,)
        return ()
    return tuple(
        record
        for record in reader.find_entities_by_alias(value)
        if record.entity_type is EntityType.PERSON
    )


def _dedupe_entries(entries: Iterable[_Entry]) -> list[_Entry]:
    unique: dict[str, _Entry] = {}
    for entry in entries:
        unique.setdefault(entry.item.id, entry)
    return [unique[key] for key in sorted(unique)]


def _sanitized_mapping(value: dict[str, object]) -> dict[str, JsonValue]:
    sanitized = sanitize_json(cast(JsonValue, value))
    if not isinstance(sanitized, dict):
        raise AssertionError("Expected sanitized mapping")
    return sanitized


def _short_json(value: JsonValue, limit: int = 160) -> str:
    sanitized = sanitize_json(value)
    rendered = json.dumps(
        sanitized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 3]}..."


def _strongest_confidence(values: Iterable[Confidence]) -> Confidence | None:
    order = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
    return max(values, key=order.__getitem__, default=None)


def _edge_identity(edge: EdgeRecord) -> str:
    return "|".join(
        (
            str(edge.source_uri),
            edge.relation.value,
            edge.target,
            edge.source_path,
            str(edge.ordinal),
        )
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _same_projection(left: ProjectionMetadata, right: ProjectionMetadata) -> bool:
    return (
        left.context_id == right.context_id
        and left.context_updated_at == right.context_updated_at
        and left.source_fingerprint == right.source_fingerprint
        and left.build_completed_at == right.build_completed_at
    )


__all__ = [
    "DEFAULT_BUDGET_UNITS",
    "PackBuildResult",
    "PackBuildStatus",
    "ProjectionChangedError",
    "build_pack",
    "estimate_pack_item_units",
]
