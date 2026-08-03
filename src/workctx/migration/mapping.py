"""Deterministic legacy-path and identity mapping preview."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from workctx.domain import (
    ClaimId,
    DecisionId,
    EvidenceId,
    ObservationId,
    PersonId,
    QuestionId,
    RiskId,
    SystemId,
    WorkctxUri,
)
from workctx.migration.inventory import InventoryAnalysis, LegacyDocument, LegacyFile
from workctx.migration.models import (
    FileClassification,
    MappingAction,
    MappingRecord,
    PrecisionLoss,
    SkippedFile,
)

_GENERIC_ID_PATTERNS = {
    "draft": re.compile(r"^DRAFT-[A-Za-z0-9._-]+$"),
    "flow": re.compile(r"^FLOW-[A-Za-z0-9._-]+$"),
    "incident": re.compile(r"^INC-[A-Za-z0-9._-]+$"),
    "integration": re.compile(r"^INT-[A-Za-z0-9._-]+$"),
    "investigation": re.compile(r"^INV-[A-Za-z0-9._-]+$"),
    "module": re.compile(r"^MOD-[A-Za-z0-9._-]+$"),
    "project": re.compile(r"^PRJ-[A-Za-z0-9._-]+$"),
    "service": re.compile(r"^SVC-[A-Za-z0-9._-]+$"),
    "team": re.compile(r"^TEAM-[A-Za-z0-9._-]+$"),
}
_GENERIC_PREFIXES = {
    "draft": "DRAFT",
    "flow": "FLOW",
    "incident": "INC",
    "integration": "INT",
    "investigation": "INV",
    "module": "MOD",
    "project": "PRJ",
    "service": "SVC",
    "team": "TEAM",
}
_ENTITY_DIRECTORIES = {
    "claim": "02_knowledge/claims",
    "decision": "02_knowledge/decisions",
    "draft": "05_outbox/documentation",
    "evidence": "02_knowledge/evidence",
    "flow": "02_knowledge/flows",
    "incident": "03_work/incidents",
    "integration": "02_knowledge/integrations",
    "investigation": "03_work/investigations",
    "module": "02_knowledge/modules",
    "observation": "02_knowledge/observations",
    "person": "02_knowledge/people",
    "project": "02_knowledge/projects",
    "question": "02_knowledge/questions",
    "risk": "02_knowledge/risks",
    "service": "02_knowledge/services",
    "system": "02_knowledge/systems",
    "task": "03_work/tasks",
    "team": "02_knowledge/teams",
}
_MUTABLE_KEYS = frozenset(
    {
        "architecture",
        "blockers",
        "deadline",
        "dependencies",
        "due",
        "due_at",
        "next_action",
        "owner",
        "ownership",
        "status",
        "waiting_on",
    }
)


@dataclass(frozen=True, slots=True)
class ArtifactPlan:
    source: LegacyFile
    inbox_path: str
    reference: str


@dataclass(frozen=True, slots=True)
class PlannedDocument:
    source: LegacyDocument
    target_id: str
    target_uri: str
    target_path: str
    task_parent_id: str | None = None
    task_root_id: str | None = None
    provenance_evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    context_id: str
    documents: tuple[PlannedDocument, ...]
    artifacts: tuple[ArtifactPlan, ...]
    mappings: tuple[MappingRecord, ...]
    skipped_files: tuple[SkippedFile, ...]
    precision_losses: tuple[PrecisionLoss, ...]

    def document_by_source(self) -> dict[str, PlannedDocument]:
        return {document.source.relative_path: document for document in self.documents}

    def documents_by_old_id(self) -> dict[str, tuple[PlannedDocument, ...]]:
        grouped: dict[str, list[PlannedDocument]] = {}
        for document in self.documents:
            if document.source.old_id is not None:
                grouped.setdefault(document.source.old_id, []).append(document)
        return {
            key: tuple(sorted(values, key=lambda item: item.source.relative_path.casefold()))
            for key, values in grouped.items()
        }

    def artifact_by_source(self) -> dict[str, ArtifactPlan]:
        return {artifact.source.relative_path: artifact for artifact in self.artifacts}


class _IdAllocator:
    def __init__(self, migration_time: datetime) -> None:
        self._date = migration_time.strftime("%Y%m%d")
        self._year = migration_time.year
        self._used: set[str] = set()
        self._sequences: dict[str, int] = {}

    def reserve_or_allocate(
        self,
        entity_type: str,
        old_id: str | None,
        source_path: str,
    ) -> str:
        if old_id is not None and old_id not in self._used and _valid_id(entity_type, old_id):
            self._used.add(old_id)
            return old_id
        slug = _slug(PurePosixPath(source_path).stem)
        return self.allocate(entity_type, slug)

    def allocate(self, entity_type: str, slug: str) -> str:
        if entity_type == "evidence":
            return self._dated("EVD", slug, width=2)
        if entity_type == "claim":
            return self._year_sequence("CLM", width=5)
        if entity_type == "observation":
            evidence_id = self._dated("EVD", slug, width=2)
            observation_id = f"{evidence_id}#OBS-001"
            self._used.add(observation_id)
            return observation_id
        if entity_type == "decision":
            return self._year_sequence("DEC", width=3)
        if entity_type == "question":
            return self._year_sequence("Q", width=3)
        if entity_type == "risk":
            return self._year_sequence("RISK", width=3)
        if entity_type == "person":
            return self._slug_id("PER", slug)
        if entity_type == "system":
            return self._slug_id("SYS", slug)
        prefix = _GENERIC_PREFIXES.get(entity_type, entity_type.upper())
        return self._slug_id(prefix, slug)

    def allocate_parent_task(self, old_id: str | None) -> str:
        if old_id is not None and old_id not in self._used and _valid_parent_task_id(old_id):
            self._used.add(old_id)
            return old_id
        return self._year_sequence("TASK", width=3)

    def allocate_subtask(self, parent_id: str, old_id: str | None) -> str:
        if (
            old_id is not None
            and old_id not in self._used
            and re.fullmatch(rf"{re.escape(parent_id)}-ST[0-9]{{2}}", old_id) is not None
        ):
            self._used.add(old_id)
            return old_id
        key = f"{parent_id}-ST"
        sequence = self._sequences.get(key, 0)
        while sequence < 99:
            sequence += 1
            candidate = f"{parent_id}-ST{sequence:02d}"
            if candidate not in self._used:
                self._sequences[key] = sequence
                self._used.add(candidate)
                return candidate
        raise ValueError(f"Subtask sequence exhausted for {parent_id}")

    def _dated(self, prefix: str, slug: str, *, width: int) -> str:
        key = f"{prefix}-{self._date}-{slug}"
        sequence = self._sequences.get(key, 0)
        limit = (10**width) - 1
        while sequence < limit:
            sequence += 1
            candidate = f"{key}-{sequence:0{width}d}"
            if candidate not in self._used:
                self._sequences[key] = sequence
                self._used.add(candidate)
                return candidate
        raise ValueError(f"ID sequence exhausted for {key}")

    def _year_sequence(self, prefix: str, *, width: int) -> str:
        key = f"{prefix}-{self._year}"
        sequence = self._sequences.get(key, 0)
        limit = (10**width) - 1
        while sequence < limit:
            sequence += 1
            candidate = f"{key}-{sequence:0{width}d}"
            if candidate not in self._used:
                self._sequences[key] = sequence
                self._used.add(candidate)
                return candidate
        raise ValueError(f"ID sequence exhausted for {key}")

    def _slug_id(self, prefix: str, slug: str) -> str:
        base = f"{prefix}-{slug}"
        candidate = base
        sequence = 1
        while candidate in self._used:
            sequence += 1
            candidate = f"{base}-{sequence}"
        self._used.add(candidate)
        return candidate


def build_mapping_preview(
    analysis: InventoryAnalysis,
    *,
    context_id: str,
    migration_time: datetime,
) -> MigrationPlan:
    """Allocate stable target identities and an old-path to new-URI preview."""

    allocator = _IdAllocator(migration_time)
    precision: list[PrecisionLoss] = []
    skipped: list[SkippedFile] = []
    mappings: list[MappingRecord] = []
    artifacts: list[ArtifactPlan] = []

    migratable_files = {
        legacy_document.relative_path
        for legacy_document in analysis.documents
        if not legacy_document.contains_secret and not legacy_document.unsafe_content
    }
    for source_file in analysis.files:
        if source_file.contains_secret or source_file.unsafe_content:
            if source_file.classification is FileClassification.CANONICAL:
                skipped.append(
                    SkippedFile(
                        path=source_file.report_path,
                        reason=(
                            "possible_secret_not_copied"
                            if source_file.contains_secret
                            else "unsafe_instruction_content_not_copied"
                        ),
                    )
                )
                mappings.append(
                    MappingRecord(
                        source_path=source_file.report_path,
                        action=MappingAction.SKIP,
                        note="Unsafe source bytes are never copied.",
                    )
                )
            continue
        preserve = source_file.relative_path in migratable_files or (
            source_file.classification is FileClassification.CANONICAL
            and source_file.entity_type == "artifact"
        )
        if preserve:
            artifact = _artifact_plan(source_file)
            artifacts.append(artifact)
            mappings.append(
                MappingRecord(
                    source_path=source_file.report_path,
                    target_uri=artifact.reference,
                    target_path=artifact.inbox_path,
                    action=MappingAction.PRESERVE_ARTIFACT,
                    note="Preserved legacy source bytes registered through ingestion.",
                )
            )

    safe_documents = tuple(
        document
        for document in analysis.documents
        if not document.contains_secret and not document.unsafe_content
    )
    non_tasks = tuple(document for document in safe_documents if document.entity_type != "task")
    tasks = tuple(document for document in safe_documents if document.entity_type == "task")
    planned: list[PlannedDocument] = []

    for document in non_tasks:
        target_id = allocator.reserve_or_allocate(
            document.entity_type,
            document.old_id,
            document.relative_path,
        )
        planned_document = _planned_document(document, target_id, context_id)
        if _was_reidentified(document, target_id):
            precision.append(
                PrecisionLoss(
                    code="MIG-ID-REALLOCATED",
                    path=document.report_path,
                    message=(
                        "The legacy ID was missing, invalid, or duplicated; a new ID was allocated."
                    ),
                )
            )
        planned.append(planned_document)

    task_plans, task_precision = _plan_tasks(tasks, allocator, context_id)
    planned.extend(task_plans)
    precision.extend(task_precision)

    with_provenance: list[PlannedDocument] = []
    for planned_document in sorted(planned, key=lambda item: item.source.relative_path.casefold()):
        evidence_id: str | None = None
        if planned_document.source.entity_type not in {"evidence", "observation"} and (
            planned_document.source.entity_type == "claim"
            or any(key in planned_document.source.frontmatter for key in _MUTABLE_KEYS)
        ):
            evidence_id = allocator.allocate(
                "evidence",
                _slug(PurePosixPath(planned_document.source.relative_path).stem),
            )
        enriched = PlannedDocument(
            source=planned_document.source,
            target_id=planned_document.target_id,
            target_uri=planned_document.target_uri,
            target_path=planned_document.target_path,
            task_parent_id=planned_document.task_parent_id,
            task_root_id=planned_document.task_root_id,
            provenance_evidence_id=evidence_id,
        )
        with_provenance.append(enriched)
        mappings.append(
            MappingRecord(
                source_path=planned_document.source.report_path,
                source_id=planned_document.source.old_id,
                target_id=planned_document.target_id,
                target_uri=planned_document.target_uri,
                target_path=planned_document.target_path,
                action=MappingAction.MIGRATE,
            )
        )
        if evidence_id is not None:
            evidence_uri = str(WorkctxUri(context_id, "evidence", evidence_id))
            mappings.append(
                MappingRecord(
                    source_path=planned_document.source.report_path,
                    target_id=evidence_id,
                    target_uri=evidence_uri,
                    target_path=_target_path("evidence", evidence_id),
                    action=MappingAction.MIGRATE,
                    note="Generated provenance evidence for recoverable mutable-state locators.",
                )
            )

    mapped_sources = {document.source.relative_path for document in with_provenance}
    artifact_sources = {artifact.source.relative_path for artifact in artifacts}
    for source_file in analysis.files:
        if (
            source_file.relative_path in mapped_sources
            or source_file.relative_path in artifact_sources
        ):
            continue
        reason = {
            FileClassification.GENERATED: "generated_view_rebuilt_from_canonical_state",
            FileClassification.OBSOLETE: "obsolete_legacy_file",
            FileClassification.UNKNOWN: "unknown_or_unsupported_file",
        }.get(source_file.classification, "not_selected_for_migration")
        skipped.append(SkippedFile(path=source_file.report_path, reason=reason))
        mappings.append(
            MappingRecord(
                source_path=source_file.report_path,
                action=MappingAction.SKIP,
                note=reason,
            )
        )

    return MigrationPlan(
        context_id=context_id,
        documents=tuple(with_provenance),
        artifacts=tuple(sorted(artifacts, key=lambda item: item.source.relative_path.casefold())),
        mappings=tuple(
            sorted(
                mappings,
                key=lambda item: (
                    item.source_path.casefold(),
                    item.action.value,
                    item.target_uri or "",
                ),
            )
        ),
        skipped_files=tuple(
            sorted(set(skipped), key=lambda item: (item.path.casefold(), item.reason))
        ),
        precision_losses=tuple(
            sorted(
                set(precision),
                key=lambda item: (item.path.casefold(), item.code, item.message),
            )
        ),
    )


def _plan_tasks(
    tasks: tuple[LegacyDocument, ...],
    allocator: _IdAllocator,
    context_id: str,
) -> tuple[list[PlannedDocument], list[PrecisionLoss]]:
    by_path = {task.relative_path: task for task in tasks}
    by_old_id: dict[str, list[LegacyDocument]] = {}
    for task in tasks:
        if task.old_id is not None:
            by_old_id.setdefault(task.old_id, []).append(task)

    parent_paths: dict[str, str | None] = {}
    precision: list[PrecisionLoss] = []
    for task in tasks:
        authored = task.frontmatter.get("parent_task", task.frontmatter.get("parent"))
        parent_path = _resolve_task_parent(task, authored, by_path, by_old_id)
        parent_paths[task.relative_path] = parent_path
        if authored not in (None, "") and parent_path is None:
            precision.append(
                PrecisionLoss(
                    code="MIG-TASK-PARENT-UNAVAILABLE",
                    path=task.report_path,
                    message=(
                        "The authored task parent did not resolve; the task became a parent task."
                    ),
                )
            )

    root_paths: dict[str, str] = {}
    for task in tasks:
        root, flattened = _root_task_path(task.relative_path, parent_paths)
        root_paths[task.relative_path] = root
        if flattened:
            precision.append(
                PrecisionLoss(
                    code="MIG-TASK-HIERARCHY-FLATTENED",
                    path=task.report_path,
                    message=(
                        "The legacy hierarchy exceeded the canonical parent/subtask depth and "
                        "was flattened to its root while retaining the legacy parent metadata."
                    ),
                )
            )

    root_ids: dict[str, str] = {}
    for root_path in sorted(set(root_paths.values()), key=str.casefold):
        root_source = by_path[root_path]
        root_ids[root_path] = allocator.allocate_parent_task(root_source.old_id)

    planned: list[PlannedDocument] = []
    for task in sorted(tasks, key=lambda item: item.relative_path.casefold()):
        root_path = root_paths[task.relative_path]
        root_id = root_ids[root_path]
        if task.relative_path == root_path:
            target_id = root_id
            parent_id = None
        else:
            target_id = allocator.allocate_subtask(root_id, task.old_id)
            parent_id = root_id
        if _was_reidentified(task, target_id):
            precision.append(
                PrecisionLoss(
                    code="MIG-ID-REALLOCATED",
                    path=task.report_path,
                    message="The legacy task ID was normalized to the canonical hierarchy.",
                )
            )
        base = _planned_document(task, target_id, context_id)
        planned.append(
            PlannedDocument(
                source=base.source,
                target_id=base.target_id,
                target_uri=base.target_uri,
                target_path=base.target_path,
                task_parent_id=parent_id,
                task_root_id=root_id,
            )
        )
    return planned, precision


def _resolve_task_parent(
    task: LegacyDocument,
    authored: object,
    by_path: dict[str, LegacyDocument],
    by_old_id: dict[str, list[LegacyDocument]],
) -> str | None:
    if not isinstance(authored, str) or not authored.strip():
        return None
    value = authored.strip()
    candidates = by_old_id.get(value)
    if candidates:
        return sorted(candidates, key=lambda item: item.relative_path.casefold())[0].relative_path
    portable = PurePosixPath(value.replace("\\", "/"))
    candidate = PurePosixPath(task.relative_path).parent.joinpath(portable)
    normalized = _normalize_relative(candidate)
    return normalized if normalized in by_path else None


def _root_task_path(
    start: str,
    parents: dict[str, str | None],
) -> tuple[str, bool]:
    chain: list[str] = []
    current = start
    while True:
        if current in chain:
            return min(chain[chain.index(current) :], key=str.casefold), True
        chain.append(current)
        parent = parents.get(current)
        if parent is None:
            return current, len(chain) > 2
        current = parent


def _planned_document(document: LegacyDocument, target_id: str, context_id: str) -> PlannedDocument:
    return PlannedDocument(
        source=document,
        target_id=target_id,
        target_uri=str(WorkctxUri(context_id, document.entity_type, target_id)),
        target_path=_target_path(document.entity_type, target_id),
    )


def _target_path(entity_type: str, target_id: str) -> str:
    directory = _ENTITY_DIRECTORIES[entity_type]
    filename = target_id.replace("#", "%23")
    return f"{directory}/{filename}.md"


def _artifact_plan(source: LegacyFile) -> ArtifactPlan:
    relative_digest = hashlib.sha256(source.relative_path.encode("utf-8")).hexdigest()[:12]
    safe_name = _portable_filename(PurePosixPath(source.relative_path).name)
    digest = source.content_hash.removeprefix("sha256:")
    return ArtifactPlan(
        source=source,
        inbox_path=f"00_inbox/raw/legacy/{relative_digest}/{safe_name}",
        reference=f"artifact://sha256/{digest}",
    )


def _portable_filename(value: str) -> str:
    path = Path(value)
    suffix = path.suffix.casefold()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    stem = _slug(path.stem)
    return f"{stem[:64]}{suffix}"


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    return (slug or "legacy-record")[:48].rstrip("-")


def _valid_id(entity_type: str, value: str) -> bool:
    try:
        if entity_type == "evidence":
            EvidenceId.parse(value)
        elif entity_type == "claim":
            ClaimId.parse(value)
        elif entity_type == "observation":
            ObservationId.parse(value)
        elif entity_type == "decision":
            DecisionId.parse(value)
        elif entity_type == "question":
            QuestionId.parse(value)
        elif entity_type == "risk":
            RiskId.parse(value)
        elif entity_type == "person":
            PersonId.parse(value)
        elif entity_type == "system":
            SystemId.parse(value)
        else:
            pattern = _GENERIC_ID_PATTERNS.get(entity_type)
            return pattern is not None and pattern.fullmatch(value) is not None
    except ValueError:
        return False
    return True


def _valid_parent_task_id(value: str) -> bool:
    return re.fullmatch(r"TASK-[0-9]{4}-[0-9]{3}", value) is not None


def _was_reidentified(document: LegacyDocument, target_id: str) -> bool:
    return document.old_id != target_id


def _normalize_relative(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


__all__ = [
    "ArtifactPlan",
    "MigrationPlan",
    "PlannedDocument",
    "build_mapping_preview",
]
