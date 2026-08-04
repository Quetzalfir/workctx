"""Deterministic artifact registration, quarantine, and archive lifecycle."""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import ValidationError

import workctx.transactions.engine as _transaction_engine
from workctx.adapters.filesystem import (
    CanonicalStore,
    ContextLock,
    ContextZone,
    IntentRecord,
    IntentTargetKind,
    LockFenceError,
    RecoveryState,
    StagedMove,
    StagedReplacement,
)
from workctx.adapters.sqlite import SQLiteProjection
from workctx.adapters.sqlite.models import RebuildReport
from workctx.domain.artifacts import ArtifactManifest, ArtifactStatus
from workctx.domain.references import ArtifactReference
from workctx.domain.transactions import (
    ArtifactManifestDocumentPayload,
    CreateOperation,
    SystemActor,
    TransactionProposal,
    UpdateOperation,
)
from workctx.ingestion.errors import (
    ArtifactNotFoundError,
    ArtifactReceiptError,
    ArtifactStateError,
    DuplicateArtifactError,
    IngestionRecoveryPendingError,
)
from workctx.ingestion.models import (
    ArchiveDisposition,
    ArchiveResult,
    ArtifactRecord,
    DuplicatePolicy,
    InboxListing,
    IngestionDiagnostic,
    IngestionPolicy,
    QuarantineInfo,
    RegisterRequest,
    RegistrationDisposition,
    RegistrationResult,
    _ManifestMetadata,
    _SidecarMetadata,
)
from workctx.ingestion.scanning import ArtifactScan, hash_preserved_file, scan_artifact
from workctx.transactions import (
    ApplyResult,
    DiagnosticSeverity,
    PostconditionRollbackError,
    PreimageChangedError,
    ProposalValidationError,
    RecoveryPendingError,
    RecoveryResult,
    RecoveryStrategy,
    StaleRevisionError,
    apply,
    authenticate_apply_result,
    verify_ledger,
)
from workctx.transactions.ledger import append_event

_MAX_TRANSACTION_RETRIES = 4
_MANIFEST_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
_ARTIFACT_SEQUENCE = re.compile(r"^(?P<prefix>ART-[0-9]{8}-[a-z0-9-]+)-(?P<sequence>[0-9]{2})$")


class _ApplyTransaction(Protocol):
    def __call__(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ) -> ApplyResult: ...


@dataclass(frozen=True, slots=True)
class _ScannedPath:
    relative_path: str
    scan: ArtifactScan


@dataclass(frozen=True, slots=True)
class _MoveSpec:
    source: str
    destination: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class _MoveOutcome:
    already_applied: bool
    recovered: bool


@dataclass(frozen=True, slots=True)
class BatchRegistrationOutcome:
    """One attempted or skipped item in an ordered registration batch."""

    request: RegisterRequest
    registration: RegistrationResult | None = None
    duplicate: ArtifactRecord | None = None
    error: Exception | None = None
    attempted: bool = True

    def __post_init__(self) -> None:
        populated = sum(
            value is not None for value in (self.registration, self.duplicate, self.error)
        )
        if self.attempted and populated != 1:
            raise ValueError("An attempted batch item must have exactly one outcome")
        if not self.attempted and populated:
            raise ValueError("A not-attempted batch item cannot have an outcome")


@dataclass(frozen=True, slots=True)
class BatchRegistrationResult:
    """Ordered batch outcomes with at most one hard per-file failure."""

    outcomes: tuple[BatchRegistrationOutcome, ...]

    @property
    def failure(self) -> BatchRegistrationOutcome | None:
        return next((outcome for outcome in self.outcomes if outcome.error is not None), None)

    @property
    def registration_count(self) -> int:
        return sum(outcome.registration is not None for outcome in self.outcomes)


