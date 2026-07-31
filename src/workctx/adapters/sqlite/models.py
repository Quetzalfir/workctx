"""Typed records returned by the SQLite projection adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue

from workctx.domain import (
    ArtifactReference,
    ClaimStatus,
    Confidence,
    EntityType,
    ObservationKind,
    RelationType,
    SourceLocator,
    TaskPriority,
    TaskStatus,
    TaskType,
    WorkctxUri,
)
from workctx.errors import ContextBoundaryError, UnavailableDependencyError, WorkctxError


class ProjectionError(WorkctxError):
    """Base error for projection build and query failures."""


class ContextIsolationError(ContextBoundaryError, ProjectionError):
    """Raised when a projection operation would cross its context boundary."""


class Fts5UnavailableError(UnavailableDependencyError, ProjectionError):
    """Raised when the Python SQLite build does not provide FTS5."""


class ProjectionBuildError(ProjectionError):
    """Raised when a complete replacement projection cannot be built or installed."""


class ProjectionQueryError(ProjectionError):
    """Raised when a typed projection query cannot be completed."""


class RebuildTrigger(StrEnum):
    EXPLICIT = "explicit"
    MISSING = "missing"
    VERSION_MISMATCH = "version_mismatch"
    WORKSPACE_VERSION_MISMATCH = "workspace_version_mismatch"
    CONTEXT_MISMATCH = "context_mismatch"
    INCOMPATIBLE_DATABASE = "incompatible_database"


class SkipReason(StrEnum):
    READ_ERROR = "read_error"
    PATH_ESCAPE = "path_escape"
    FRONTMATTER_ERROR = "frontmatter_error"
    UNSUPPORTED_DOCUMENT = "unsupported_document"
    VALIDATION_ERROR = "validation_error"
    CONTEXT_MISMATCH = "context_mismatch"
    DUPLICATE_IDENTITY = "duplicate_identity"
    TASK_HIERARCHY = "task_hierarchy"


class SearchRecordKind(StrEnum):
    ENTITY = "entity"
    CLAIM = "claim"
    OBSERVATION = "observation"


@dataclass(frozen=True, slots=True)
class ProjectionMetadata:
    projection_schema_version: int
    workspace_schema_version: int
    context_id: str
    context_updated_at: datetime
    source_fingerprint: str
    source_file_count: int
    indexed_document_count: int
    skipped_document_count: int
    build_started_at: datetime
    build_completed_at: datetime


@dataclass(frozen=True, slots=True)
class RebuildCounts:
    documents_seen: int
    documents_indexed: int
    documents_skipped: int
    entities: int
    aliases: int
    edges: int
    backlinks: int
    observations: int
    claims: int
    tasks: int
    fts_records: int


@dataclass(frozen=True, slots=True)
class SkippedDocument:
    path: str
    reason: SkipReason
    message: str


@dataclass(frozen=True, slots=True)
class RebuildReport:
    trigger: RebuildTrigger
    metadata: ProjectionMetadata
    counts: RebuildCounts
    skipped_documents: tuple[SkippedDocument, ...]


@dataclass(frozen=True, slots=True)
class EntityRecord:
    context_id: str
    id: str
    entity_type: EntityType
    uri: WorkctxUri
    title: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    status: str | None
    confidence: Confidence | None
    body: str
    source_path: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EdgeRecord:
    source_uri: WorkctxUri
    relation: RelationType
    target: str
    confidence: Confidence | None
    source_observations: tuple[str, ...]
    valid_from: datetime | None
    valid_to: datetime | None
    note: str | None
    source_path: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    context_id: str
    id: str
    uri: WorkctxUri
    parent_entity_uri: WorkctxUri | None
    kind: ObservationKind
    statement: str
    confidence: Confidence
    source_ref: ArtifactReference
    locator: SourceLocator
    observed_at: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    derived_from: tuple[str, ...]
    body: str
    source_path: str


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    context_id: str
    id: str
    uri: WorkctxUri
    subject: WorkctxUri
    predicate: str
    object: JsonValue
    observed_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    status: ClaimStatus
    supersedes: str | None
    superseded_by: str | None
    confidence: Confidence
    source_observations: tuple[WorkctxUri, ...]
    body: str
    source_path: str


@dataclass(frozen=True, slots=True)
class TaskRecord:
    context_id: str
    id: str
    uri: WorkctxUri
    entity_type: EntityType
    title: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    confidence: Confidence | None
    task_type: TaskType
    parent_task: str | None
    root_task: str
    priority: TaskPriority
    status: TaskStatus
    owner: str | None
    requester: str | None
    waiting_on: tuple[str, ...]
    due_at: datetime | None
    next_action: str
    dependencies: tuple[str, ...]
    blockers: tuple[str, ...]
    source_observations: tuple[str, ...]
    body: str
    source_path: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TaskQuery:
    statuses: frozenset[TaskStatus] | None = None
    owner: str | None = None
    waiting_on: str | None = None
    root_task: str | None = None
    parent_task: str | None = None


@dataclass(frozen=True, slots=True)
class SearchHit:
    id: str
    uri: WorkctxUri
    record_kind: SearchRecordKind
    entity_type: EntityType
    title: str
    source_path: str
    score: float
