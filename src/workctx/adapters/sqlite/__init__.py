"""Context-isolated SQLite and FTS projections."""

from workctx.adapters.sqlite.models import (
    ClaimRecord,
    ContextIsolationError,
    EdgeRecord,
    EntityRecord,
    Fts5UnavailableError,
    ObservationRecord,
    ProjectionBuildError,
    ProjectionError,
    ProjectionMetadata,
    ProjectionQueryError,
    RebuildCounts,
    RebuildReport,
    RebuildTrigger,
    SearchHit,
    SearchRecordKind,
    SkippedDocument,
    SkipReason,
    TaskQuery,
    TaskRecord,
)
from workctx.adapters.sqlite.projection import SQLiteProjection
from workctx.adapters.sqlite.schema import PROJECTION_SCHEMA_VERSION

__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "ClaimRecord",
    "ContextIsolationError",
    "EdgeRecord",
    "EntityRecord",
    "Fts5UnavailableError",
    "ObservationRecord",
    "ProjectionBuildError",
    "ProjectionError",
    "ProjectionMetadata",
    "ProjectionQueryError",
    "RebuildCounts",
    "RebuildReport",
    "RebuildTrigger",
    "SQLiteProjection",
    "SearchHit",
    "SearchRecordKind",
    "SkipReason",
    "SkippedDocument",
    "TaskQuery",
    "TaskRecord",
]