class IngestionService:
    """Application service bound to one isolated Work Context root."""

    def __init__(
        self,
        context_root: Path,
        *,
        policy: IngestionPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        transaction_apply: _ApplyTransaction = apply,
        stager_factory: Callable[[Path], StagedReplacement] = StagedReplacement,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._policy = policy or IngestionPolicy()
        self._clock = clock or _utc_now
        self._transaction_apply = transaction_apply
        self._uses_default_transaction_apply = transaction_apply is apply
        self._stager_factory = stager_factory
        self._batch_operation: _BatchTransactionOperation | None = None

    @property
    def context_root(self) -> Path:
        return self._root

    def register(
        self,
        request: RegisterRequest,
        *,
        session_id: str | None = None,
    ) -> RegistrationResult:
        """Register one existing raw artifact and quarantine suspicious bytes."""

        session = session_id or f"ingestion-register-{secrets.token_hex(8)}"
        existing = self._records()
        source = self._resolve_evidence_path(request.path, prefixes=("00_inbox/raw",))
        source_exists = source.is_file() and not source.is_symlink() and not _is_junction(source)

        replay = self._replay_candidate(existing, request, source_exists=source_exists)
        if replay is not None:
            return self._resume_or_return_registration(replay, request, session_id=session)
        if not source_exists:
            raise ArtifactNotFoundError("The requested raw artifact was not found.")

        primary = _ScannedPath(
            request.path,
            scan_artifact(
                source,
                declared_media_type=request.media_type,
                policy=self._policy,
            ),
        )
        sidecars = tuple(self._scan_sidecar(path) for path in request.sidecars)
        diagnostics = _scan_diagnostics(primary, sidecars)

        duplicate = _first_by_content_hash(existing, primary.scan.content_hash)
        if duplicate is not None and not diagnostics:
            if request.duplicate_policy is DuplicatePolicy.REFUSE:
                raise DuplicateArtifactError(duplicate_of=duplicate.manifest.id)
            status = ArtifactStatus.DUPLICATE
            duplicate_of = duplicate.manifest.id
        elif diagnostics:
            status = ArtifactStatus.QUARANTINED
            duplicate_of = None
        else:
            status = ArtifactStatus.PENDING
            duplicate_of = None

        ingested_at = _normalize_transaction_time(self._clock())
        artifact_id = _allocate_artifact_id(existing, request.path, ingested_at)
        manifest_path = _manifest_path(artifact_id)
        preserved_path = (
            _destination_path("00_inbox/quarantine", artifact_id, request.path)
            if status is ArtifactStatus.QUARANTINED
            else request.path
        )
        preserved_sidecars = (
            tuple(
                _destination_path(
                    "00_inbox/quarantine",
                    artifact_id,
                    sidecar.relative_path,
                    sidecar_index=index,
                )
                for index, sidecar in enumerate(sidecars, start=1)
            )
            if status is ArtifactStatus.QUARANTINED
            else tuple(sidecar.relative_path for sidecar in sidecars)
        )
        metadata = _ManifestMetadata(
            registered_path=request.path,
            sidecars=tuple(
                _SidecarMetadata(
                    source_path=sidecar.relative_path,
                    content_hash=sidecar.scan.content_hash,
                )
                for sidecar in sidecars
            ),
            quarantine_diagnostics=diagnostics,
        )
        manifest = ArtifactManifest(
            schema_version=1,
            id=artifact_id,
            content_hash=primary.scan.content_hash,
            original_name=PurePosixPath(request.path).name,
            media_type=primary.scan.media_type,
            source_type=request.source_type,
            source_origin=request.source_origin,
            event_at=request.event_at,
            event_at_inferred=request.event_at_inferred,
            ingested_at=ingested_at,
            language=request.language,
            participants=list(request.participants),
            classification=request.classification,
            status=status,
            preserved_path=preserved_path,
            sidecars=list(preserved_sidecars),
            duplicate_of=duplicate_of,
            notes=_encode_metadata(metadata),
        )

        registration_receipt = self._create_manifest(
            manifest_path,
            manifest,
            action="register",
            session_id=session,
        )
        receipts = [registration_receipt]
        recovered_move = False
        if status is ArtifactStatus.QUARANTINED:
            manifest, metadata_receipt = self._persist_quarantine_receipt(
                manifest_path,
                manifest,
                registration_receipt,
                session_id=session,
            )
            receipts.append(metadata_receipt)
            specs = _quarantine_move_specs(manifest, _require_metadata(manifest))
            outcome = self._move_with_receipt(
                manifest,
                metadata_receipt,
                specs,
                transaction_id=_move_transaction_id("quarantine", manifest.id),
                session_id=session,
            )
            recovered_move = outcome.recovered

        disposition = {
            ArtifactStatus.DUPLICATE: RegistrationDisposition.DUPLICATE_LINKED,
            ArtifactStatus.QUARANTINED: RegistrationDisposition.QUARANTINED,
        }.get(status, RegistrationDisposition.REGISTERED)
        return RegistrationResult(
            disposition=disposition,
            artifact=_record(manifest_path, manifest),
            diagnostics=diagnostics,
            receipts=tuple(receipts),
            recovered_move=recovered_move,
        )

    def register_batch(
        self,
        requests: Iterable[RegisterRequest],
        *,
        session_id: str | None = None,
    ) -> BatchRegistrationResult:
        """Register requests in order, stopping after the first hard failure.

        Duplicate refusal is represented as a per-file outcome. Every other
        exception is attached to the failing item, and later requests are
        returned as not attempted. With the default transaction adapter, all
        attempted items share one lock, heartbeat lease, and projection
        preflight while retaining an independent durable commit per file.
        """

        ordered = tuple(requests)
        if not ordered:
            return BatchRegistrationResult(())
        if self._batch_operation is not None:
            raise RuntimeError("Nested ingestion registration batches are not supported")

        session = session_id or f"ingestion-register-batch-{secrets.token_hex(8)}"
        if not self._uses_default_transaction_apply:
            return self._register_batch_requests(ordered, session_id=session)

        with _BatchTransactionOperation(
            self._root,
            session_id=session,
            clock=self._clock,
            stager_factory=self._stager_factory,
        ) as operation:
            self._batch_operation = operation
            try:
                return self._register_batch_requests(ordered, session_id=session)
            finally:
                self._batch_operation = None

    def _register_batch_requests(
        self,
        requests: tuple[RegisterRequest, ...],
        *,
        session_id: str,
    ) -> BatchRegistrationResult:
        outcomes: list[BatchRegistrationOutcome] = []
        for index, request in enumerate(requests):
            item_session = f"{session_id}-{index + 1}"
            try:
                registration = self.register(request, session_id=item_session)
            except DuplicateArtifactError as exc:
                try:
                    duplicate = self._record_by_id(exc.duplicate_of)
                except Exception as error:
                    outcomes.append(BatchRegistrationOutcome(request=request, error=error))
                    outcomes.extend(
                        BatchRegistrationOutcome(request=remaining, attempted=False)
                        for remaining in requests[index + 1 :]
                    )
                    break
                outcomes.append(BatchRegistrationOutcome(request=request, duplicate=duplicate))
            except Exception as error:
                outcomes.append(BatchRegistrationOutcome(request=request, error=error))
                outcomes.extend(
                    BatchRegistrationOutcome(request=remaining, attempted=False)
                    for remaining in requests[index + 1 :]
                )
                break
            else:
                outcomes.append(
                    BatchRegistrationOutcome(request=request, registration=registration)
                )
        return BatchRegistrationResult(tuple(outcomes))

    def list_inbox(
        self,
        *,
        statuses: frozenset[ArtifactStatus] | None = None,
    ) -> InboxListing:
        """List schema-valid manifests in deterministic ID order."""

        records = self._records()
        if statuses is not None:
            records = tuple(record for record in records if record.manifest.status in statuses)
        return InboxListing(artifacts=records)

    def quarantine_info(self, artifact_id: str) -> QuarantineInfo:
        """Return content-free quarantine reasons and physical move state."""

        record = self._record_by_id(artifact_id)
        if record.manifest.status is not ArtifactStatus.QUARANTINED:
            raise ArtifactStateError("The requested artifact is not quarantined.")
        metadata = _require_metadata(record.manifest)
        source = self._resolve_evidence_path(metadata.registered_path, prefixes=("00_inbox/raw",))
        destination = self._resolve_evidence_path(
            record.manifest.preserved_path,
            prefixes=("00_inbox/quarantine",),
        )
        inspection = self._stager_factory(self._root).inspect_recovery()
        recovery_pending = (
            inspection.state is not RecoveryState.CLEAN
            and inspection.intent is not None
            and inspection.intent.transaction_id
            == _move_transaction_id("quarantine", record.manifest.id)
        )
        return QuarantineInfo(
            artifact=record,
            diagnostics=metadata.quarantine_diagnostics,
            recovery_pending=recovery_pending,
            source_present=source.is_file(),
            destination_present=destination.is_file(),
        )

    def archive_after(
        self,
        artifact_id: str,
        receipt: ApplyResult,
        *,
        session_id: str | None = None,
    ) -> ArchiveResult:
        """Archive a raw original only after authenticating a referencing receipt."""

        session = session_id or f"ingestion-archive-{secrets.token_hex(8)}"
        record = self._record_by_id(artifact_id)
        manifest = record.manifest
        reference = _artifact_reference(manifest)
        self._authenticate_receipt(receipt, reference)
        metadata = _require_metadata(manifest)

        if manifest.status in {
            ArtifactStatus.DUPLICATE,
            ArtifactStatus.FAILED,
            ArtifactStatus.QUARANTINED,
        }:
            raise ArtifactStateError("The artifact is not eligible for archive.")

        source_path = metadata.registered_path
        was_processed = manifest.status is ArtifactStatus.PROCESSED
        manifest_receipt: ApplyResult | None = None
        if not was_processed:
            if manifest.status not in {ArtifactStatus.PENDING, ArtifactStatus.PROCESSING}:
                raise ArtifactStateError("The artifact is not eligible for archive.")
            self._require_registered_hashes(manifest, metadata)
            destinations = _archive_destinations(manifest, metadata)
            manifest = _updated_manifest(
                manifest,
                status=ArtifactStatus.PROCESSED,
                preserved_path=destinations[0],
                sidecars=list(destinations[1:]),
            )
            manifest_receipt = self._update_manifest(
                record.manifest_path,
                manifest,
                action="archive-manifest",
                session_id=session,
            )

        specs = _archive_move_specs(manifest, metadata)
        outcome = self._move_with_receipt(
            manifest,
            receipt,
            specs,
            transaction_id=_move_transaction_id("archive", manifest.id),
            session_id=session,
        )
        if was_processed:
            disposition = (
                ArchiveDisposition.ALREADY_ARCHIVED
                if outcome.already_applied
                else ArchiveDisposition.RECOVERED
            )
        else:
            disposition = (
                ArchiveDisposition.RECOVERED if outcome.recovered else ArchiveDisposition.ARCHIVED
            )
        return ArchiveResult(
            disposition=disposition,
            artifact=_record(record.manifest_path, manifest),
            source_path=source_path,
            destination_path=manifest.preserved_path,
            manifest_receipt=manifest_receipt,
        )

    def _records(self) -> tuple[ArtifactRecord, ...]:
        directory = self._store.resolve_path("00_inbox/manifests", zones=(ContextZone.INBOX,))
        records: list[ArtifactRecord] = []
        seen_ids: set[str] = set()
        for path in sorted(directory.iterdir(), key=lambda candidate: candidate.name.casefold()):
            if path.suffix.lower() not in _MANIFEST_SUFFIXES:
                continue
            relative = path.relative_to(self._root).as_posix()
            manifest = self._store.read_artifact_manifest(relative)
            if manifest.id in seen_ids:
                raise ArtifactStateError("Artifact manifests contain a duplicate ID.")
            seen_ids.add(manifest.id)
            records.append(_record(relative, manifest))
        return tuple(sorted(records, key=lambda record: record.manifest.id))

    def _record_by_id(self, artifact_id: str) -> ArtifactRecord:
        matches = tuple(record for record in self._records() if record.manifest.id == artifact_id)
        if len(matches) != 1:
            raise ArtifactNotFoundError("The requested artifact manifest was not found.")
        return matches[0]

    def _scan_sidecar(self, relative_path: str) -> _ScannedPath:
        path = self._resolve_evidence_path(relative_path, prefixes=("00_inbox/raw",))
        return _ScannedPath(
            relative_path,
            scan_artifact(path, declared_media_type=None, policy=self._policy),
        )

    def _replay_candidate(
        self,
        records: tuple[ArtifactRecord, ...],
        request: RegisterRequest,
        *,
        source_exists: bool,
    ) -> ArtifactRecord | None:
        candidates = tuple(
            record
            for record in records
            if (metadata := _decode_metadata(record.manifest.notes)) is not None
            and metadata.registered_path == request.path
        )
        if not candidates:
            return None
        if not source_exists:
            for record in reversed(candidates):
                metadata = _require_metadata(record.manifest)
                if tuple(item.source_path for item in metadata.sidecars) != request.sidecars:
                    continue
                if record.manifest.status not in {
                    ArtifactStatus.QUARANTINED,
                    ArtifactStatus.PROCESSED,
                }:
                    continue
                destination = self._resolve_evidence_path(
                    record.manifest.preserved_path,
                    prefixes=("00_inbox/quarantine", "01_processed"),
                )
                inspection = self._stager_factory(self._root).inspect_recovery()
                if destination.is_file() or inspection.state is not RecoveryState.CLEAN:
                    return record
            return None

        source = self._resolve_evidence_path(request.path, prefixes=("00_inbox/raw",))
        current_hash = hash_preserved_file(source)
        for record in reversed(candidates):
            metadata = _require_metadata(record.manifest)
            if tuple(item.source_path for item in metadata.sidecars) != request.sidecars:
                continue
            destination_exists = self._resolve_evidence_path(
                record.manifest.preserved_path,
                prefixes=("00_inbox/raw", "00_inbox/quarantine", "01_processed"),
            ).is_file()
            source_is_authoritative = (
                record.manifest.preserved_path == request.path or not destination_exists
            )
            if not source_is_authoritative:
                continue
            if record.manifest.content_hash != current_hash:
                raise ArtifactStateError(
                    "A registered raw path changed before its lifecycle move completed."
                )
            for sidecar in metadata.sidecars:
                sidecar_path = self._resolve_evidence_path(
                    sidecar.source_path,
                    prefixes=("00_inbox/raw",),
                )
                if hash_preserved_file(sidecar_path) != sidecar.content_hash:
                    raise ArtifactStateError(
                        "A registered sidecar changed before its lifecycle move completed."
                    )
            return record
        return None

    def _resume_or_return_registration(
        self,
        record: ArtifactRecord,
        request: RegisterRequest,
        *,
        session_id: str,
    ) -> RegistrationResult:
        manifest = record.manifest
        metadata = _require_metadata(manifest)
        receipts: tuple[ApplyResult, ...] = ()
        recovered = False
        if manifest.status is ArtifactStatus.QUARANTINED:
            authorization = metadata.quarantine_receipt
            if authorization is None:
                inspection = self._stager_factory(self._root).inspect_recovery()
                if inspection.state is not RecoveryState.CLEAN:
                    raise ArtifactStateError(
                        "A quarantine move lacks its authenticated recovery receipt."
                    )
                authorization = self._update_manifest(
                    record.manifest_path,
                    manifest,
                    action="quarantine-authorize",
                    session_id=session_id,
                )
                manifest, stored = self._persist_quarantine_receipt(
                    record.manifest_path,
                    manifest,
                    authorization,
                    session_id=session_id,
                )
                receipts = (authorization, stored)
                metadata = _require_metadata(manifest)
            outcome = self._move_with_receipt(
                manifest,
                authorization,
                _quarantine_move_specs(manifest, metadata),
                transaction_id=_move_transaction_id("quarantine", manifest.id),
                session_id=session_id,
            )
            recovered = outcome.recovered
        return RegistrationResult(
            disposition=RegistrationDisposition.ALREADY_REGISTERED,
            artifact=_record(record.manifest_path, manifest),
            diagnostics=metadata.quarantine_diagnostics,
            receipts=receipts,
            recovered_move=recovered,
        )

    def _persist_quarantine_receipt(
        self,
        manifest_path: str,
        manifest: ArtifactManifest,
        receipt: ApplyResult,
        *,
        session_id: str,
    ) -> tuple[ArtifactManifest, ApplyResult]:
        metadata = _require_metadata(manifest).model_copy(update={"quarantine_receipt": receipt})
        updated = _updated_manifest(manifest, notes=_encode_metadata(metadata))
        stored_receipt = self._update_manifest(
            manifest_path,
            updated,
            action="quarantine-receipt",
            session_id=session_id,
        )
        return updated, stored_receipt

    def _create_manifest(
        self,
        manifest_path: str,
        manifest: ArtifactManifest,
        *,
        action: str,
        session_id: str,
    ) -> ApplyResult:
        payload = ArtifactManifestDocumentPayload(kind="artifact_manifest", document=manifest)
        operation = CreateOperation(op="create", target=manifest_path, payload=payload)
        return self._apply_manifest_operation(
            manifest,
            operation,
            action=action,
            session_id=session_id,
        )

    def _update_manifest(
        self,
        manifest_path: str,
        manifest: ArtifactManifest,
        *,
        action: str,
        session_id: str,
    ) -> ApplyResult:
        target = self._store.resolve_path(manifest_path, zones=(ContextZone.INBOX,))
        expected_hash = hash_preserved_file(target)
        payload = ArtifactManifestDocumentPayload(kind="artifact_manifest", document=manifest)
        operation = UpdateOperation(
            op="update",
            target=manifest_path,
            payload=payload,
            expected_hash=expected_hash,
        )
        return self._apply_manifest_operation(
            manifest,
            operation,
            action=action,
            session_id=session_id,
        )

    def _apply_manifest_operation(
        self,
        manifest: ArtifactManifest,
        operation: CreateOperation | UpdateOperation,
        *,
        action: str,
        session_id: str,
    ) -> ApplyResult:
        last_stale: StaleRevisionError | None = None
        for _attempt in range(_MAX_TRANSACTION_RETRIES):
            created_at = _normalize_transaction_time(self._clock())
            proposal = TransactionProposal(
                schema_version=1,
                id=_proposal_id(created_at, action, manifest.id),
                context_id=self._store.context_id,
                base_revision=verify_ledger(self._root).head_hash,
                actor=SystemActor(
                    type="system",
                    id="workctx-ingestion",
                    agent=None,
                    model=None,
                ),
                created_at=created_at,
                source_refs=[_artifact_reference(manifest)],
                operations=[operation],
                preconditions=[],
                postconditions=[],
                expected_views=["sqlite"],
                approval="not_required",
            )
            try:
                transaction_apply = (
                    self._batch_operation.apply
                    if self._batch_operation is not None
                    else self._transaction_apply
                )
                return transaction_apply(
                    self._root,
                    proposal,
                    approved=False,
                    session_id=session_id,
                )
            except StaleRevisionError as error:
                last_stale = error
        if last_stale is not None:
            raise last_stale
        raise AssertionError("transaction retry loop did not execute")

    def _require_registered_hashes(
        self,
        manifest: ArtifactManifest,
        metadata: _ManifestMetadata,
    ) -> None:
        primary = self._resolve_evidence_path(
            metadata.registered_path,
            prefixes=("00_inbox/raw",),
        )
        if hash_preserved_file(primary) != manifest.content_hash:
            raise ArtifactStateError("The raw artifact no longer matches its manifest hash.")
        for sidecar in metadata.sidecars:
            path = self._resolve_evidence_path(sidecar.source_path, prefixes=("00_inbox/raw",))
            if hash_preserved_file(path) != sidecar.content_hash:
                raise ArtifactStateError("A raw sidecar no longer matches its registered hash.")

    def _authenticate_receipt(self, receipt: ApplyResult, reference: str) -> None:
        event = authenticate_apply_result(self._root, receipt)
        if reference not in event.source_refs:
            raise ArtifactReceiptError()

    def _move_with_receipt(
        self,
        manifest: ArtifactManifest,
        receipt: ApplyResult,
        specs: tuple[_MoveSpec, ...],
        *,
        transaction_id: str,
        session_id: str,
    ) -> _MoveOutcome:
        reference = _artifact_reference(manifest)
        batch_operation = self._batch_operation
        if batch_operation is not None:
            return batch_operation.run(
                lambda: self._move_with_receipt_under_lock(
                    receipt,
                    reference,
                    specs,
                    transaction_id=transaction_id,
                    lock=batch_operation.lock,
                )
            )
        with ContextLock.acquire(self._root, session_id=f"{session_id}-move") as lock:
            return self._move_with_receipt_under_lock(
                receipt,
                reference,
                specs,
                transaction_id=transaction_id,
                lock=lock,
            )
        raise AssertionError("evidence move did not produce a result")

    def _move_with_receipt_under_lock(
        self,
        receipt: ApplyResult,
        reference: str,
        specs: tuple[_MoveSpec, ...],
        *,
        transaction_id: str,
        lock: ContextLock,
    ) -> _MoveOutcome:
        self._authenticate_receipt(receipt, reference)
        stager = self._stager_factory(self._root)
        inspection = stager.inspect_recovery()
        if inspection.state is RecoveryState.CLEAN:
            state = self._move_state(specs)
            if state == "applied":
                return _MoveOutcome(already_applied=True, recovered=False)
            if state != "pending":
                raise ArtifactStateError("Evidence move paths are in a conflicting state.")
            intent = stager.prepare(
                transaction_id,
                lock.nonce,
                tuple(StagedMove(spec.source, spec.destination) for spec in specs),
                lock=lock,
            )
            if not _intent_matches_specs(intent, specs):
                stager.rollback(intent, lock=lock)
                self._authenticate_receipt(receipt, reference)
                stager.finalize_rollback_after_audit(transaction_id, lock=lock)
                raise ArtifactStateError("Evidence changed while its move was staged.")
            try:
                stager.apply(intent, lock=lock)
                self._authenticate_receipt(receipt, reference)
                # D-036: the authenticated WP-300 event audits the manifest
                # state; the separate WP-201 intent carries only physical moves.
                stager.finalize_after_audit(transaction_id, lock=lock)
                return _MoveOutcome(already_applied=False, recovered=False)
            except Exception as error:
                self._raise_move_recovery(stager, receipt, error)

        if inspection.intent is None or inspection.intent.transaction_id != transaction_id:
            raise ArtifactStateError("Another staged context mutation requires recovery.")
        if not _intent_matches_specs(inspection.intent, specs):
            raise ArtifactStateError("The staged evidence move does not match this artifact.")
        try:
            if inspection.state in {
                RecoveryState.PREPARED,
                RecoveryState.PARTIALLY_APPLIED,
            }:
                stager.complete_recovery(inspection.intent, lock=lock)
            elif inspection.state is not RecoveryState.FULLY_REPLACED_AWAITING_AUDIT:
                raise ArtifactStateError("The staged evidence move cannot be recovered safely.")
            self._authenticate_receipt(receipt, reference)
            stager.finalize_recovery_after_audit(transaction_id, lock=lock)
            return _MoveOutcome(already_applied=False, recovered=True)
        except Exception as error:
            self._raise_move_recovery(stager, receipt, error)
        raise AssertionError("evidence move did not produce a result")

    def _raise_move_recovery(
        self,
        stager: StagedReplacement,
        receipt: ApplyResult,
        error: Exception,
    ) -> None:
        pending = stager.inspect_recovery()
        if pending.state is not RecoveryState.CLEAN:
            raise IngestionRecoveryPendingError(
                inspection=pending,
                receipt=receipt,
            ) from error
        raise error

    def _move_state(self, specs: tuple[_MoveSpec, ...]) -> str:
        pending = 0
        applied = 0
        for spec in specs:
            source = self._resolve_evidence_path(
                spec.source,
                prefixes=("00_inbox/raw",),
            )
            destination = self._resolve_evidence_path(
                spec.destination,
                prefixes=("00_inbox/quarantine", "01_processed"),
            )
            source_hash = hash_preserved_file(source) if source.exists() else None
            destination_hash = hash_preserved_file(destination) if destination.exists() else None
            if source_hash == spec.content_hash and destination_hash is None:
                pending += 1
            elif source_hash is None and destination_hash == spec.content_hash:
                applied += 1
            else:
                return "conflict"
        if pending == len(specs):
            return "pending"
        if applied == len(specs):
            return "applied"
        return "conflict"

    def _resolve_evidence_path(self, relative_path: str, *, prefixes: tuple[str, ...]) -> Path:
        path = self._store.resolve_path(
            relative_path,
            zones=(ContextZone.INBOX, ContextZone.PROCESSED),
        )
        portable = PurePosixPath(relative_path).as_posix()
        if not any(portable.startswith(f"{prefix}/") for prefix in prefixes):
            raise ArtifactStateError("Artifact metadata names an invalid evidence zone.")
        return path


class _BatchTransactionOperation:
    """Apply ingestion-only proposals under one transaction operation scope."""

    def __init__(
        self,
        context_root: Path,
        *,
        session_id: str,
        clock: Callable[[], datetime],
        stager_factory: Callable[[Path], StagedReplacement],
    ) -> None:
        self._root = context_root
        self._session_id = session_id
        self._engine = _transaction_engine.TransactionEngine(
            context_root,
            stager_factory=stager_factory,
            clock=clock,
        )
        self._lock: ContextLock | None = None
        self._heartbeat: _transaction_engine._HeartbeatLease | None = None
        self._stager: StagedReplacement | None = None
        self._projection: SQLiteProjection | None = None
        self._projection_report: RebuildReport | None = None
        self._operation_cache: _transaction_engine._OperationCache | None = None
        self._closed = False

    @property
    def lock(self) -> ContextLock:
        if self._lock is None or self._closed:
            raise RuntimeError("The ingestion batch operation is not active")
        return self._lock

    def __enter__(self) -> _BatchTransactionOperation:
        if self._lock is not None:
            raise RuntimeError("The ingestion batch operation is already active")
        self._lock = ContextLock.acquire(self._root, session_id=self._session_id)
        self._heartbeat = _transaction_engine._HeartbeatLease(self._lock)
        self._operation_cache = _transaction_engine._OperationCache()
        self._engine._operation_cache = self._operation_cache
        try:
            self._heartbeat.start()
            self._stager = self.run(lambda: self._engine._stager_factory(self._root))
            inspection = self.run(self._stager.inspect_recovery)
            if inspection.state is not RecoveryState.CLEAN:
                raise RecoveryPendingError(inspection)
            self._projection = self.run(lambda: self._engine._projection_factory(self._root))
            self._projection._begin_locked_operation()
            self._projection_report = self.run(self._projection.rebuild)
            return self
        except Exception:
            self._close(raise_heartbeat_failure=False)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc, traceback
        self._close(raise_heartbeat_failure=exc_type is None)

    def run[ResultT](self, operation: Callable[[], ResultT]) -> ResultT:
        """Run one opaque batch step while checking the shared heartbeat."""

        return _transaction_engine._run_with_heartbeat(self.lock, operation)

    def apply(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ) -> ApplyResult:
        """Commit one file's proposal without reopening batch-wide resources."""

        del session_id
        if context_root != self._root:
            raise ValueError("The batch proposal belongs to another context root")
        self._require_ingestion_only(proposal)
        lock = self.lock
        stager = self._require_stager()
        projection = self._require_projection()
        projection_report = self._require_projection_report()
        operation_cache = self._require_operation_cache()
        operation_cache.clear()
        intent: IntentRecord | None = None
        try:
            inspection = self.run(stager.inspect_recovery)
            if inspection.state is not RecoveryState.CLEAN:
                raise RecoveryPendingError(inspection)

            verification, duplicate = self.run(
                lambda: self._engine._verify_apply_ledger(proposal.id)
            )
            self._engine._raise_apply_conflicts(proposal, verification, duplicate)
            analysis = self.run(
                lambda: self._engine._analyze(
                    proposal,
                    projection=projection,
                    verification=verification,
                    projection_report=projection_report,
                    check_duplicate=False,
                )
            )
            if proposal.approval == "required" and not approved:
                analysis = _transaction_engine._with_diagnostic(
                    analysis,
                    _transaction_engine._diagnostic(
                        "TXN-APPROVAL-REQUIRED",
                        DiagnosticSeverity.ERROR,
                        "The proposal requires explicit runtime approval.",
                        repair_action="Approve the reviewed proposal and retry.",
                    ),
                )
            if not analysis.valid or analysis.compiled is None:
                raise ProposalValidationError(
                    _transaction_engine._validation_result(proposal, analysis)
                )
            compiled = analysis.compiled

            lock.verify_fence()
            prepared_intent = self.run(
                lambda: stager.prepare(
                    proposal.id,
                    lock.nonce,
                    compiled.writes,
                    lock=lock,
                )
            )
            intent = prepared_intent
            if not _transaction_engine._intent_matches_operations(
                prepared_intent,
                compiled.audit_operations,
            ):
                event = self.run(
                    lambda: self._engine._proposal_event(
                        proposal,
                        operations=_transaction_engine._intent_audit_operations(prepared_intent),
                        action="apply",
                        result="rolled_back",
                        prev_hash=verification.head_hash,
                    )
                )
                appended = self.run(lambda: append_event(self._root, event, lock=lock))
                self._engine._verify_appended_event(event, appended)
                _transaction_engine._run_stager_step(
                    lock,
                    stager,
                    lambda: stager.finalize_rollback_after_audit(
                        proposal.id,
                        lock=lock,
                    ),
                )
                intent = None
                result = RecoveryResult(
                    strategy=RecoveryStrategy.ROLLBACK,
                    outcome="rolled_back",
                    transaction_id=proposal.id,
                    committed_revision=event.event_hash,
                    applied_targets=(),
                    ledger_event_id=event.id,
                    ledger_event_hash=event.event_hash,
                    projection=_transaction_engine._fresh_projection_status(),
                    diagnostics=(
                        _transaction_engine._diagnostic(
                            "TXN-PREIMAGE-CHANGED",
                            DiagnosticSeverity.ERROR,
                            "A target changed after validation; the proposal was rolled back.",
                            repair_action=(
                                "Rebase the proposal on the committed rollback revision."
                            ),
                        ),
                    ),
                )
                raise PreimageChangedError(result)

            _transaction_engine._run_stager_step(
                lock,
                stager,
                lambda: stager.apply(prepared_intent, lock=lock),
            )
            operation_cache.clear()

            postcondition_diagnostics = self.run(
                lambda: self._engine._live_condition_diagnostics(
                    proposal.postconditions,
                    field_name="postconditions",
                )
            )
            post_report = self.run(
                lambda: self._engine._workspace_validator(
                    self._root,
                    strict=True,
                    freshness_probe=None,
                )
            )
            if not post_report.ok or any(
                item.severity is DiagnosticSeverity.ERROR for item in postcondition_diagnostics
            ):
                _transaction_engine._run_stager_step(
                    lock,
                    stager,
                    lambda: stager.rollback(prepared_intent, lock=lock),
                )
                event = self.run(
                    lambda: self._engine._proposal_event(
                        proposal,
                        operations=compiled.audit_operations,
                        action="apply",
                        result="rolled_back",
                        prev_hash=verification.head_hash,
                    )
                )
                appended = self.run(lambda: append_event(self._root, event, lock=lock))
                self._engine._verify_appended_event(event, appended)
                _transaction_engine._run_stager_step(
                    lock,
                    stager,
                    lambda: stager.finalize_rollback_after_audit(
                        proposal.id,
                        lock=lock,
                    ),
                )
                intent = None
                recovery_result = RecoveryResult(
                    strategy=RecoveryStrategy.ROLLBACK,
                    outcome="rolled_back",
                    transaction_id=proposal.id,
                    committed_revision=event.event_hash,
                    applied_targets=(),
                    ledger_event_id=event.id,
                    ledger_event_hash=event.event_hash,
                    projection=_transaction_engine._fresh_projection_status(),
                    diagnostics=(
                        *postcondition_diagnostics,
                        *_transaction_engine._workspace_diagnostics(post_report),
                    ),
                )
                raise PostconditionRollbackError(recovery_result)

            event = self.run(
                lambda: self._engine._proposal_event(
                    proposal,
                    operations=compiled.audit_operations,
                    action="apply",
                    result="committed",
                    prev_hash=verification.head_hash,
                )
            )
            appended = self.run(lambda: append_event(self._root, event, lock=lock))
            self._engine._verify_appended_event(event, appended)
            _transaction_engine._run_stager_step(
                lock,
                stager,
                lambda: stager.finalize_after_audit(proposal.id, lock=lock),
            )
            intent = None

            return ApplyResult(
                proposal_id=proposal.id,
                context_id=proposal.context_id,
                base_revision=proposal.base_revision,
                committed_revision=event.event_hash,
                applied_targets=_transaction_engine._applied_targets(compiled.effects),
                ledger_event_id=event.id,
                ledger_event_hash=event.event_hash,
                ledger_source_refs=tuple(event.source_refs),
                # Ingestion proposals mutate only manifests; SQLite indexes
                # neither inbox manifests nor evidence paths, so the one
                # verified preflight remains current throughout this batch.
                projection=_transaction_engine._fresh_projection_status(),
            )
        except PostconditionRollbackError:
            raise
        except Exception as exc:
            if intent is not None:
                pending = stager.inspect_recovery()
                if pending.state is not RecoveryState.CLEAN:
                    raise RecoveryPendingError(pending) from exc
            raise
        finally:
            operation_cache.clear()

    @staticmethod
    def _require_ingestion_only(proposal: TransactionProposal) -> None:
        for operation in proposal.operations:
            if not isinstance(operation, (CreateOperation, UpdateOperation)) or not (
                operation.target.startswith("00_inbox/manifests/")
            ):
                raise ValueError("A registration batch may mutate only artifact manifests")

    def _require_stager(self) -> StagedReplacement:
        if self._stager is None:
            raise RuntimeError("The ingestion batch stager is unavailable")
        return self._stager

    def _require_projection(self) -> SQLiteProjection:
        if self._projection is None:
            raise RuntimeError("The ingestion batch projection is unavailable")
        return self._projection

    def _require_projection_report(self) -> RebuildReport:
        if self._projection_report is None:
            raise RuntimeError("The ingestion batch projection preflight is unavailable")
        return self._projection_report

    def _require_operation_cache(self) -> _transaction_engine._OperationCache:
        if self._operation_cache is None:
            raise RuntimeError("The ingestion batch operation cache is unavailable")
        return self._operation_cache

    def _close(self, *, raise_heartbeat_failure: bool) -> None:
        if self._closed:
            return
        self._closed = True
        heartbeat_failure: Exception | None = None
        try:
            if self._projection is not None:
                self._projection._end_locked_operation()
        finally:
            if self._operation_cache is not None:
                self._operation_cache.clear()
            self._engine._operation_cache = None
            if self._heartbeat is not None:
                heartbeat_failure = self._heartbeat.stop()
            if self._lock is not None:
                with suppress(LockFenceError):
                    self._lock.release()
        if heartbeat_failure is not None and raise_heartbeat_failure:
            raise LockFenceError(
                "The transaction heartbeat could not refresh its lease"
            ) from heartbeat_failure


def register(
    context_root: Path,
    request: RegisterRequest,
    *,
    policy: IngestionPolicy | None = None,
    session_id: str | None = None,
) -> RegistrationResult:
    """Register one raw artifact using the default ingestion service."""

    return IngestionService(context_root, policy=policy).register(request, session_id=session_id)


def list_inbox(
    context_root: Path,
    *,
    statuses: frozenset[ArtifactStatus] | None = None,
) -> InboxListing:
    """List registered artifact manifests."""

    return IngestionService(context_root).list_inbox(statuses=statuses)


def quarantine_info(context_root: Path, artifact_id: str) -> QuarantineInfo:
    """Return location-only quarantine information for one artifact."""

    return IngestionService(context_root).quarantine_info(artifact_id)


def archive_after(
    context_root: Path,
    artifact_id: str,
    receipt: ApplyResult,
    *,
    session_id: str | None = None,
) -> ArchiveResult:
    """Archive one artifact after a committed referencing transaction."""

    return IngestionService(context_root).archive_after(
        artifact_id,
        receipt,
        session_id=session_id,
    )


def _scan_diagnostics(
    primary: _ScannedPath,
    sidecars: tuple[_ScannedPath, ...],
) -> tuple[IngestionDiagnostic, ...]:
    diagnostics = [
        IngestionDiagnostic(reason=reason, path=primary.relative_path)
        for reason in primary.scan.reasons
    ]
    diagnostics.extend(
        IngestionDiagnostic(reason=reason, path=sidecar.relative_path)
        for sidecar in sidecars
        for reason in sidecar.scan.reasons
    )
    return tuple(
        sorted(
            set(diagnostics),
            key=lambda diagnostic: (diagnostic.path, diagnostic.reason.value),
        )
    )


def _first_by_content_hash(
    records: tuple[ArtifactRecord, ...],
    content_hash: str,
) -> ArtifactRecord | None:
    return next(
        (record for record in records if record.manifest.content_hash == content_hash),
        None,
    )


def _allocate_artifact_id(
    records: tuple[ArtifactRecord, ...],
    relative_path: str,
    ingested_at: datetime,
) -> str:
    stem = _slug(PurePosixPath(relative_path).stem)
    prefix = f"ART-{ingested_at.astimezone(UTC):%Y%m%d}-{stem}"
    used = {
        int(match.group("sequence"))
        for record in records
        if (match := _ARTIFACT_SEQUENCE.fullmatch(record.manifest.id)) is not None
        and match.group("prefix") == prefix
    }
    for sequence in range(1, 100):
        if sequence not in used:
            return f"{prefix}-{sequence:02d}"
    raise ArtifactStateError("The daily artifact ID sequence is exhausted for this filename.")


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:48].rstrip("-")
    return slug or "artifact"


