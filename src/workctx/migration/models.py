"""Typed records for deterministic legacy-context migration."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class MigrationMode(StrEnum):
    DRY_RUN = "dry_run"
    APPLY = "apply"


class FileClassification(StrEnum):
    CANONICAL = "canonical"
    GENERATED = "generated"
    OBSOLETE = "obsolete"
    UNKNOWN = "unknown"


class MappingAction(StrEnum):
    MIGRATE = "migrate"
    PRESERVE_ARTIFACT = "preserve_artifact"
    SKIP = "skip"


class FindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    ADVISORY = "advisory"


class StageStatus(StrEnum):
    COMPLETED = "completed"
    NOT_RUN = "not_run"
    FAILED = "failed"


class LedgerInteraction(StrEnum):
    """Supported migration-side audit policy values.

    Only ``single_import`` has an implementation in WP-510. The enum and writer
    protocol form the decision seam without pretending the open question is settled.
    """

    SINGLE_IMPORT = "single_import"
    PER_ENTITY = "per_entity"
    NONE = "none"


class _MigrationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class InventoryRecord(_MigrationRecord):
    path: str
    classification: FileClassification
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    entity_type: str | None = None


class MigrationFinding(_MigrationRecord):
    code: str = Field(pattern=r"^MIG-[A-Z0-9-]+$")
    severity: FindingSeverity
    path: str
    locator: str | None = None
    message: str
    blocks_apply: bool = False


class MappingRecord(_MigrationRecord):
    source_path: str
    source_id: str | None = None
    target_id: str | None = None
    target_uri: str | None = None
    target_path: str | None = None
    action: MappingAction
    note: str | None = None


class SkippedFile(_MigrationRecord):
    path: str
    reason: str


class PrecisionLoss(_MigrationRecord):
    code: str = Field(pattern=r"^MIG-[A-Z0-9-]+$")
    path: str
    message: str


class StageRecord(_MigrationRecord):
    number: int = Field(ge=1, le=13)
    name: str
    status: StageStatus
    detail: str


class SourceIntegrity(_MigrationRecord):
    before: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    after: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    unchanged: bool


class ValidationDiagnostic(_MigrationRecord):
    severity: str
    code: str
    path: str | None = None
    message: str


class ValidationSummary(_MigrationRecord):
    ok: bool
    issues: tuple[ValidationDiagnostic, ...] = ()


class ProjectionSummary(_MigrationRecord):
    documents_seen: int = Field(ge=0)
    documents_indexed: int = Field(ge=0)
    documents_skipped: int = Field(ge=0)
    entities: int = Field(ge=0)
    observations: int = Field(ge=0)
    claims: int = Field(ge=0)
    tasks: int = Field(ge=0)


class ViewSummary(_MigrationRecord):
    paths: tuple[str, ...] = ()


class LedgerSummary(_MigrationRecord):
    interaction: LedgerInteraction
    decision_required: Literal[True] = True
    artifact_registration_events: int = Field(ge=0)
    import_events: int = Field(ge=0)
    total_events: int = Field(ge=0)


class ReportPaths(_MigrationRecord):
    json_path: str = Field(alias="json", serialization_alias="json")
    markdown_path: str = Field(alias="markdown", serialization_alias="markdown")


class MigrationReport(_MigrationRecord):
    schema_version: Literal[1] = 1
    mode: MigrationMode
    applied: bool
    blocked: bool
    allow_findings: bool
    generated_at: AwareDatetime
    source_label: str
    target_context_id: str
    source_integrity: SourceIntegrity
    inventory: tuple[InventoryRecord, ...]
    findings: tuple[MigrationFinding, ...]
    mappings: tuple[MappingRecord, ...]
    skipped_files: tuple[SkippedFile, ...]
    precision_losses: tuple[PrecisionLoss, ...]
    stages: tuple[StageRecord, ...]
    validation: ValidationSummary | None = None
    projection: ProjectionSummary | None = None
    views: ViewSummary | None = None
    ledger: LedgerSummary | None = None
    report_paths: ReportPaths | None = None


__all__ = [
    "FileClassification",
    "FindingSeverity",
    "InventoryRecord",
    "LedgerInteraction",
    "LedgerSummary",
    "MappingAction",
    "MappingRecord",
    "MigrationFinding",
    "MigrationMode",
    "MigrationReport",
    "PrecisionLoss",
    "ProjectionSummary",
    "ReportPaths",
    "SkippedFile",
    "SourceIntegrity",
    "StageRecord",
    "StageStatus",
    "ValidationDiagnostic",
    "ValidationSummary",
    "ViewSummary",
]
