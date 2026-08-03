"""Thirteen-stage deterministic legacy migration engine."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from workctx.adapters.sqlite import SQLiteProjection
from workctx.adapters.sqlite.freshness import SqliteFreshnessProbe
from workctx.domain import ArtifactSourceType
from workctx.ingestion import (
    DuplicatePolicy,
    IngestionService,
    RegisterRequest,
    RegistrationDisposition,
)
from workctx.migration.audit import DEFAULT_LEDGER_WRITER, MigrationLedgerWriter
from workctx.migration.errors import (
    MigrationBlockedError,
    MigrationBoundaryError,
    MigrationError,
    MigrationSourceChangedError,
    MigrationValidationError,
)
from workctx.migration.inventory import (
    fingerprint_source_tree,
    hash_file,
    inventory_source,
    safe_report_path,
)
from workctx.migration.mapping import ArtifactPlan, build_mapping_preview
from workctx.migration.models import (
    FindingSeverity,
    InventoryRecord,
    LedgerSummary,
    MappingAction,
    MappingRecord,
    MigrationFinding,
    MigrationMode,
    MigrationReport,
    PrecisionLoss,
    ProjectionSummary,
    ReportPaths,
    SkippedFile,
    SourceIntegrity,
    StageRecord,
    StageStatus,
    ValidationDiagnostic,
    ValidationSummary,
    ViewSummary,
)
from workctx.migration.normalize import NormalizedMigration, normalize_migration
from workctx.migration.reporting import (
    REPORT_JSON_PATH,
    REPORT_MARKDOWN_PATH,
    write_migration_reports,
)
from workctx.services.contexts import initialize_context, slugify_context_id
from workctx.transactions import verify_ledger
from workctx.validation import ValidationReport, contains_possible_secret, validate_workspace
from workctx.views import rebuild_views

type MigrationClock = Callable[[], datetime]

_STAGES = (
    "Inventory and classify files",
    "Calculate source hashes and no-write report data",
    "Detect unsafe content and structural findings",
    "Map directories into a new context template",
    "Normalize frontmatter through domain models",
    "Preserve source artifacts through ingestion",
    "Create only observations with recoverable locators",
    "Convert references and task hierarchy",
    "Generate evidence-backed mutable-state claims",
    "Validate the staged context",
    "Rebuild projections and generated views",
    "Produce the old-path to new-URI migration ledger",
    "Verify the source tree is unchanged",
)
_TEXT_ARTIFACT_SUFFIXES = frozenset({".cfg", ".json", ".md", ".txt", ".yaml", ".yml"})


def migrate_legacy(
    source_path: Path,
    target_context_path: Path,
    *,
    apply_changes: bool = False,
    allow_findings: bool = False,
    clock: MigrationClock | None = None,
    ledger_writer: MigrationLedgerWriter = DEFAULT_LEDGER_WRITER,
) -> MigrationReport:
    """Preview or apply one source-preserving legacy migration."""

    migration_time = _utc_time((clock or _utc_now)())
    analysis = inventory_source(source_path)
    target, target_existed = _preflight_target(analysis.root, target_context_path)
    context_id = _context_id(target, analysis.tree_hash)
    plan = build_mapping_preview(
        analysis,
        context_id=context_id,
        migration_time=migration_time,
    )
    mode = MigrationMode.APPLY if apply_changes else MigrationMode.DRY_RUN
    source_after_preview = fingerprint_source_tree(analysis.root)
    _require_source_unchanged(analysis.tree_hash, source_after_preview)
    blocking = tuple(finding for finding in analysis.findings if finding.blocks_apply)

    if not apply_changes:
        return _report(
            mode=mode,
            applied=False,
            blocked=bool(blocking) and not allow_findings,
            allow_findings=allow_findings,
            migration_time=migration_time,
            source_label=safe_report_path(analysis.root.name),
            context_id=context_id,
            source_before=analysis.tree_hash,
            source_after=source_after_preview,
            inventory=analysis.records,
            findings=analysis.findings,
            mappings=plan.mappings,
            skipped_files=plan.skipped_files,
            precision_losses=plan.precision_losses,
            stages=_stages(completed={1, 2, 3}),
        )

    if blocking and not allow_findings:
        report = _report(
            mode=mode,
            applied=False,
            blocked=True,
            allow_findings=False,
            migration_time=migration_time,
            source_label=safe_report_path(analysis.root.name),
            context_id=context_id,
            source_before=analysis.tree_hash,
            source_after=source_after_preview,
            inventory=analysis.records,
            findings=analysis.findings,
            mappings=plan.mappings,
            skipped_files=plan.skipped_files,
            precision_losses=plan.precision_losses,
            stages=_stages(completed={1, 2, 3}),
        )
        raise MigrationBlockedError(report)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".workctx-migration-", dir=target.parent)).resolve(
        strict=True
    )
    published = False
    try:
        initialize_context(
            temporary,
            name=_context_name(target),
            context_id=context_id,
        )
        normalized = normalize_migration(plan, migration_time=migration_time)
        registration_events, artifact_mappings, ingestion_findings = _preserve_artifacts(
            temporary,
            plan.artifacts,
            migration_time=migration_time,
        )
        normalized = NormalizedMigration(
            operations=normalized.operations,
            mappings=_merge_artifact_mappings(normalized.mappings, artifact_mappings),
            precision_losses=normalized.precision_losses,
            source_references=normalized.source_references,
        )
        receipt = ledger_writer.apply_import(
            temporary,
            normalized,
            migration_time=migration_time,
            source_fingerprint=analysis.tree_hash,
        )
        if receipt.projection.state.value != "fresh":
            raise MigrationError("The canonical import committed with a stale projection.")

        canonical_validation = validate_workspace(temporary)
        if not canonical_validation.ok:
            failed_report = _report(
                mode=mode,
                applied=False,
                blocked=True,
                allow_findings=allow_findings,
                migration_time=migration_time,
                source_label=safe_report_path(analysis.root.name),
                context_id=context_id,
                source_before=analysis.tree_hash,
                source_after=fingerprint_source_tree(analysis.root),
                inventory=analysis.records,
                findings=(*analysis.findings, *ingestion_findings),
                mappings=normalized.mappings,
                skipped_files=plan.skipped_files,
                precision_losses=normalized.precision_losses,
                stages=_stages(completed=set(range(1, 10)), failed=10),
                validation=_validation_summary(canonical_validation),
            )
            raise MigrationValidationError(failed_report)

        projection_report = SQLiteProjection(temporary).rebuild()
        if projection_report.skipped_documents:
            raise MigrationError("Projection rebuild skipped normalized migration documents.")
        view_report = rebuild_views(
            temporary,
            clock=lambda: migration_time,
            session_id=f"migration-views-{analysis.tree_hash[-12:]}",
        )
        final_validation = validate_workspace(
            temporary,
            freshness_probe=SqliteFreshnessProbe(),
        )
        if not final_validation.ok:
            failed_report = _report(
                mode=mode,
                applied=False,
                blocked=True,
                allow_findings=allow_findings,
                migration_time=migration_time,
                source_label=safe_report_path(analysis.root.name),
                context_id=context_id,
                source_before=analysis.tree_hash,
                source_after=fingerprint_source_tree(analysis.root),
                inventory=analysis.records,
                findings=(*analysis.findings, *ingestion_findings),
                mappings=normalized.mappings,
                skipped_files=plan.skipped_files,
                precision_losses=normalized.precision_losses,
                stages=_stages(completed=set(range(1, 11)), failed=11),
                validation=_validation_summary(final_validation),
            )
            raise MigrationValidationError(failed_report)

        verification = verify_ledger(temporary)
        import_events = verification.event_count - registration_events
        source_after = fingerprint_source_tree(analysis.root)
        _require_source_unchanged(analysis.tree_hash, source_after)
        report = _report(
            mode=mode,
            applied=True,
            blocked=False,
            allow_findings=allow_findings,
            migration_time=migration_time,
            source_label=safe_report_path(analysis.root.name),
            context_id=context_id,
            source_before=analysis.tree_hash,
            source_after=source_after,
            inventory=analysis.records,
            findings=(*analysis.findings, *ingestion_findings),
            mappings=normalized.mappings,
            skipped_files=plan.skipped_files,
            precision_losses=normalized.precision_losses,
            stages=_stages(completed=set(range(1, 14))),
            validation=_validation_summary(final_validation),
            projection=ProjectionSummary(
                documents_seen=projection_report.counts.documents_seen,
                documents_indexed=projection_report.counts.documents_indexed,
                documents_skipped=projection_report.counts.documents_skipped,
                entities=projection_report.counts.entities,
                observations=projection_report.counts.observations,
                claims=projection_report.counts.claims,
                tasks=projection_report.counts.tasks,
            ),
            views=ViewSummary(paths=tuple(sorted(view.path for view in view_report.views))),
            ledger=LedgerSummary(
                interaction=ledger_writer.interaction,
                artifact_registration_events=registration_events,
                import_events=import_events,
                total_events=verification.event_count,
            ),
            report_paths=ReportPaths(
                json=REPORT_JSON_PATH,
                markdown=REPORT_MARKDOWN_PATH,
            ),
        )
        write_migration_reports(temporary, report)
        report_validation = validate_workspace(
            temporary,
            freshness_probe=SqliteFreshnessProbe(),
        )
        if not report_validation.ok:
            failed = report.model_copy(
                update={
                    "applied": False,
                    "blocked": True,
                    "validation": _validation_summary(report_validation),
                    "stages": _stages(completed=set(range(1, 12)), failed=12),
                }
            )
            raise MigrationValidationError(failed)
        _require_source_unchanged(
            analysis.tree_hash,
            fingerprint_source_tree(analysis.root),
        )
        _publish_staged_context(
            temporary,
            target,
            target_existed=target_existed,
        )
        published = True
        return report
    finally:
        if not published and temporary.exists():
            _remove_staging_context(temporary, target.parent)


def _preserve_artifacts(
    context_root: Path,
    artifacts: tuple[ArtifactPlan, ...],
    *,
    migration_time: datetime,
) -> tuple[int, tuple[MappingRecord, ...], tuple[MigrationFinding, ...]]:
    service = IngestionService(context_root, clock=lambda: migration_time)
    mappings: list[MappingRecord] = []
    findings: list[MigrationFinding] = []
    before = verify_ledger(context_root).event_count
    for artifact in artifacts:
        destination = context_root.joinpath(*PurePosixPath(artifact.inbox_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_verified(artifact.source.absolute_path, destination, artifact.source.content_hash)
        suffix = destination.suffix.casefold()
        result = service.register(
            RegisterRequest(
                path=artifact.inbox_path,
                source_type=(
                    ArtifactSourceType.NOTE
                    if suffix in _TEXT_ARTIFACT_SUFFIXES
                    else ArtifactSourceType.OTHER
                ),
                source_origin=(
                    "legacy://source/"
                    f"{hashlib.sha256(artifact.source.relative_path.encode('utf-8')).hexdigest()[:16]}"
                ),
                language="en" if suffix in _TEXT_ARTIFACT_SUFFIXES else None,
                classification="confidential",
                duplicate_policy=DuplicatePolicy.LINK,
            ),
            session_id=f"migration-ingest-{artifact.source.content_hash[-16:]}",
        )
        if result.artifact.reference != artifact.reference:
            raise MigrationSourceChangedError(
                "A preserved artifact did not match its inventoried source hash."
            )
        if result.disposition is RegistrationDisposition.QUARANTINED:
            findings.append(
                MigrationFinding(
                    code="MIG-ARTIFACT-QUARANTINED",
                    severity=FindingSeverity.WARNING,
                    path=artifact.source.report_path,
                    message="The ingestion engine quarantined this preserved source artifact.",
                    blocks_apply=False,
                )
            )
        mappings.append(
            MappingRecord(
                source_path=artifact.source.report_path,
                target_uri=artifact.reference,
                target_path=result.artifact.manifest.preserved_path,
                action=MappingAction.PRESERVE_ARTIFACT,
                note="Preserved legacy source bytes registered through ingestion.",
            )
        )
    after = verify_ledger(context_root).event_count
    return after - before, tuple(mappings), tuple(findings)


def _merge_artifact_mappings(
    mappings: tuple[MappingRecord, ...],
    replacements: tuple[MappingRecord, ...],
) -> tuple[MappingRecord, ...]:
    replacement_keys = {
        (item.source_path, item.target_uri, item.action): item for item in replacements
    }
    merged: list[MappingRecord] = []
    for item in mappings:
        key = (item.source_path, item.target_uri, item.action)
        merged.append(replacement_keys.pop(key, item))
    merged.extend(replacement_keys.values())
    return tuple(
        sorted(
            set(merged),
            key=lambda item: (
                item.source_path.casefold(),
                item.action.value,
                item.target_uri or "",
            ),
        )
    )


def _copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                digest.update(chunk)
                writer.write(chunk)
    except OSError as exc:
        raise MigrationBoundaryError(
            "A legacy source artifact could not be preserved safely."
        ) from exc
    copied_hash = f"sha256:{digest.hexdigest()}"
    source_hash, _size = hash_file(source)
    if copied_hash != expected_hash or source_hash != expected_hash:
        raise MigrationSourceChangedError("The legacy source changed while it was being copied.")


def _preflight_target(source: Path, requested: Path) -> tuple[Path, bool]:
    expanded = requested.expanduser()
    if expanded.is_symlink() or _is_junction(expanded):
        raise MigrationBoundaryError("The migration target cannot be a link or junction.")
    try:
        target = expanded.resolve(strict=False)
    except OSError as exc:
        raise MigrationBoundaryError("The migration target path cannot be resolved.") from exc
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise MigrationBoundaryError("Source and target context boundaries must not overlap.")
    if target.exists():
        if not target.is_dir():
            raise MigrationError("The migration target exists and is not a directory.")
        try:
            non_empty = next(target.iterdir(), None) is not None
        except OSError as exc:
            raise MigrationBoundaryError("The migration target cannot be inspected.") from exc
        if non_empty:
            raise MigrationError("The migration target directory is not empty.")
        return target, True
    return target, False


def _publish_staged_context(
    staged: Path,
    target: Path,
    *,
    target_existed: bool,
) -> None:
    if target_existed:
        if not target.is_dir() or target.is_symlink() or _is_junction(target):
            raise MigrationSourceChangedError("The empty migration target changed before publish.")
        if next(target.iterdir(), None) is not None:
            raise MigrationSourceChangedError("The empty migration target changed before publish.")
        target.rmdir()
    elif target.exists():
        raise MigrationSourceChangedError("The migration target appeared before publish.")
    try:
        os.replace(staged, target)
    except OSError:
        if target_existed and not target.exists():
            target.mkdir()
        raise


def _remove_staging_context(staged: Path, allowed_parent: Path) -> None:
    resolved = staged.resolve(strict=False)
    parent = allowed_parent.resolve(strict=True)
    if resolved.parent != parent or not resolved.name.startswith(".workctx-migration-"):
        raise MigrationBoundaryError("Refusing to remove an unexpected staging path.")
    shutil.rmtree(resolved)


def _context_id(target: Path, source_fingerprint: str) -> str:
    try:
        return slugify_context_id(target.name)
    except ValueError:
        suffix = source_fingerprint.removeprefix("sha256:")[:8]
        return slugify_context_id(f"legacy-{target.name}-{suffix}")


def _context_name(target: Path) -> str:
    words = target.name.replace("_", " ").replace("-", " ").strip()
    return words.title() or "Migrated Legacy Context"


def _validation_summary(report: ValidationReport) -> ValidationSummary:
    return ValidationSummary(
        ok=report.ok,
        issues=tuple(
            ValidationDiagnostic(
                severity=issue.severity.value,
                code=issue.code,
                path=issue.path,
                message=_safe_diagnostic(issue.message),
            )
            for issue in report.issues
        ),
    )


def _safe_diagnostic(message: str) -> str:
    normalized = " ".join(message.replace("\r", " ").replace("\n", " ").split())
    if contains_possible_secret(normalized):
        return "Validation issue detected; source details were redacted."
    return normalized[:500]


def _stages(
    *,
    completed: set[int],
    failed: int | None = None,
) -> tuple[StageRecord, ...]:
    records: list[StageRecord] = []
    for number, name in enumerate(_STAGES, start=1):
        if number == failed:
            status = StageStatus.FAILED
            detail = "The stage failed; no destination context was published."
        elif number in completed:
            status = StageStatus.COMPLETED
            detail = "Completed deterministically."
        else:
            status = StageStatus.NOT_RUN
            detail = "Not run in this mode or after the reported stop condition."
        records.append(StageRecord(number=number, name=name, status=status, detail=detail))
    return tuple(records)


def _report(
    *,
    mode: MigrationMode,
    applied: bool,
    blocked: bool,
    allow_findings: bool,
    migration_time: datetime,
    source_label: str,
    context_id: str,
    source_before: str,
    source_after: str,
    inventory: tuple[InventoryRecord, ...],
    findings: tuple[MigrationFinding, ...],
    mappings: tuple[MappingRecord, ...],
    skipped_files: tuple[SkippedFile, ...],
    precision_losses: tuple[PrecisionLoss, ...],
    stages: tuple[StageRecord, ...],
    validation: ValidationSummary | None = None,
    projection: ProjectionSummary | None = None,
    views: ViewSummary | None = None,
    ledger: LedgerSummary | None = None,
    report_paths: ReportPaths | None = None,
) -> MigrationReport:
    return MigrationReport(
        mode=mode,
        applied=applied,
        blocked=blocked,
        allow_findings=allow_findings,
        generated_at=migration_time,
        source_label=source_label,
        target_context_id=context_id,
        source_integrity=SourceIntegrity(
            before=source_before,
            after=source_after,
            unchanged=source_before == source_after,
        ),
        inventory=inventory,
        findings=tuple(
            sorted(
                set(findings),
                key=lambda item: (item.path.casefold(), item.code, item.locator or ""),
            )
        ),
        mappings=mappings,
        skipped_files=skipped_files,
        precision_losses=precision_losses,
        stages=stages,
        validation=validation,
        projection=projection,
        views=views,
        ledger=ledger,
        report_paths=report_paths,
    )


def _require_source_unchanged(before: str, after: str) -> None:
    if before != after:
        raise MigrationSourceChangedError("The legacy source tree changed during migration.")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Migration clocks must return timezone-aware datetimes.")
    return value.astimezone(UTC).replace(microsecond=0)


def _is_junction(path: Path) -> bool:
    try:
        return path.is_junction()
    except (AttributeError, OSError):
        return False


__all__ = ["MigrationClock", "migrate_legacy"]