def _manifest_path(artifact_id: str) -> str:
    return f"00_inbox/manifests/{artifact_id}.json"


def _destination_path(
    zone: str,
    artifact_id: str,
    source_path: str,
    *,
    sidecar_index: int | None = None,
) -> str:
    suffix = PurePosixPath(source_path).suffix.lower()
    safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,16}", suffix) else ""
    sidecar = "" if sidecar_index is None else f"-sidecar-{sidecar_index:02d}"
    return f"{zone}/{artifact_id}{sidecar}{safe_suffix}"


def _archive_destinations(
    manifest: ArtifactManifest,
    metadata: _ManifestMetadata,
) -> tuple[str, ...]:
    return (
        _destination_path("01_processed", manifest.id, metadata.registered_path),
        *(
            _destination_path(
                "01_processed",
                manifest.id,
                sidecar.source_path,
                sidecar_index=index,
            )
            for index, sidecar in enumerate(metadata.sidecars, start=1)
        ),
    )


def _quarantine_move_specs(
    manifest: ArtifactManifest,
    metadata: _ManifestMetadata,
) -> tuple[_MoveSpec, ...]:
    if len(manifest.sidecars) != len(metadata.sidecars):
        raise ArtifactStateError("Quarantine sidecar metadata is inconsistent.")
    return (
        _MoveSpec(
            source=metadata.registered_path,
            destination=manifest.preserved_path,
            content_hash=manifest.content_hash,
        ),
        *(
            _MoveSpec(
                source=sidecar.source_path,
                destination=destination,
                content_hash=sidecar.content_hash,
            )
            for sidecar, destination in zip(metadata.sidecars, manifest.sidecars, strict=True)
        ),
    )


