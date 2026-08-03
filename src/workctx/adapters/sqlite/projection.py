"""Context-bound SQLite projection build and typed query APIs."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import JsonValue, ValidationError

from workctx.adapters.sqlite.models import (
    ClaimRecord,
    ContextIsolationError,
    EdgeRecord,
    EntityRecord,
    Fts5UnavailableError,
    ObservationRecord,
    ProjectionBuildError,
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
from workctx.adapters.sqlite.schema import (
    PROJECTION_SCHEMA_VERSION,
    create_schema,
    schema_is_compatible,
)
from workctx.domain import (
    ArtifactReference,
    Claim,
    ClaimId,
    ClaimStatus,
    Confidence,
    EntityFrontmatter,
    EntityType,
    Observation,
    ObservationId,
    ObservationKind,
    RelationType,
    Task,
    TaskHierarchyError,
    TaskPriority,
    TaskStatus,
    TaskType,
    TypedReference,
    WorkctxUri,
    parse_durable_reference,
    parse_source_locator,
    validate_task_hierarchy,
)
from workctx.domain.frontmatter import parse_frontmatter
from workctx.models.context import ContextConfig
from workctx.services.contexts import load_context_config

_DATABASE_NAME = "index.sqlite3"
_INDEXED_ZONES = ("02_knowledge", "03_work")


def projection_database_path(context_root: Path) -> Path:
    """Location of the live projection database for a context root.

    Lead integration helper: lets presentation code check for a built
    projection without constructing (and thereby auto-rebuilding) the adapter.
    """

    return context_root / "98_state" / _DATABASE_NAME


_REPLACE_ATTEMPTS = 100
_REPLACE_RETRY_SECONDS = 0.01
_LIVE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


class _ReaderSwapGate:
    """Keep adapter-managed readers closed during the atomic filename swap."""

    def __init__(self) -> None:
        self.writer_lock = threading.Lock()
        self._condition = threading.Condition()
        self._active_readers = 0
        self._swapping = False

    @contextmanager
    def reader(self) -> Iterator[None]:
        with self._condition:
            while self._swapping:
                self._condition.wait()
            self._active_readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._active_readers -= 1
                if self._active_readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def swap(self) -> Iterator[None]:
        with self._condition:
            self._swapping = True
            while self._active_readers:
                self._condition.wait()
        try:
            yield
        finally:
            with self._condition:
                self._swapping = False
                self._condition.notify_all()


_GATES_LOCK = threading.Lock()
_GATES: dict[Path, _ReaderSwapGate] = {}


def _gate_for(database_path: Path) -> _ReaderSwapGate:
    with _GATES_LOCK:
        return _GATES.setdefault(database_path, _ReaderSwapGate())


@dataclass(frozen=True, slots=True)
class _SourceFile:
    path: Path
    relative_path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class _ParsedDocument:
    source_path: str
    body: str
    entity: EntityFrontmatter | None = None
    task: Task | None = None
    claim: Claim | None = None
    standalone_observation: Observation | None = None
    observations: tuple[Observation, ...] = ()
    references: tuple[TypedReference, ...] = ()


@dataclass(slots=True)
class _BuildCounter:
    entities: int = 0
    aliases: int = 0
    edges: int = 0
    observations: int = 0
    claims: int = 0
    tasks: int = 0
    fts_records: int = 0


@dataclass(slots=True)
class _LockedOperationState:
    preflight_rebuild_seen: bool = False
    preflight_current: bool = False
    preflight_report: RebuildReport | None = None
    reader: sqlite3.Connection | None = None
    reader_context: AbstractContextManager[sqlite3.Connection] | None = None


class _UnsupportedDocument(ValueError):
    pass


class SQLiteProjection:
    """A rebuildable, context-isolated projection over canonical documents."""

    def __init__(self, context_root: Path) -> None:
        try:
            root = context_root.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectionBuildError("Context root does not exist or cannot be resolved") from exc
        if not root.is_dir():
            raise ProjectionBuildError("Context root must be a directory")

        context_path = root / "context.yaml"
        self._require_contained_existing_path(context_path, root, "context configuration")
        state_path = root / "98_state"
        resolved_state = self._require_contained_existing_path(
            state_path, root, "projection state directory"
        )
        if state_path.is_symlink() or resolved_state != state_path:
            raise ContextIsolationError("Projection state directory cannot be a symbolic link")
        if not resolved_state.is_dir():
            raise ProjectionBuildError("Projection state path must be a directory")

        try:
            config = load_context_config(root)
        except Exception as exc:
            raise ProjectionBuildError("Context configuration is missing or invalid") from exc

        self._context_root = root
        self._context_id = config.id
        self._state_path = resolved_state
        self._database_path = resolved_state / _DATABASE_NAME
        self._gate = _gate_for(self._database_path)
        self._locked_operation: _LockedOperationState | None = None
        self._verify_database_target()

    @property
    def context_root(self) -> Path:
        return self._context_root

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def database_path(self) -> Path:
        return self._database_path

    def ensure_ready(self) -> RebuildReport | None:
        """Build when absent or fully rebuild when projection metadata is incompatible."""

        if self._locked_preflight_is_current():
            return None
        config = self._load_bound_config()
        trigger = self._readiness_trigger(config)
        if trigger is None:
            return None
        with self._gate.writer_lock:
            config = self._load_bound_config()
            trigger = self._readiness_trigger(config)
            if trigger is None:
                return None
            return self._rebuild_locked(trigger, config)

    def rebuild(self) -> RebuildReport:
        """Build a complete temporary projection and atomically replace the live database."""

        with self._gate.writer_lock:
            return self._rebuild_locked(RebuildTrigger.EXPLICIT, self._load_bound_config())

    def readiness_trigger(self) -> RebuildTrigger | None:
        """Report why a rebuild would run, without rebuilding.

        Lead integration addition for read-only consumers (validation freshness
        probes) that must never mutate derived state. ``None`` means the live
        projection is present and compatible.
        """

        if self._locked_preflight_is_current():
            return None
        return self._readiness_trigger(self._load_bound_config())

    def _begin_locked_operation(self) -> None:
        if self._locked_operation is not None:
            raise RuntimeError("Projection operation state is already active")
        self._locked_operation = _LockedOperationState()

    def _end_locked_operation(self) -> None:
        self._close_operation_reader()
        self._locked_operation = None

    def _locked_preflight_is_current(self) -> bool:
        return self._locked_operation is not None and self._locked_operation.preflight_current

    def invalidate(self) -> None:
        """Durably mark the live projection stale without rebuilding.

        Lead integration addition for WP-300: after a canonical commit whose
        projection refresh failed, the engine marks derived state stale so the
        next ``ensure_ready`` performs a full rebuild and ``readiness_trigger``
        reports the pending rebuild. Removing the database is the ADR 0007
        rebuild-not-migrate path; sidecars are cleaned with it.
        """

        if self._locked_operation is not None:
            self._close_operation_reader()
            self._locked_operation.preflight_current = False
        with self._gate.writer_lock:
            for suffix in ("", *_LIVE_SIDECAR_SUFFIXES):
                candidate = self._database_path.with_name(self._database_path.name + suffix)
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    continue

    def metadata(self) -> ProjectionMetadata:
        self.ensure_ready()
        metadata = self._read_metadata()
        if metadata is None:
            raise ProjectionQueryError("Projection metadata is unavailable after rebuild")
        return metadata

    def get_entity_by_id(self, entity_id: str) -> EntityRecord | None:
        if not entity_id:
            raise ValueError("entity_id must not be empty")
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM entities
                    WHERE context_id = ? AND id = ?
                    """,
                    (self._context_id, entity_id),
                ).fetchone()
                return None if row is None else self._entity_record(connection, row)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Entity lookup failed") from exc

    def get_entity_by_uri(self, uri: str | WorkctxUri) -> EntityRecord | None:
        parsed = self._coerce_local_uri(uri)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM entities
                    WHERE context_id = ? AND uri = ?
                    """,
                    (self._context_id, str(parsed)),
                ).fetchone()
                return None if row is None else self._entity_record(connection, row)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Entity lookup failed") from exc

    def find_entities_by_alias(self, alias: str) -> tuple[EntityRecord, ...]:
        if not alias:
            raise ValueError("alias must not be empty")
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                rows = connection.execute(
                    """
                    SELECT entities.*
                    FROM aliases
                    JOIN entities
                      ON entities.context_id = aliases.context_id
                     AND entities.id = aliases.entity_id
                    WHERE aliases.context_id = ? AND aliases.alias = ?
                    ORDER BY entities.id
                    """,
                    (self._context_id, alias),
                ).fetchall()
                return tuple(self._entity_record(connection, row) for row in rows)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Alias lookup failed") from exc

    def get_document_by_uri(
        self, uri: str | WorkctxUri
    ) -> EntityRecord | TaskRecord | ClaimRecord | ObservationRecord | None:
        parsed = self._coerce_local_uri(uri)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                if parsed.entity_type == EntityType.TASK.value:
                    row = connection.execute(
                        """
                        SELECT tasks.*, entities.uri, entities.title, entities.confidence,
                               entities.body, entities.source_path,
                               entities.created_at, entities.updated_at
                        FROM tasks
                        JOIN entities
                          ON entities.context_id = tasks.context_id
                         AND entities.id = tasks.id
                        WHERE tasks.context_id = ? AND tasks.id = ?
                        """,
                        (self._context_id, parsed.entity_id),
                    ).fetchone()
                    if row is not None:
                        return self._task_record(connection, row)
                elif parsed.entity_type == EntityType.CLAIM.value:
                    try:
                        ClaimId.parse(parsed.entity_id)
                    except ValueError:
                        pass
                    else:
                        row = connection.execute(
                            "SELECT * FROM claims WHERE context_id = ? AND id = ?",
                            (self._context_id, parsed.entity_id),
                        ).fetchone()
                        if row is not None:
                            return self._claim_record(connection, row)
                elif parsed.entity_type == EntityType.OBSERVATION.value:
                    try:
                        ObservationId.parse(parsed.entity_id)
                    except ValueError:
                        pass
                    else:
                        row = connection.execute(
                            "SELECT * FROM observations WHERE context_id = ? AND id = ?",
                            (self._context_id, parsed.entity_id),
                        ).fetchone()
                        if row is not None:
                            return self._observation_record(connection, row)

                row = connection.execute(
                    "SELECT * FROM entities WHERE context_id = ? AND uri = ?",
                    (self._context_id, str(parsed)),
                ).fetchone()
                return None if row is None else self._entity_record(connection, row)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Document lookup failed") from exc

    def outbound_edges(
        self,
        source: str | WorkctxUri,
        *,
        relations: frozenset[RelationType] | None = None,
    ) -> tuple[EdgeRecord, ...]:
        source_uri = self._coerce_local_uri(source)
        return self._query_edges("source_uri", str(source_uri), relations)

    def inbound_edges(
        self,
        target: str | WorkctxUri,
        *,
        relations: frozenset[RelationType] | None = None,
    ) -> tuple[EdgeRecord, ...]:
        target_value = self._coerce_durable_target(target)
        return self._query_edges("target_uri", target_value, relations, inbound=True)

    def get_observation(self, observation: str | WorkctxUri) -> ObservationRecord | None:
        observation_id = self._local_identifier(
            observation, EntityType.OBSERVATION, ObservationId.parse
        )
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM observations
                    WHERE context_id = ? AND id = ?
                    """,
                    (self._context_id, observation_id),
                ).fetchone()
                return None if row is None else self._observation_record(connection, row)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Observation lookup failed") from exc

    def observations_for_parent(self, parent: str | WorkctxUri) -> tuple[ObservationRecord, ...]:
        parent_uri = self._coerce_local_uri(parent)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM observations
                    WHERE context_id = ? AND parent_entity_uri = ?
                    ORDER BY id
                    """,
                    (self._context_id, str(parent_uri)),
                ).fetchall()
                return tuple(self._observation_record(connection, row) for row in rows)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Observation parent query failed") from exc

    def get_claim(self, claim: str | WorkctxUri) -> ClaimRecord | None:
        claim_id = self._local_identifier(claim, EntityType.CLAIM, ClaimId.parse)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM claims
                    WHERE context_id = ? AND id = ?
                    """,
                    (self._context_id, claim_id),
                ).fetchone()
                return None if row is None else self._claim_record(connection, row)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Claim lookup failed") from exc

    def claims_for_subject(
        self,
        subject: str | WorkctxUri,
        *,
        statuses: frozenset[ClaimStatus] | None = None,
    ) -> tuple[ClaimRecord, ...]:
        subject_uri = self._coerce_local_uri(subject)
        if statuses is not None and not statuses:
            return ()
        parameters: list[object] = [self._context_id, str(subject_uri)]
        status_clause = ""
        if statuses is not None:
            values = sorted(status.value for status in statuses)
            status_clause = f" AND status IN ({_placeholders(len(values))})"
            parameters.extend(values)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM claims
                    WHERE context_id = ? AND subject_uri = ?{status_clause}
                    ORDER BY observed_at DESC, id
                    """,
                    parameters,
                ).fetchall()
                return tuple(self._claim_record(connection, row) for row in rows)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Claim query failed") from exc

    def get_task(self, task: str | WorkctxUri) -> TaskRecord | None:
        task_id = self._local_identifier(task, EntityType.TASK)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                row = connection.execute(
                    """
                    SELECT tasks.*, entities.uri, entities.title, entities.confidence,
                           entities.body,
                           entities.source_path, entities.created_at, entities.updated_at
                    FROM tasks
                    JOIN entities
                      ON entities.context_id = tasks.context_id
                     AND entities.id = tasks.id
                    WHERE tasks.context_id = ? AND tasks.id = ?
                    """,
                    (self._context_id, task_id),
                ).fetchone()
                return None if row is None else self._task_record(connection, row)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Task lookup failed") from exc

    def query_tasks(self, query: TaskQuery | None = None) -> tuple[TaskRecord, ...]:
        query = TaskQuery() if query is None else query
        for structured_value in (query.owner, query.waiting_on):
            if structured_value is not None and structured_value.startswith("workctx://"):
                self._coerce_local_uri(structured_value)
        if query.statuses is not None and not query.statuses:
            return ()
        clauses = ["tasks.context_id = ?"]
        parameters: list[object] = [self._context_id]
        if query.statuses is not None:
            values = sorted(status.value for status in query.statuses)
            clauses.append(f"tasks.status IN ({_placeholders(len(values))})")
            parameters.extend(values)
        if query.owner is not None:
            clauses.append("tasks.owner = ?")
            parameters.append(query.owner)
        if query.root_task is not None:
            clauses.append("tasks.root_task = ?")
            parameters.append(query.root_task)
        if query.parent_task is not None:
            clauses.append("tasks.parent_task = ?")
            parameters.append(query.parent_task)
        if query.waiting_on is not None:
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM task_waiting_on
                    WHERE task_waiting_on.context_id = tasks.context_id
                      AND task_waiting_on.task_id = tasks.id
                      AND task_waiting_on.value = ?
                )
                """
            )
            parameters.append(query.waiting_on)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT tasks.*, entities.uri, entities.title, entities.confidence,
                           entities.body,
                           entities.source_path, entities.created_at, entities.updated_at
                    FROM tasks
                    JOIN entities
                      ON entities.context_id = tasks.context_id
                     AND entities.id = tasks.id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY tasks.id
                    """,
                    parameters,
                ).fetchall()
                return tuple(self._task_record(connection, row) for row in rows)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Task query failed") from exc

    def search(
        self,
        query: str,
        *,
        entity_types: frozenset[EntityType] | None = None,
        limit: int = 20,
    ) -> tuple[SearchHit, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        match_query = _literal_fts_query(query)
        if entity_types is not None and not entity_types:
            return ()
        parameters: list[object] = [match_query, self._context_id]
        type_clause = ""
        if entity_types is not None:
            values = sorted(entity_type.value for entity_type in entity_types)
            type_clause = f" AND documents.entity_type IN ({_placeholders(len(values))})"
            parameters.extend(values)
        parameters.append(limit)
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT
                        documents.record_id,
                        documents.uri,
                        documents.record_kind,
                        documents.entity_type,
                        documents.title,
                        documents.source_path,
                        bm25(search_fts, 8.0, 2.0, 5.0) AS rank
                    FROM search_fts
                    JOIN search_documents AS documents
                      ON documents.rowid = search_fts.rowid
                    WHERE search_fts MATCH ?
                      AND documents.context_id = ?{type_clause}
                    ORDER BY rank, documents.record_kind, documents.record_id
                    LIMIT ?
                    """,
                    parameters,
                ).fetchall()
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Full-text search failed") from exc
        return tuple(
            SearchHit(
                id=cast(str, row["record_id"]),
                uri=WorkctxUri.parse(cast(str, row["uri"])),
                record_kind=SearchRecordKind(cast(str, row["record_kind"])),
                entity_type=EntityType(cast(str, row["entity_type"])),
                title=cast(str, row["title"]),
                source_path=cast(str, row["source_path"]),
                score=-float(row["rank"]),
            )
            for row in rows
        )

    def _rebuild_locked(self, trigger: RebuildTrigger, config: ContextConfig) -> RebuildReport:
        operation = self._locked_operation
        preflight_rebuild = operation is not None and not operation.preflight_rebuild_seen
        if preflight_rebuild:
            assert operation is not None
            operation.preflight_rebuild_seen = True
        elif operation is not None:
            self._close_operation_reader()
            if self._preflight_projection_still_current():
                if operation.preflight_report is None:  # pragma: no cover - state invariant
                    raise AssertionError("Projection preflight report is unavailable")
                return operation.preflight_report

        file_descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            self._verify_context_configuration()
            self._verify_state_directory()
            started_at = datetime.now(UTC)
            sources, scan_skips = self._collect_sources()
            documents, parse_skips = self._parse_sources(sources)
            skipped = [*scan_skips, *parse_skips]
            fingerprint = self._source_fingerprint(sources, scan_skips)

            self._verify_state_directory()
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f"{_DATABASE_NAME}.", suffix=".tmp", dir=self._state_path
            )
            temporary_path = Path(temporary_name)
            created_stat = os.fstat(file_descriptor)
            resolved_temporary = temporary_path.resolve(strict=True)
            if resolved_temporary.parent != self._state_path:
                os.close(file_descriptor)
                file_descriptor = None
                _remove_escaped_temporary_database(resolved_temporary, created_stat)
                temporary_path = None
                raise ContextIsolationError(
                    "Temporary projection database resolves outside the state directory"
                )
            os.close(file_descriptor)
            file_descriptor = None
            metadata, counts = self._build_temporary_database(
                temporary_path,
                config,
                documents,
                skipped,
                len(sources) + len(scan_skips),
                fingerprint,
                started_at,
            )
            with temporary_path.open("r+b") as database_file:
                os.fsync(database_file.fileno())
            with self._gate.swap():
                self._load_bound_config()
                self._verify_database_target()
                self._quiesce_live_database_for_swap()
                _replace_with_retry(temporary_path, self._database_path)
        except Fts5UnavailableError:
            raise
        except ProjectionBuildError:
            raise
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            raise ProjectionBuildError(
                "Projection rebuild failed; the prior database is intact"
            ) from exc
        finally:
            if file_descriptor is not None:
                with suppress(OSError):
                    os.close(file_descriptor)
            if temporary_path is not None:
                _remove_temporary_database(temporary_path, self._state_path)

        report = RebuildReport(
            trigger=trigger,
            metadata=metadata,
            counts=counts,
            skipped_documents=tuple(sorted(skipped, key=lambda item: (item.path, item.reason))),
        )
        if preflight_rebuild:
            if operation is None:  # pragma: no cover - preflight invariant
                raise AssertionError("Projection operation state is unavailable")
            operation.preflight_current = True
            operation.preflight_report = report
        return report

    def _preflight_projection_still_current(self) -> bool:
        operation = self._locked_operation
        if operation is None or operation.preflight_report is None:
            return False
        try:
            sources, scan_skips = self._collect_sources()
            return (
                not scan_skips
                and self._source_fingerprint(
                    sources,
                    scan_skips,
                )
                == operation.preflight_report.metadata.source_fingerprint
            )
        except (OSError, RuntimeError, ValueError):
            return False

    def _build_temporary_database(
        self,
        database_path: Path,
        config: ContextConfig,
        documents: Sequence[_ParsedDocument],
        skipped: Sequence[SkippedDocument],
        documents_seen: int,
        source_fingerprint: str,
        started_at: datetime,
    ) -> tuple[ProjectionMetadata, RebuildCounts]:
        counter = _BuildCounter()
        connection = sqlite3.connect(database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                create_schema(connection)
            except sqlite3.OperationalError as exc:
                if "fts5" in str(exc).lower():
                    raise Fts5UnavailableError(
                        "The active Python SQLite build does not provide required FTS5 support"
                    ) from exc
                raise

            connection.execute(
                """
                INSERT INTO projection_metadata (
                    singleton,
                    projection_schema_version,
                    workspace_schema_version,
                    context_id,
                    context_updated_at,
                    source_fingerprint,
                    source_file_count,
                    indexed_document_count,
                    skipped_document_count,
                    build_started_at,
                    build_completed_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    PROJECTION_SCHEMA_VERSION,
                    config.schema_version,
                    config.id,
                    _datetime_text(config.updated_at),
                    source_fingerprint,
                    documents_seen,
                    len(documents),
                    len(skipped),
                    _datetime_text(started_at),
                    _datetime_text(started_at),
                ),
            )
            for document in documents:
                self._insert_document(connection, document, counter)

            connection.execute("INSERT INTO search_fts(search_fts) VALUES ('rebuild')")
            connection.execute(
                "INSERT INTO search_fts(search_fts, rank) VALUES ('integrity-check', 1)"
            )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ProjectionBuildError("Temporary projection failed its integrity check")

            completed_at = datetime.now(UTC)
            connection.execute(
                """
                UPDATE projection_metadata
                SET build_completed_at = ?
                WHERE singleton = 1
                """,
                (_datetime_text(completed_at),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        metadata = ProjectionMetadata(
            projection_schema_version=PROJECTION_SCHEMA_VERSION,
            workspace_schema_version=config.schema_version,
            context_id=config.id,
            context_updated_at=config.updated_at.astimezone(UTC),
            source_fingerprint=source_fingerprint,
            source_file_count=documents_seen,
            indexed_document_count=len(documents),
            skipped_document_count=len(skipped),
            build_started_at=started_at,
            build_completed_at=completed_at,
        )
        counts = RebuildCounts(
            documents_seen=documents_seen,
            documents_indexed=len(documents),
            documents_skipped=len(skipped),
            entities=counter.entities,
            aliases=counter.aliases,
            edges=counter.edges,
            backlinks=counter.edges,
            observations=counter.observations,
            claims=counter.claims,
            tasks=counter.tasks,
            fts_records=counter.fts_records,
        )
        return metadata, counts

    def _insert_document(
        self,
        connection: sqlite3.Connection,
        document: _ParsedDocument,
        counter: _BuildCounter,
    ) -> None:
        if document.entity is not None:
            entity = document.entity
            connection.execute(
                """
                INSERT INTO entities (
                    context_id, id, entity_type, uri, title, status, confidence,
                    body, source_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._context_id,
                    entity.id,
                    entity.entity_type,
                    entity.uri,
                    entity.title,
                    _enum_text(entity.status),
                    _enum_text(entity.confidence),
                    document.body,
                    document.source_path,
                    _datetime_text(entity.created_at),
                    _datetime_text(entity.updated_at),
                ),
            )
            counter.entities += 1
            for position, alias in enumerate(entity.aliases):
                connection.execute(
                    """
                    INSERT INTO aliases(context_id, entity_id, position, alias)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self._context_id, entity.id, position, alias),
                )
                counter.aliases += 1
            for position, tag in enumerate(entity.tags):
                connection.execute(
                    """
                    INSERT INTO entity_tags(context_id, entity_id, position, tag)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self._context_id, entity.id, position, tag),
                )
            for ordinal, reference in enumerate(document.references):
                self._insert_edge(
                    connection,
                    entity.uri,
                    reference,
                    document.source_path,
                    ordinal,
                )
                counter.edges += 1
            self._insert_search_document(
                connection,
                SearchRecordKind.ENTITY,
                entity.id,
                EntityType(entity.entity_type),
                entity.uri,
                entity.title,
                document.body,
                "",
                document.source_path,
            )
            counter.fts_records += 1

        if document.task is not None:
            self._insert_task(connection, document.task)
            counter.tasks += 1
        if document.claim is not None:
            self._insert_claim(connection, document.claim, document.body, document.source_path)
            counter.claims += 1
            counter.fts_records += 1
        if document.standalone_observation is not None:
            self._insert_observation(
                connection,
                document.standalone_observation,
                None,
                document.body,
                document.source_path,
            )
            counter.observations += 1
            counter.edges += len(document.standalone_observation.related)
            counter.fts_records += 1
        for observation in document.observations:
            parent_uri = None if document.entity is None else document.entity.uri
            self._insert_observation(
                connection,
                observation,
                parent_uri,
                "",
                document.source_path,
            )
            counter.observations += 1
            counter.edges += len(observation.related)
            counter.fts_records += 1

    def _insert_edge(
        self,
        connection: sqlite3.Connection,
        source_uri: str,
        reference: TypedReference,
        source_path: str,
        ordinal: int,
    ) -> None:
        cursor = connection.execute(
            """
            INSERT INTO edges (
                context_id, source_uri, relation, target_uri, confidence,
                valid_from, valid_to, note, source_path, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._context_id,
                source_uri,
                reference.relation.value,
                reference.target,
                _enum_text(reference.confidence),
                _datetime_text(reference.valid_from),
                _datetime_text(reference.valid_to),
                reference.note,
                source_path,
                ordinal,
            ),
        )
        edge_id = cast(int, cursor.lastrowid)
        for position, observation_uri in enumerate(reference.source_observations):
            connection.execute(
                """
                INSERT INTO edge_source_observations(edge_id, position, observation_uri)
                VALUES (?, ?, ?)
                """,
                (edge_id, position, observation_uri),
            )

    def _insert_observation(
        self,
        connection: sqlite3.Connection,
        observation: Observation,
        parent_entity_uri: str | None,
        body: str,
        source_path: str,
    ) -> None:
        uri = str(WorkctxUri(self._context_id, EntityType.OBSERVATION, observation.id))
        locator = observation.source.locator
        locator_payload = locator.model_dump(mode="json")
        connection.execute(
            """
            INSERT INTO observations (
                context_id, id, uri, parent_entity_uri, kind, statement, confidence,
                source_ref, locator_type, locator_json, observed_at, valid_from, valid_to,
                body, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._context_id,
                observation.id,
                uri,
                parent_entity_uri,
                observation.kind.value,
                observation.statement,
                observation.confidence.value,
                observation.source.ref,
                locator.type,
                _json_text(locator_payload),
                _datetime_text(observation.observed_at),
                _datetime_text(observation.valid_from),
                _datetime_text(observation.valid_to),
                body,
                source_path,
            ),
        )
        for position, derived_reference in enumerate(observation.derived_from):
            connection.execute(
                """
                INSERT INTO observation_derivations (
                    context_id, observation_id, position, source_reference
                ) VALUES (?, ?, ?, ?)
                """,
                (self._context_id, observation.id, position, derived_reference),
            )
        for ordinal, related_reference in enumerate(observation.related):
            self._insert_edge(connection, uri, related_reference, source_path, ordinal)
        self._insert_search_document(
            connection,
            SearchRecordKind.OBSERVATION,
            observation.id,
            EntityType.OBSERVATION,
            uri,
            "",
            body,
            observation.statement,
            source_path,
        )

    def _insert_claim(
        self, connection: sqlite3.Connection, claim: Claim, body: str, source_path: str
    ) -> None:
        uri = str(WorkctxUri(self._context_id, EntityType.CLAIM, claim.id))
        object_json = _json_text(claim.object)
        connection.execute(
            """
            INSERT INTO claims (
                context_id, id, uri, subject_uri, predicate, object_json,
                observed_at, valid_from, valid_to, status, supersedes, superseded_by,
                confidence, body, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._context_id,
                claim.id,
                uri,
                claim.subject,
                claim.predicate,
                object_json,
                _datetime_text(claim.observed_at),
                _datetime_text(claim.valid_from),
                _datetime_text(claim.valid_to),
                claim.status.value,
                claim.supersedes,
                claim.superseded_by,
                claim.confidence.value,
                body,
                source_path,
            ),
        )
        for position, observation_uri in enumerate(claim.source_observations):
            connection.execute(
                """
                INSERT INTO claim_source_observations (
                    context_id, claim_id, position, observation_uri
                ) VALUES (?, ?, ?, ?)
                """,
                (self._context_id, claim.id, position, observation_uri),
            )
        self._insert_search_document(
            connection,
            SearchRecordKind.CLAIM,
            claim.id,
            EntityType.CLAIM,
            uri,
            "",
            body,
            f"{claim.predicate} {_json_search_text(claim.object)}",
            source_path,
        )

    def _insert_task(self, connection: sqlite3.Connection, task: Task) -> None:
        connection.execute(
            """
            INSERT INTO tasks (
                context_id, id, task_type, parent_task, root_task, priority, status,
                owner, requester, due_at, next_action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._context_id,
                task.id,
                task.task_type.value,
                task.parent_task,
                task.root_task,
                task.priority.value,
                task.status.value,
                task.owner,
                task.requester,
                _datetime_text(task.due_at),
                task.next_action,
            ),
        )
        self._insert_ordered_values(connection, "task_waiting_on", task.id, task.waiting_on)
        self._insert_ordered_values(connection, "task_dependencies", task.id, task.dependencies)
        self._insert_ordered_values(connection, "task_blockers", task.id, task.blockers)
        self._insert_ordered_values(
            connection,
            "task_source_observations",
            task.id,
            task.source_observations,
        )

    def _insert_ordered_values(
        self,
        connection: sqlite3.Connection,
        table: str,
        task_id: str,
        values: Sequence[str],
    ) -> None:
        for position, value in enumerate(values):
            connection.execute(
                f"""
                INSERT INTO {table}(context_id, task_id, position, value)
                VALUES (?, ?, ?, ?)
                """,
                (self._context_id, task_id, position, value),
            )

    def _insert_search_document(
        self,
        connection: sqlite3.Connection,
        record_kind: SearchRecordKind,
        record_id: str,
        entity_type: EntityType,
        uri: str,
        title: str,
        body: str,
        statement: str,
        source_path: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO search_documents (
                context_id, record_kind, record_id, entity_type, uri,
                title, body, statement, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._context_id,
                record_kind.value,
                record_id,
                entity_type.value,
                uri,
                title,
                body,
                statement,
                source_path,
            ),
        )

    def _collect_sources(self) -> tuple[list[_SourceFile], list[SkippedDocument]]:
        candidates: list[tuple[Path, Path]] = []
        skipped: list[SkippedDocument] = []
        for zone_name in _INDEXED_ZONES:
            zone = self._context_root / zone_name
            if zone.is_symlink():
                raise ContextIsolationError("Canonical zones cannot be symbolic links")
            if not zone.exists():
                continue
            try:
                resolved_zone = zone.resolve(strict=True)
            except (OSError, RuntimeError):
                skipped.append(
                    SkippedDocument(
                        path=zone_name,
                        reason=SkipReason.PATH_ESCAPE,
                        message="Canonical zone cannot be safely resolved",
                    )
                )
                continue
            if not resolved_zone.is_relative_to(self._context_root):
                raise ContextIsolationError("Canonical zone resolves outside the context root")
            if resolved_zone != zone or not resolved_zone.is_dir():
                raise ProjectionBuildError("Canonical zone must be a directory")
            for path in zone.rglob("*"):
                if path.name.casefold() == "readme.md" or path.suffix.casefold() != ".md":
                    continue
                if path.is_file():
                    candidates.append((path, resolved_zone))

        sources: list[_SourceFile] = []
        for path, resolved_zone in sorted(
            candidates,
            key=lambda item: item[0].relative_to(self._context_root).as_posix(),
        ):
            relative_path = path.relative_to(self._context_root).as_posix()
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(resolved_zone):
                    skipped.append(
                        SkippedDocument(
                            path=relative_path,
                            reason=SkipReason.PATH_ESCAPE,
                            message="Canonical document resolves outside its indexed zone",
                        )
                    )
                    continue
                content = resolved.read_bytes()
            except (OSError, RuntimeError):
                skipped.append(
                    SkippedDocument(
                        path=relative_path,
                        reason=SkipReason.READ_ERROR,
                        message="Canonical document could not be read",
                    )
                )
                continue
            sources.append(_SourceFile(path=resolved, relative_path=relative_path, content=content))
        return sources, skipped

    def _parse_sources(
        self, sources: Sequence[_SourceFile]
    ) -> tuple[list[_ParsedDocument], list[SkippedDocument]]:
        parsed: list[_ParsedDocument] = []
        skipped: list[SkippedDocument] = []
        seen_ids: set[str] = set()
        seen_uris: set[str] = set()
        for source in sources:
            try:
                text = source.content.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(
                    SkippedDocument(
                        path=source.relative_path,
                        reason=SkipReason.READ_ERROR,
                        message="Canonical document is not valid UTF-8",
                    )
                )
                continue
            try:
                raw, body = parse_frontmatter(text)
            except yaml.YAMLError as exc:
                skipped.append(
                    SkippedDocument(
                        path=source.relative_path,
                        reason=SkipReason.FRONTMATTER_ERROR,
                        message=_yaml_diagnostic(exc),
                    )
                )
                continue
            except ValueError:
                skipped.append(
                    SkippedDocument(
                        path=source.relative_path,
                        reason=SkipReason.FRONTMATTER_ERROR,
                        message="Canonical Markdown frontmatter delimiters are invalid",
                    )
                )
                continue
            try:
                document = self._parse_document(raw, body, source.relative_path)
                identities = self._document_identities(document)
                if any(
                    identifier in seen_ids or uri in seen_uris for identifier, uri in identities
                ):
                    skipped.append(
                        SkippedDocument(
                            path=source.relative_path,
                            reason=SkipReason.DUPLICATE_IDENTITY,
                            message="Canonical ID or URI duplicates an earlier document",
                        )
                    )
                    continue
                seen_ids.update(identifier for identifier, _ in identities)
                seen_uris.update(uri for _, uri in identities)
                parsed.append(document)
            except ValidationError as exc:
                skipped.append(
                    SkippedDocument(
                        path=source.relative_path,
                        reason=SkipReason.VALIDATION_ERROR,
                        message=_validation_diagnostic(exc),
                    )
                )
            except ContextIsolationError:
                skipped.append(
                    SkippedDocument(
                        path=source.relative_path,
                        reason=SkipReason.CONTEXT_MISMATCH,
                        message="Structured reference belongs to another context",
                    )
                )
            except _UnsupportedDocument:
                skipped.append(
                    SkippedDocument(
                        path=source.relative_path,
                        reason=SkipReason.UNSUPPORTED_DOCUMENT,
                        message="Frontmatter does not match an integrated canonical model",
                    )
                )
            except ValueError:
                skipped.append(
                    SkippedDocument(
                        path=source.relative_path,
                        reason=SkipReason.VALIDATION_ERROR,
                        message="Canonical document failed semantic validation",
                    )
                )

        non_tasks = [document for document in parsed if document.task is None]
        task_documents = sorted(
            (document for document in parsed if document.task is not None),
            key=lambda document: (
                document.task is None or document.task.task_type.value != "parent",
                "" if document.task is None else document.task.id,
                document.source_path,
            ),
        )
        accepted_tasks: list[_ParsedDocument] = []
        accepted_parents: dict[str, Task] = {}
        for document in task_documents:
            task = document.task
            if task is None:
                continue
            try:
                if task.task_type.value == "parent":
                    validate_task_hierarchy([task])
                else:
                    parent = accepted_parents.get(task.root_task)
                    validate_task_hierarchy([task] if parent is None else [parent, task])
            except TaskHierarchyError:
                skipped.append(
                    SkippedDocument(
                        path=document.source_path,
                        reason=SkipReason.TASK_HIERARCHY,
                        message="Task hierarchy is incomplete or inconsistent",
                    )
                )
                continue
            if task.task_type.value == "parent":
                accepted_parents[task.id] = task
            accepted_tasks.append(document)
        accepted = sorted([*non_tasks, *accepted_tasks], key=lambda item: item.source_path)
        return accepted, skipped

    def _parse_document(self, raw: dict[str, Any], body: str, source_path: str) -> _ParsedDocument:
        entity_type = raw.get("entity_type")
        if entity_type is not None:
            if entity_type == EntityType.TASK:
                task = Task.model_validate(raw)
                entity: EntityFrontmatter = task
                self._validate_task_context(task)
            else:
                entity = EntityFrontmatter.model_validate(raw)
                task = None
            self._require_local_uri(entity.uri)
            references = self._parse_entity_references(raw)
            observations = self._parse_embedded_observations(raw, entity)
            return _ParsedDocument(
                source_path=source_path,
                body=body,
                entity=entity,
                task=task,
                observations=observations,
                references=references,
            )

        identifier = raw.get("id")
        if isinstance(identifier, str) and identifier.startswith("CLM-"):
            claim = Claim.model_validate(raw)
            self._validate_claim_context(claim)
            return _ParsedDocument(
                source_path=source_path,
                body=body,
                claim=claim,
            )
        if isinstance(identifier, str) and "#OBS-" in identifier:
            observation = Observation.model_validate(raw)
            self._validate_observation_context(observation)
            return _ParsedDocument(
                source_path=source_path,
                body=body,
                standalone_observation=observation,
            )
        raise _UnsupportedDocument

    def _parse_entity_references(self, raw: dict[str, Any]) -> tuple[TypedReference, ...]:
        raw_references = raw.get("references", [])
        if not isinstance(raw_references, list):
            raise ValueError("references must be a list")
        references = tuple(TypedReference.model_validate(item) for item in raw_references)
        for reference in references:
            self._require_reference_context(reference.target)
            for observation_uri in reference.source_observations:
                self._require_local_uri(observation_uri, EntityType.OBSERVATION)
        return references

    def _parse_embedded_observations(
        self, raw: dict[str, Any], entity: EntityFrontmatter
    ) -> tuple[Observation, ...]:
        raw_observations = raw.get("observations", [])
        if not isinstance(raw_observations, list):
            raise ValueError("observations must be a list")
        observations = tuple(Observation.model_validate(item) for item in raw_observations)
        for observation in observations:
            self._validate_observation_context(observation)
            if entity.entity_type == EntityType.EVIDENCE.value and not observation.id.startswith(
                f"{entity.id}#OBS-"
            ):
                raise ValueError("embedded observation does not belong to its evidence note")
        return observations

    def _validate_task_context(self, task: Task) -> None:
        structured_values = [task.owner, task.requester]
        structured_values.extend(task.waiting_on)
        structured_values.extend(task.dependencies)
        structured_values.extend(task.blockers)
        structured_values.extend(task.source_observations)
        for value in structured_values:
            if value is not None and value.startswith("workctx://"):
                self._require_local_uri(value)

    def _validate_claim_context(self, claim: Claim) -> None:
        self._require_local_uri(claim.subject)
        for observation_uri in claim.source_observations:
            self._require_local_uri(observation_uri, EntityType.OBSERVATION)

    def _validate_observation_context(self, observation: Observation) -> None:
        for reference in observation.derived_from:
            self._require_reference_context(reference)
        for related in observation.related:
            self._require_reference_context(related.target)
            for observation_uri in related.source_observations:
                self._require_local_uri(observation_uri, EntityType.OBSERVATION)

    def _document_identities(self, document: _ParsedDocument) -> tuple[tuple[str, str], ...]:
        identities: list[tuple[str, str]] = []
        if document.entity is not None:
            identities.append((document.entity.id, document.entity.uri))
        if document.claim is not None:
            identities.append(
                (
                    document.claim.id,
                    str(WorkctxUri(self._context_id, EntityType.CLAIM, document.claim.id)),
                )
            )
        if document.standalone_observation is not None:
            observation = document.standalone_observation
            identities.append(
                (
                    observation.id,
                    str(WorkctxUri(self._context_id, EntityType.OBSERVATION, observation.id)),
                )
            )
        identities.extend(
            (
                observation.id,
                str(WorkctxUri(self._context_id, EntityType.OBSERVATION, observation.id)),
            )
            for observation in document.observations
        )
        if len({identifier for identifier, _ in identities}) != len(identities):
            raise ValueError("document contains duplicate record IDs")
        return tuple(identities)

    def _source_fingerprint(
        self,
        sources: Sequence[_SourceFile],
        scan_skips: Sequence[SkippedDocument],
    ) -> str:
        digest = hashlib.sha256()
        context_path = self._verify_context_configuration()
        try:
            context_content = context_path.read_bytes()
        except OSError as exc:
            raise ProjectionBuildError("Context configuration could not be read") from exc
        _update_fingerprint(digest, "context.yaml", context_content)
        for source in sources:
            _update_fingerprint(digest, source.relative_path, source.content)
        for skipped in sorted(scan_skips, key=lambda item: (item.path, item.reason)):
            _update_fingerprint(
                digest,
                skipped.path,
                f"unreadable:{skipped.reason.value}".encode("ascii"),
            )
        return digest.hexdigest()

    def _readiness_trigger(self, config: ContextConfig) -> RebuildTrigger | None:
        self._verify_database_target()
        if not self._database_path.exists():
            return RebuildTrigger.MISSING
        if not self._database_path.is_file():
            raise ProjectionBuildError("Projection database path is not a regular file")
        try:
            with self._reader_connection() as connection:
                user_version = cast(int, connection.execute("PRAGMA user_version").fetchone()[0])
                row = connection.execute(
                    "SELECT * FROM projection_metadata WHERE singleton = 1"
                ).fetchone()
                compatible_schema = schema_is_compatible(connection)
        except sqlite3.Error:
            return RebuildTrigger.INCOMPATIBLE_DATABASE
        if row is None or not compatible_schema:
            return RebuildTrigger.INCOMPATIBLE_DATABASE
        try:
            projection_version = int(row["projection_schema_version"])
            workspace_version = int(row["workspace_schema_version"])
            context_id = cast(str, row["context_id"])
        except (IndexError, KeyError, TypeError, ValueError):
            return RebuildTrigger.INCOMPATIBLE_DATABASE
        if context_id != self._context_id:
            return RebuildTrigger.CONTEXT_MISMATCH
        if (
            projection_version != PROJECTION_SCHEMA_VERSION
            or user_version != PROJECTION_SCHEMA_VERSION
        ):
            return RebuildTrigger.VERSION_MISMATCH
        if workspace_version != config.schema_version:
            return RebuildTrigger.WORKSPACE_VERSION_MISMATCH
        return None

    def _read_metadata(self) -> ProjectionMetadata | None:
        try:
            with self._reader_connection() as connection:
                row = connection.execute(
                    "SELECT * FROM projection_metadata WHERE singleton = 1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Projection metadata lookup failed") from exc
        return None if row is None else _metadata_record(row)

    def _query_edges(
        self,
        column: str,
        value: str,
        relations: frozenset[RelationType] | None,
        *,
        inbound: bool = False,
    ) -> tuple[EdgeRecord, ...]:
        if relations is not None and not relations:
            return ()
        if column not in {"source_uri", "target_uri"}:
            raise AssertionError("Unexpected edge query column")
        parameters: list[object] = [self._context_id, value]
        relation_clause = ""
        if relations is not None:
            values = sorted(relation.value for relation in relations)
            relation_clause = f" AND relation IN ({_placeholders(len(values))})"
            parameters.extend(values)
        source = "backlinks" if inbound else "edges"
        self.ensure_ready()
        try:
            with self._reader_connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM {source}
                    WHERE context_id = ? AND {column} = ?{relation_clause}
                    ORDER BY relation, source_uri, target_uri, ordinal
                    """,
                    parameters,
                ).fetchall()
                return tuple(self._edge_record(connection, row) for row in rows)
        except sqlite3.Error as exc:
            raise ProjectionQueryError("Typed edge query failed") from exc

    def _entity_record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> EntityRecord:
        aliases = tuple(
            cast(str, item[0])
            for item in connection.execute(
                """
                SELECT alias FROM aliases
                WHERE context_id = ? AND entity_id = ?
                ORDER BY position
                """,
                (self._context_id, row["id"]),
            ).fetchall()
        )
        tags = tuple(
            cast(str, item[0])
            for item in connection.execute(
                """
                SELECT tag FROM entity_tags
                WHERE context_id = ? AND entity_id = ?
                ORDER BY position
                """,
                (self._context_id, row["id"]),
            ).fetchall()
        )
        confidence = row["confidence"]
        return EntityRecord(
            context_id=cast(str, row["context_id"]),
            id=cast(str, row["id"]),
            entity_type=EntityType(cast(str, row["entity_type"])),
            uri=WorkctxUri.parse(cast(str, row["uri"])),
            title=cast(str, row["title"]),
            aliases=aliases,
            tags=tags,
            status=cast(str | None, row["status"]),
            confidence=None if confidence is None else Confidence(cast(str, confidence)),
            body=cast(str, row["body"]),
            source_path=cast(str, row["source_path"]),
            created_at=_parse_datetime(cast(str, row["created_at"])),
            updated_at=_parse_datetime(cast(str, row["updated_at"])),
        )

    def _edge_record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> EdgeRecord:
        source_observations = tuple(
            cast(str, item[0])
            for item in connection.execute(
                """
                SELECT observation_uri FROM edge_source_observations
                WHERE edge_id = ?
                ORDER BY position
                """,
                (row["edge_id"],),
            ).fetchall()
        )
        confidence = row["confidence"]
        return EdgeRecord(
            source_uri=WorkctxUri.parse(cast(str, row["source_uri"])),
            relation=RelationType(cast(str, row["relation"])),
            target=cast(str, row["target_uri"]),
            confidence=None if confidence is None else Confidence(cast(str, confidence)),
            source_observations=source_observations,
            valid_from=_parse_optional_datetime(row["valid_from"]),
            valid_to=_parse_optional_datetime(row["valid_to"]),
            note=cast(str | None, row["note"]),
            source_path=cast(str, row["source_path"]),
            ordinal=cast(int, row["ordinal"]),
        )

    def _observation_record(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> ObservationRecord:
        derived_from = tuple(
            cast(str, item[0])
            for item in connection.execute(
                """
                SELECT source_reference FROM observation_derivations
                WHERE context_id = ? AND observation_id = ?
                ORDER BY position
                """,
                (self._context_id, row["id"]),
            ).fetchall()
        )
        parent_uri = row["parent_entity_uri"]
        locator_payload = json.loads(cast(str, row["locator_json"]))
        return ObservationRecord(
            context_id=cast(str, row["context_id"]),
            id=cast(str, row["id"]),
            uri=WorkctxUri.parse(cast(str, row["uri"])),
            parent_entity_uri=(
                None if parent_uri is None else WorkctxUri.parse(cast(str, parent_uri))
            ),
            kind=ObservationKind(cast(str, row["kind"])),
            statement=cast(str, row["statement"]),
            confidence=Confidence(cast(str, row["confidence"])),
            source_ref=ArtifactReference.parse(cast(str, row["source_ref"])),
            locator=parse_source_locator(locator_payload),
            observed_at=_parse_optional_datetime(row["observed_at"]),
            valid_from=_parse_optional_datetime(row["valid_from"]),
            valid_to=_parse_optional_datetime(row["valid_to"]),
            derived_from=derived_from,
            body=cast(str, row["body"]),
            source_path=cast(str, row["source_path"]),
        )

    def _claim_record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> ClaimRecord:
        source_observations = tuple(
            WorkctxUri.parse(cast(str, item[0]))
            for item in connection.execute(
                """
                SELECT observation_uri FROM claim_source_observations
                WHERE context_id = ? AND claim_id = ?
                ORDER BY position
                """,
                (self._context_id, row["id"]),
            ).fetchall()
        )
        return ClaimRecord(
            context_id=cast(str, row["context_id"]),
            id=cast(str, row["id"]),
            uri=WorkctxUri.parse(cast(str, row["uri"])),
            subject=WorkctxUri.parse(cast(str, row["subject_uri"])),
            predicate=cast(str, row["predicate"]),
            object=cast(JsonValue, json.loads(cast(str, row["object_json"]))),
            observed_at=_parse_datetime(cast(str, row["observed_at"])),
            valid_from=_parse_optional_datetime(row["valid_from"]),
            valid_to=_parse_optional_datetime(row["valid_to"]),
            status=ClaimStatus(cast(str, row["status"])),
            supersedes=cast(str | None, row["supersedes"]),
            superseded_by=cast(str | None, row["superseded_by"]),
            confidence=Confidence(cast(str, row["confidence"])),
            source_observations=source_observations,
            body=cast(str, row["body"]),
            source_path=cast(str, row["source_path"]),
        )

    def _task_record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> TaskRecord:
        aliases = tuple(
            cast(str, item[0])
            for item in connection.execute(
                """
                SELECT alias FROM aliases
                WHERE context_id = ? AND entity_id = ?
                ORDER BY position
                """,
                (self._context_id, row["id"]),
            ).fetchall()
        )
        tags = tuple(
            cast(str, item[0])
            for item in connection.execute(
                """
                SELECT tag FROM entity_tags
                WHERE context_id = ? AND entity_id = ?
                ORDER BY position
                """,
                (self._context_id, row["id"]),
            ).fetchall()
        )
        confidence = row["confidence"]
        return TaskRecord(
            context_id=cast(str, row["context_id"]),
            id=cast(str, row["id"]),
            uri=WorkctxUri.parse(cast(str, row["uri"])),
            entity_type=EntityType.TASK,
            title=cast(str, row["title"]),
            aliases=aliases,
            tags=tags,
            confidence=None if confidence is None else Confidence(cast(str, confidence)),
            task_type=TaskType(cast(str, row["task_type"])),
            parent_task=cast(str | None, row["parent_task"]),
            root_task=cast(str, row["root_task"]),
            priority=TaskPriority(cast(str, row["priority"])),
            status=TaskStatus(cast(str, row["status"])),
            owner=cast(str | None, row["owner"]),
            requester=cast(str | None, row["requester"]),
            waiting_on=self._task_values(connection, "task_waiting_on", row["id"]),
            due_at=_parse_optional_datetime(row["due_at"]),
            next_action=cast(str, row["next_action"]),
            dependencies=self._task_values(connection, "task_dependencies", row["id"]),
            blockers=self._task_values(connection, "task_blockers", row["id"]),
            source_observations=self._task_values(
                connection, "task_source_observations", row["id"]
            ),
            body=cast(str, row["body"]),
            source_path=cast(str, row["source_path"]),
            created_at=_parse_datetime(cast(str, row["created_at"])),
            updated_at=_parse_datetime(cast(str, row["updated_at"])),
        )

    def _task_values(
        self, connection: sqlite3.Connection, table: str, task_id: object
    ) -> tuple[str, ...]:
        return tuple(
            cast(str, row[0])
            for row in connection.execute(
                f"""
                SELECT value FROM {table}
                WHERE context_id = ? AND task_id = ?
                ORDER BY position
                """,
                (self._context_id, task_id),
            ).fetchall()
        )

    def _operation_reader(self) -> sqlite3.Connection | None:
        if self._locked_operation is None:
            return None
        return self._locked_operation.reader

    def _open_operation_reader(self) -> None:
        operation = self._locked_operation
        if operation is None:
            raise RuntimeError("Projection operation state is not active")
        if operation.reader is not None or operation.reader_context is not None:
            raise RuntimeError("Projection operation reader is already active")
        reader_context = self._standalone_reader_connection()
        connection = reader_context.__enter__()
        operation.reader_context = reader_context
        operation.reader = connection

    def _close_operation_reader(self) -> None:
        operation = self._locked_operation
        if operation is None:
            return
        reader_context = operation.reader_context
        operation.reader = None
        operation.reader_context = None
        if reader_context is not None:
            reader_context.__exit__(None, None, None)

    @contextmanager
    def _reader_connection(self) -> Iterator[sqlite3.Connection]:
        operation_reader = self._operation_reader()
        if operation_reader is not None:
            yield operation_reader
            return
        if self._locked_preflight_is_current():
            self._open_operation_reader()
            operation_reader = self._operation_reader()
            if operation_reader is None:  # pragma: no cover - operation-state invariant
                raise AssertionError("Projection operation reader is unavailable")
            yield operation_reader
            return
        with self._standalone_reader_connection() as connection:
            yield connection

    @contextmanager
    def _standalone_reader_connection(self) -> Iterator[sqlite3.Connection]:
        with self._gate.reader():
            connection: sqlite3.Connection | None = None
            try:
                self._verify_database_target()
                connection = sqlite3.connect(
                    f"{self._database_path.as_uri()}?mode=ro&cache=private",
                    uri=True,
                    timeout=5.0,
                )
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only = ON")
                connection.execute("PRAGMA foreign_keys = ON")
                yield connection
            finally:
                if connection is not None:
                    connection.close()

    def _load_bound_config(self) -> ContextConfig:
        self._verify_context_configuration()
        try:
            config = load_context_config(self._context_root)
        except Exception as exc:
            raise ProjectionBuildError("Context configuration is missing or invalid") from exc
        if config.id != self._context_id:
            raise ContextIsolationError(
                "Context ID changed after the projection adapter was constructed"
            )
        return config

    def _verify_context_configuration(self) -> Path:
        context_path = self._context_root / "context.yaml"
        if self._locked_operation is None:
            resolved = self._require_contained_existing_path(
                context_path,
                self._context_root,
                "context configuration",
            )
            if not resolved.is_file():
                raise ProjectionBuildError("Context configuration must be a regular file")
            return resolved
        if context_path.is_symlink() or (
            hasattr(context_path, "is_junction") and context_path.is_junction()
        ):
            raise ContextIsolationError("Context configuration cannot be a link")
        if not context_path.is_file():
            raise ProjectionBuildError("Context configuration must be a regular file")
        return context_path

    def _verify_state_directory(self) -> None:
        state_path = self._context_root / "98_state"
        if self._locked_operation is None:
            if state_path.is_symlink():
                raise ContextIsolationError("Projection state directory cannot be a symbolic link")
            resolved = self._require_contained_existing_path(
                state_path,
                self._context_root,
                "projection state directory",
            )
            if resolved != self._state_path or resolved != state_path or not resolved.is_dir():
                raise ContextIsolationError("Projection state directory changed unexpectedly")
            return
        if state_path.is_symlink() or (
            hasattr(state_path, "is_junction") and state_path.is_junction()
        ):
            raise ContextIsolationError("Projection state directory cannot be a symbolic link")
        if state_path != self._state_path or not state_path.is_dir():
            raise ContextIsolationError("Projection state directory changed unexpectedly")

    def _verify_database_target(self) -> None:
        self._verify_state_directory()
        if self._database_path.is_symlink() or (
            hasattr(self._database_path, "is_junction") and self._database_path.is_junction()
        ):
            raise ContextIsolationError("Projection database cannot be a symbolic link")
        if self._locked_operation is None and self._database_path.exists():
            try:
                resolved = self._database_path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ContextIsolationError(
                    "Projection database cannot be safely resolved"
                ) from exc
            if resolved.parent != self._state_path:
                raise ContextIsolationError(
                    "Projection database resolves outside the state directory"
                )

    def _quiesce_live_database_for_swap(self) -> None:
        """Checkpoint a live WAL database or refuse a swap with stale sidecars."""

        sidecars = tuple(
            self._database_path.with_name(f"{self._database_path.name}{suffix}")
            for suffix in _LIVE_SIDECAR_SUFFIXES
        )
        present_sidecars = tuple(path for path in sidecars if os.path.lexists(path))
        if not present_sidecars:
            return
        for sidecar in present_sidecars:
            if sidecar.is_symlink():
                raise ContextIsolationError("SQLite sidecars cannot be symbolic links")
            try:
                resolved_sidecar = sidecar.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ProjectionBuildError("SQLite sidecar cannot be safely resolved") from exc
            if resolved_sidecar.parent != self._state_path:
                raise ContextIsolationError("SQLite sidecar resolves outside the state directory")
            if not resolved_sidecar.is_file():
                raise ProjectionBuildError("SQLite sidecar must be a regular file")
        if not self._database_path.exists():
            raise ProjectionBuildError("Orphaned SQLite sidecars prevent a safe projection swap")

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._database_path, timeout=0.25)
            connection.execute("PRAGMA busy_timeout = 250")
            journal_mode = cast(
                str, connection.execute("PRAGMA journal_mode").fetchone()[0]
            ).casefold()
            if journal_mode == "wal":
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is None or int(checkpoint[0]) != 0:
                    raise ProjectionBuildError(
                        "Live projection database is busy; its WAL cannot be checkpointed"
                    )
                resulting_mode = cast(
                    str, connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
                ).casefold()
                if resulting_mode != "delete":
                    raise ProjectionBuildError(
                        "Live projection database could not leave WAL mode safely"
                    )
            else:
                connection.execute("PRAGMA schema_version").fetchone()
        except ProjectionBuildError:
            raise
        except sqlite3.Error as exc:
            raise ProjectionBuildError(
                "Live SQLite sidecars prevent a safe projection swap"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

        for _ in range(10):
            if not any(os.path.lexists(path) for path in sidecars):
                return
            time.sleep(_REPLACE_RETRY_SECONDS)
        raise ProjectionBuildError("Live SQLite sidecars prevent a safe projection swap")

    @staticmethod
    def _require_contained_existing_path(path: Path, root: Path, label: str) -> Path:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectionBuildError(f"Missing or unreadable {label}") from exc
        if not resolved.is_relative_to(root):
            raise ContextIsolationError(f"{label.capitalize()} resolves outside the context root")
        return resolved

    def _require_local_uri(
        self, value: str | WorkctxUri, expected_type: EntityType | None = None
    ) -> WorkctxUri:
        uri = value if isinstance(value, WorkctxUri) else WorkctxUri.parse(value)
        try:
            uri.require_context(self._context_id)
        except ValueError as exc:
            raise ContextIsolationError("Structured URI belongs to another context") from exc
        if expected_type is not None and uri.entity_type != expected_type.value:
            raise ValueError(f"Expected a {expected_type.value} URI")
        return uri

    def _require_reference_context(self, value: str) -> None:
        reference = parse_durable_reference(value)
        if isinstance(reference, WorkctxUri):
            try:
                reference.require_context(self._context_id)
            except ValueError as exc:
                raise ContextIsolationError(
                    "Structured reference belongs to another context"
                ) from exc

    def _coerce_local_uri(self, value: str | WorkctxUri) -> WorkctxUri:
        try:
            return self._require_local_uri(value)
        except ValueError as exc:
            raise ContextIsolationError("URI is invalid or belongs to another context") from exc

    def _coerce_durable_target(self, value: str | WorkctxUri) -> str:
        if isinstance(value, WorkctxUri):
            try:
                value.require_context(self._context_id)
            except ValueError as exc:
                raise ContextIsolationError(
                    "Reference is invalid or belongs to another context"
                ) from exc
            return str(value)
        try:
            parsed = parse_durable_reference(value)
            if isinstance(parsed, WorkctxUri):
                parsed.require_context(self._context_id)
            return str(parsed)
        except ValueError as exc:
            raise ContextIsolationError(
                "Reference is invalid or belongs to another context"
            ) from exc

    def _local_identifier(
        self,
        value: str | WorkctxUri,
        expected_type: EntityType,
        validator: Callable[[str], object] | None = None,
    ) -> str:
        if isinstance(value, WorkctxUri) or (
            isinstance(value, str) and value.startswith("workctx://")
        ):
            uri = self._coerce_local_uri(value)
            if uri.entity_type != expected_type.value:
                raise ValueError(f"Expected a {expected_type.value} URI")
            identifier = uri.entity_id
        else:
            identifier = value
        if not identifier:
            raise ValueError("Identifier must not be empty")
        if validator is not None:
            validator(identifier)
        return identifier


def _metadata_record(row: sqlite3.Row) -> ProjectionMetadata:
    return ProjectionMetadata(
        projection_schema_version=cast(int, row["projection_schema_version"]),
        workspace_schema_version=cast(int, row["workspace_schema_version"]),
        context_id=cast(str, row["context_id"]),
        context_updated_at=_parse_datetime(cast(str, row["context_updated_at"])),
        source_fingerprint=cast(str, row["source_fingerprint"]),
        source_file_count=cast(int, row["source_file_count"]),
        indexed_document_count=cast(int, row["indexed_document_count"]),
        skipped_document_count=cast(int, row["skipped_document_count"]),
        build_started_at=_parse_datetime(cast(str, row["build_started_at"])),
        build_completed_at=_parse_datetime(cast(str, row["build_completed_at"])),
    )


def _literal_fts_query(value: str) -> str:
    tokens = _unicode61_query_tokens(value)
    if not tokens:
        raise ValueError("Search query must contain at least one word")
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _unicode61_query_tokens(value: str) -> tuple[str, ...]:
    """Approximate unicode61 boundaries while retaining in-token combining marks."""

    tokens: list[str] = []
    current: list[str] = []
    for character in unicodedata.normalize("NFC", value):
        category = unicodedata.category(character)
        is_word_character = character != "_" and (character.isalnum() or category == "Co")
        is_combining_continuation = category.startswith("M") and bool(current)
        if is_word_character or is_combining_continuation:
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _datetime_text(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_optional_datetime(value: object) -> datetime | None:
    return None if value is None else _parse_datetime(cast(str, value))


def _enum_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_search_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return _json_text(value)


def _update_fingerprint(digest: Any, relative_path: str, content: bytes) -> None:
    encoded_path = relative_path.encode("utf-8")
    digest.update(len(encoded_path).to_bytes(8, "big"))
    digest.update(encoded_path)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)


def _validation_diagnostic(error: ValidationError) -> str:
    locations = sorted(
        {
            ".".join(str(component) for component in item["loc"])
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        }
    )
    location_text = ", ".join(locations[:8]) or "document"
    return f"Canonical model validation failed at: {location_text}"


def _yaml_diagnostic(error: yaml.YAMLError) -> str:
    mark = getattr(error, "problem_mark", None)
    if mark is None:
        return "YAML frontmatter could not be parsed"
    return f"YAML frontmatter could not be parsed at line {mark.line + 1}, column {mark.column + 1}"


def _replace_with_retry(source: Path, destination: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if attempt + 1 == _REPLACE_ATTEMPTS:
                raise ProjectionBuildError(
                    "Projection swap remained blocked by another process"
                ) from exc
            time.sleep(_REPLACE_RETRY_SECONDS)


def _remove_temporary_database(path: Path, allowed_parent: Path) -> None:
    try:
        if path.parent.resolve(strict=True) != allowed_parent:
            return
    except (OSError, RuntimeError):
        return
    for candidate in (
        path,
        path.with_name(f"{path.name}-journal"),
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    ):
        with suppress(OSError):
            candidate.unlink(missing_ok=True)


def _remove_escaped_temporary_database(path: Path, expected_stat: os.stat_result) -> None:
    """Remove only the exact empty file that this rebuild just created."""

    if not (path.name.startswith(f"{_DATABASE_NAME}.") and path.name.endswith(".tmp")):
        raise ProjectionBuildError("Escaped temporary projection could not be identified")
    try:
        current_stat = path.stat(follow_symlinks=False)
        if not os.path.samestat(current_stat, expected_stat):
            raise ProjectionBuildError("Escaped temporary projection changed unexpectedly")
        path.unlink()
    except ProjectionBuildError:
        raise
    except OSError as exc:
        raise ProjectionBuildError(
            "Escaped temporary projection could not be safely removed"
        ) from exc