def _archive_move_specs(
    manifest: ArtifactManifest,
    metadata: _ManifestMetadata,
) -> tuple[_MoveSpec, ...]:
    if len(manifest.sidecars) != len(metadata.sidecars):
        raise ArtifactStateError("Archive sidecar metadata is inconsistent.")
    return (
        _MoveSpec(
            source=metadata.registered_path,
            destination=manifest.preserved_path,
            content_hash=manifest.content_hash,
        ),
        *(
            _MoveSpec(
                source=sidecar.source_path,
                destination=destination,
                content_hash=sidecar.content_hash,
            )
            for sidecar, destination in zip(metadata.sidecars, manifest.sidecars, strict=True)
        ),
    )


def _intent_matches_specs(intent: object, specs: tuple[_MoveSpec, ...]) -> bool:
    targets = getattr(intent, "targets", ())
    if len(targets) != len(specs):
        return False
    return all(
        target.kind is IntentTargetKind.MOVE
        and target.target == spec.source
        and target.destination == spec.destination
        and target.content_hash == spec.content_hash
        and target.preimage_hash == spec.content_hash
        for target, spec in zip(targets, specs, strict=True)
    )


def _artifact_reference(manifest: ArtifactManifest) -> str:
    return str(ArtifactReference(manifest.content_hash.removeprefix("sha256:")))


def _record(manifest_path: str, manifest: ArtifactManifest) -> ArtifactRecord:
    return ArtifactRecord(
        manifest_path=manifest_path,
        reference=_artifact_reference(manifest),
        manifest=manifest,
    )


def _updated_manifest(manifest: ArtifactManifest, **updates: object) -> ArtifactManifest:
    values = manifest.model_dump(mode="python")
    values.update(updates)
    return ArtifactManifest.model_validate(values)


def _encode_metadata(metadata: _ManifestMetadata) -> str:
    return json.dumps(
        metadata.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_metadata(notes: str | None) -> _ManifestMetadata | None:
    if notes is None:
        return None
    try:
        return _ManifestMetadata.model_validate_json(notes)
    except (ValidationError, ValueError):
        return None


def _require_metadata(manifest: ArtifactManifest) -> _ManifestMetadata:
    metadata = _decode_metadata(manifest.notes)
    if metadata is None:
        raise ArtifactStateError("The artifact lacks ingestion lifecycle metadata.")
    return metadata


def _proposal_id(created_at: datetime, action: str, artifact_id: str) -> str:
    slug = f"{_slug(action)}-{_slug(artifact_id.removeprefix('ART-'))}-{secrets.token_hex(4)}"
    return f"TXP-{created_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{slug}"


def _move_transaction_id(action: str, artifact_id: str) -> str:
    return f"WP310-{action}-{artifact_id}"


def _normalize_transaction_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ingestion clocks must return timezone-aware datetimes")
    return value.astimezone(UTC).replace(microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_junction(path: Path) -> bool:
    return hasattr(path, "is_junction") and path.is_junction()


__all__ = [
    "IngestionService",
    "archive_after",
    "list_inbox",
    "quarantine_info",
    "register",
]
