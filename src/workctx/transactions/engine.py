"""Deterministic transaction validation, dry-run, apply, and recovery."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Event, Thread
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel

from workctx.adapters.filesystem import (
    CanonicalStore,
    ContextLock,
    ContextZone,
    IntentRecord,
    IntentTargetKind,
    LockFenceError,
    RecoveryInspection,
    RecoveryState,
    StagedDelete,
    StagedMove,
    StagedReplacement,
    StagedWrite,
    has_hand_edits_markdown,
    load_json_model,
    load_markdown_model,
    load_yaml_model,
    render_markdown_bytes,
)
from workctx.adapters.sqlite import RebuildReport, SQLiteProjection
from workctx.domain.artifacts import ArtifactManifest
from workctx.domain.claims import Claim
from workctx.domain.entities import EntityFrontmatter
from workctx.domain.frontmatter import parse_frontmatter
from workctx.domain.observations import Observation
from workctx.domain.references import ArtifactReference, WorkctxUri, parse_durable_reference
from workctx.domain.tasks import Task, TaskType
from workctx.domain.transactions import (
    ArtifactManifestDocumentPayload,
    AuditCreateOperation,
    AuditDeleteGeneratedOperation,
    AuditEvent,
    AuditEventContent,
    AuditMoveOperation,
    AuditOperation,
    AuditUpdateOperation,
    ClaimDocumentPayload,
    CreateOperation,
    DeleteGeneratedOperation,
    DocumentPayload,
    EntityDocumentPayload,
    MoveOperation,
    ObservationDocumentPayload,
    PathAbsentCondition,
    PathExistsCondition,
    PathHashCondition,
    ReferenceExistsCondition,
    SystemActor,
    TaskDocumentPayload,
    TransactionCondition,
    TransactionProposal,
    UpdateOperation,
)
from workctx.transactions.errors import (
    DuplicateProposalError,
    LedgerIntegrityError,
    PostconditionRollbackError,
    PreimageChangedError,
    ProposalValidationError,
    RecoveryPendingError,
    StaleRevisionError,
    TransactionConflictError,
)
from workctx.transactions.ledger import (
    LedgerVerification,
    _read_verified_events,
    _verification,
    append_event,
    audit_summary,
    find_event_by_proposal_id,
    verify_ledger,
)
from workctx.transactions.models import (
    ApplyResult,
    DiagnosticSeverity,
    DryRunResult,
    OperationEffect,
    ProjectionState,
    ProjectionStatus,
    ProposalValidationResult,
    RecoveryResult,
    RecoveryStrategy,
    TransactionDiagnostic,
)
from workctx.validation import Severity, ValidationReport, validate_workspace

_OPERATION_ZONES = (
    ContextZone.INBOX,
    ContextZone.PROCESSED,
    ContextZone.KNOWLEDGE,
    ContextZone.WORK,
    ContextZone.VIEWS,
    ContextZone.OUTBOX,
    ContextZone.INTEGRATIONS,
    ContextZone.META,
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|client[_-]?secret)"
    r"\s*[:=]\s*\S+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}\b")
_KNOWN_TOKEN = re.compile(r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,})")
_TASK_ID = re.compile(r"^TASK-[0-9]{4}-[0-9]{3}(?:-ST[0-9]{2})?$")
_BODY_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9+._-])(?:"
    r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\[\]{}\"'`]*|"
    r"(?i:workctx|artifact|repo):[^\s<>\[\]{}\"'`]+)"
)
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "secret",
    "secret_access_key",
    "token",
}
_PROJECTION_REPAIR = "Run `workctx index rebuild` before projection-backed reads."
_HEARTBEAT_INTERVAL_SECONDS = 30.0

StagedOperation = StagedWrite | StagedMove | StagedDelete
ProjectionFactory = Callable[[Path], SQLiteProjection]
StagerFactory = Callable[[Path], StagedReplacement]
WorkspaceValidator = Callable[..., ValidationReport]
Clock = Callable[[], datetime]
LockFactory = Callable[[Path, str], ContextLock]


@dataclass(frozen=True, slots=True)
class _CompiledProposal:
    writes: tuple[StagedOperation, ...]
    effects: tuple[OperationEffect, ...]
    audit_operations: tuple[AuditOperation, ...]
    final_files: Mapping[str, bytes | None]
    identities: frozenset[str]
    artifact_ids: frozenset[str]
    artifact_digests: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Analysis:
    diagnostics: tuple[TransactionDiagnostic, ...]
    compiled: _CompiledProposal | None

    @property
    def valid(self) -> bool:
        return not any(
            diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in self.diagnostics
        )


@dataclass(slots=True)
class _OperationCache:
    paths: dict[tuple[str, tuple[ContextZone, ...]], Path] = field(default_factory=dict)
    contents: dict[Path, bytes | None] = field(default_factory=dict)
    identities_by_path: dict[Path, frozenset[str]] = field(default_factory=dict)
    indexed_identities: tuple[tuple[str, str], ...] | None = None
    manifests: tuple[tuple[str, ArtifactManifest], ...] | None = None
    outbox_identity_paths: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def clear(self) -> None:
        self.paths.clear()
        self.contents.clear()
        self.identities_by_path.clear()
        self.indexed_identities = None
        self.manifests = None
        self.outbox_identity_paths.clear()


class _HeartbeatLease:
    """Refresh one lock periodically for the lifetime of a public mutation."""

    def __init__(self, lock: ContextLock) -> None:
        self._lock = lock
        self._stopped = Event()
        self._failures: list[Exception] = []
        self._worker: Thread | None = None
        self._token: Token[_HeartbeatLease | None] | None = None

    def start(self) -> None:
        self._token = _ACTIVE_HEARTBEAT.set(self)
        self._worker = Thread(
            target=self._refresh,
            name="workctx-transaction-heartbeat",
            daemon=True,
        )
        try:
            self._worker.start()
        except BaseException:
            _ACTIVE_HEARTBEAT.reset(self._token)
            self._token = None
            self._worker = None
            raise

    def stop(self) -> Exception | None:
        self._stopped.set()
        if self._worker is not None:
            self._worker.join()
            self._worker = None
        if self._token is not None:
            _ACTIVE_HEARTBEAT.reset(self._token)
            self._token = None
        return self._failures[0] if self._failures else None

    def check(self) -> None:
        if self._failures:
            raise LockFenceError(
                "The transaction heartbeat could not refresh its lease"
            ) from self._failures[0]

    def _refresh(self) -> None:
        stale_seconds = self._lock._stale_after.total_seconds()
        interval = min(_HEARTBEAT_INTERVAL_SECONDS, stale_seconds / 4.0)
        while not self._stopped.wait(self._seconds_until_refresh(interval)):
            try:
                self._lock.heartbeat()
            except Exception as exc:
                self._failures.append(exc)
                self._stopped.set()
                return

    def _seconds_until_refresh(self, interval: float) -> float:
        age = max(
            0.0,
            (datetime.now(UTC) - self._lock.metadata.heartbeat_at).total_seconds(),
        )
        return max(0.0, interval - age)


_ACTIVE_HEARTBEAT: ContextVar[_HeartbeatLease | None] = ContextVar(
    "workctx_active_heartbeat",
    default=None,
)


class TransactionEngine:
    """Context-bound implementation of the ADR 0006 transaction protocol."""

    def __init__(
        self,
        context_root: Path,
        *,
        projection_factory: ProjectionFactory = SQLiteProjection,
        stager_factory: StagerFactory = StagedReplacement,
        workspace_validator: WorkspaceValidator = validate_workspace,
        clock: Clock | None = None,
        lock_factory: LockFactory | None = None,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._projection_factory = projection_factory
        self._stager_factory = stager_factory
        self._workspace_validator = workspace_validator
        self._clock = clock or _utc_now
        self._lock_factory = lock_factory or _acquire_context_lock
        self._operation_cache: _OperationCache | None = None

    @property
    def context_root(self) -> Path:
        return self._root

    def validate_proposal(self, proposal: TransactionProposal) -> ProposalValidationResult:
        """Validate a typed proposal without mutating canonical or derived state."""

        analysis = self._analyze_read_only(proposal)
        return ProposalValidationResult(
            proposal_id=proposal.id,
            context_id=proposal.context_id,
            base_revision=proposal.base_revision,
            valid=analysis.valid,
            diagnostics=analysis.diagnostics,
        )

    def dry_run(self, proposal: TransactionProposal) -> DryRunResult:
        """Describe exact ordered effects without acquiring a lock or writing files."""

        analysis = self._analyze_read_only(proposal)
        return DryRunResult(
            proposal_id=proposal.id,
            context_id=proposal.context_id,
            base_revision=proposal.base_revision,
            valid=analysis.valid,
            effects=analysis.compiled.effects if analysis.compiled is not None else (),
            diagnostics=analysis.diagnostics,
        )

    def apply(
        self,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ) -> ApplyResult:
        """Atomically apply one proposal and return its durable commit receipt."""

        lock = self._lock_factory(
            self._root,
            session_id or f"transaction-{proposal.id}",
        )
        stager: StagedReplacement | None = None
        intent: IntentRecord | None = None
        projection: SQLiteProjection | None = None
        operation_cache = _OperationCache()
        self._operation_cache = operation_cache
        heartbeat = _HeartbeatLease(lock)
        try:
            heartbeat.start()
            stager = _run_with_heartbeat(
                lock,
                lambda: self._stager_factory(self._root),
            )
            inspection = _run_with_heartbeat(lock, stager.inspect_recovery)
            if inspection.state is not RecoveryState.CLEAN:
                raise RecoveryPendingError(inspection)

            verification, duplicate = _run_with_heartbeat(
                lock,
                lambda: self._verify_apply_ledger(proposal.id),
            )
            self._raise_apply_conflicts(proposal, verification, duplicate)
            projection = _run_with_heartbeat(
                lock,
                lambda: self._projection_factory(self._root),
            )
            projection._begin_locked_operation()
            projection_report = _run_with_heartbeat(lock, projection.rebuild)
            analysis = _run_with_heartbeat(
                lock,
                lambda: self._analyze(
                    proposal,
                    projection=projection,
                    verification=verification,
                    projection_report=projection_report,
                    check_duplicate=False,
                ),
            )
            if proposal.approval == "required" and not approved:
                analysis = _with_diagnostic(
                    analysis,
                    _diagnostic(
                        "TXN-APPROVAL-REQUIRED",
                        DiagnosticSeverity.ERROR,
                        "The proposal requires explicit runtime approval.",
                        repair_action="Approve the reviewed proposal and retry.",
                    ),
                )
            if not analysis.valid or analysis.compiled is None:
                raise ProposalValidationError(_validation_result(proposal, analysis))
            compiled = analysis.compiled

            lock.verify_fence()
            prepared_intent = _run_with_heartbeat(
                lock,
                lambda: stager.prepare(
                    proposal.id,
                    lock.nonce,
                    compiled.writes,
                    lock=lock,
                ),
            )
            intent = prepared_intent
            if not _intent_matches_operations(
                prepared_intent,
                compiled.audit_operations,
            ):
                event = _run_with_heartbeat(
                    lock,
                    lambda: self._proposal_event(
                        proposal,
                        operations=_intent_audit_operations(prepared_intent),
                        action="apply",
                        result="rolled_back",
                        prev_hash=verification.head_hash,
                    ),
                )
                appended = _run_with_heartbeat(
                    lock,
                    lambda: append_event(self._root, event, lock=lock),
                )
                self._verify_appended_event(event, appended)
                _run_stager_step(
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
                    projection=self._refresh_projection_under_lock(projection, lock),
                    diagnostics=(
                        _diagnostic(
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
            _run_stager_step(
                lock,
                stager,
                lambda: stager.apply(prepared_intent, lock=lock),
            )
            operation_cache.clear()

            postcondition_diagnostics = _run_with_heartbeat(
                lock,
                lambda: self._live_condition_diagnostics(
                    proposal.postconditions,
                    field_name="postconditions",
                ),
            )
            post_report = _run_with_heartbeat(
                lock,
                lambda: self._workspace_validator(
                    self._root,
                    strict=True,
                    freshness_probe=None,
                ),
            )
            if not post_report.ok or any(
                item.severity is DiagnosticSeverity.ERROR for item in postcondition_diagnostics
            ):
                _run_stager_step(
                    lock,
                    stager,
                    lambda: stager.rollback(prepared_intent, lock=lock),
                )
                event = _run_with_heartbeat(
                    lock,
                    lambda: self._proposal_event(
                        proposal,
                        operations=compiled.audit_operations,
                        action="apply",
                        result="rolled_back",
                        prev_hash=verification.head_hash,
                    ),
                )
                appended = _run_with_heartbeat(
                    lock,
                    lambda: append_event(self._root, event, lock=lock),
                )
                self._verify_appended_event(event, appended)
                _run_stager_step(
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
                    projection=_fresh_projection_status(),
                    diagnostics=(
                        *postcondition_diagnostics,
                        *_workspace_diagnostics(post_report),
                    ),
                )
                raise PostconditionRollbackError(recovery_result)

            event = _run_with_heartbeat(
                lock,
                lambda: self._proposal_event(
                    proposal,
                    operations=compiled.audit_operations,
                    action="apply",
                    result="committed",
                    prev_hash=verification.head_hash,
                ),
            )
            appended = _run_with_heartbeat(
                lock,
                lambda: append_event(self._root, event, lock=lock),
            )
            self._verify_appended_event(event, appended)
            _run_stager_step(
                lock,
                stager,
                lambda: stager.finalize_after_audit(proposal.id, lock=lock),
            )
            intent = None

            projection_status = self._refresh_projection_under_lock(projection, lock)
            return ApplyResult(
                proposal_id=proposal.id,
                context_id=proposal.context_id,
                base_revision=proposal.base_revision,
                committed_revision=event.event_hash,
                applied_targets=_applied_targets(compiled.effects),
                ledger_event_id=event.id,
                ledger_event_hash=event.event_hash,
                ledger_source_refs=tuple(event.source_refs),
                projection=projection_status,
            )
        except PostconditionRollbackError:
            raise
        except Exception as exc:
            if intent is not None and stager is not None:
                pending = stager.inspect_recovery()
                if pending.state is not RecoveryState.CLEAN:
                    raise RecoveryPendingError(pending) from exc
            raise
        finally:
            try:
                if projection is not None:
                    projection._end_locked_operation()
            finally:
                operation_cache.clear()
                self._operation_cache = None
                try:
                    heartbeat_failure = heartbeat.stop()
                finally:
                    with suppress(LockFenceError):
                        lock.release()
            if heartbeat_failure is not None and sys.exc_info()[0] is None:
                raise LockFenceError(
                    "The transaction heartbeat could not refresh its lease"
                ) from heartbeat_failure

    def recover(
        self,
        strategy: RecoveryStrategy,
        *,
        proposal: TransactionProposal | None = None,
        transaction_id: str | None = None,
        session_id: str | None = None,
    ) -> RecoveryResult:
        """Resolve an interrupted intent according to the D-031 ledger commit point."""

        lock = self._lock_factory(
            self._root,
            session_id or "transaction-recovery",
        )
        stager: StagedReplacement | None = None
        mutation_started = False
        heartbeat = _HeartbeatLease(lock)
        try:
            heartbeat.start()
            stager = _run_with_heartbeat(
                lock,
                lambda: self._stager_factory(self._root),
            )
            verification = _run_with_heartbeat(lock, lambda: verify_ledger(self._root))
            inspection = _run_with_heartbeat(lock, stager.inspect_recovery)
            if inspection.state is RecoveryState.CLEAN:
                finalized_id = _recovery_selector(proposal, transaction_id)
                return self._already_finalized(strategy, finalized_id, lock=lock)
            if (
                inspection.state
                in {
                    RecoveryState.INVALID_INTENT,
                    RecoveryState.RECOVERY_CONFLICT,
                }
                or inspection.intent is None
            ):
                raise RecoveryPendingError(inspection)

            intent = inspection.intent
            selected_id = _recovery_selector(proposal, transaction_id)
            if selected_id is not None and selected_id != intent.transaction_id:
                raise TransactionConflictError("TXN-RECOVERY-TRANSACTION-MISMATCH")

            existing = _run_with_heartbeat(
                lock,
                lambda: find_event_by_proposal_id(self._root, intent.transaction_id),
            )
            if existing is not None:
                mutation_started = True
                _run_with_heartbeat(
                    lock,
                    lambda: self._finalize_existing_recovery(
                        stager,
                        inspection,
                        existing,
                        lock=lock,
                    ),
                )
                return RecoveryResult(
                    strategy=strategy,
                    outcome="already_finalized",
                    transaction_id=intent.transaction_id,
                    committed_revision=existing.event_hash,
                    applied_targets=(
                        _audit_applied_targets(existing.operations)
                        if existing.result == "committed"
                        else ()
                    ),
                    ledger_event_id=existing.id,
                    ledger_event_hash=existing.event_hash,
                    projection=self._recovery_projection_status(lock),
                )

            # D-031: without an audit event the transaction never crossed the
            # commit point. Recovery may restore preimages but must never apply a
            # staged postimage, regardless of the caller's requested strategy.
            # Build and validate the exact rollback event before touching a
            # preimage so a malformed or unsupported intent remains recoverable.
            event = self._intent_event(intent, prev_hash=verification.head_hash)
            mutation_started = True
            _run_with_heartbeat(
                lock,
                lambda: stager.rollback_recovery(intent, lock=lock),
            )

            current = _run_with_heartbeat(lock, lambda: verify_ledger(self._root))
            if current.head_hash != verification.head_hash:
                raise RecoveryPendingError(_run_with_heartbeat(lock, stager.inspect_recovery))
            appended = _run_with_heartbeat(
                lock,
                lambda: append_event(self._root, event, lock=lock),
            )
            self._verify_appended_event(event, appended)
            _run_with_heartbeat(
                lock,
                lambda: stager.finalize_rollback_after_audit(
                    intent.transaction_id,
                    lock=lock,
                ),
            )
            return RecoveryResult(
                strategy=strategy,
                outcome="rolled_back",
                transaction_id=intent.transaction_id,
                committed_revision=event.event_hash,
                applied_targets=(),
                ledger_event_id=event.id,
                ledger_event_hash=event.event_hash,
                projection=self._recovery_projection_status(lock),
            )
        except RecoveryPendingError:
            raise
        except TransactionConflictError as exc:
            if mutation_started and stager is not None:
                pending = stager.inspect_recovery()
                if pending.state is not RecoveryState.CLEAN:
                    raise RecoveryPendingError(pending) from exc
            raise
        except LedgerIntegrityError:
            raise
        except Exception as exc:
            if stager is not None:
                pending = stager.inspect_recovery()
                if pending.state is not RecoveryState.CLEAN:
                    raise RecoveryPendingError(pending) from exc
            raise
        finally:
            try:
                heartbeat_failure = heartbeat.stop()
            finally:
                with suppress(LockFenceError):
                    lock.release()
            if heartbeat_failure is not None and sys.exc_info()[0] is None:
                raise LockFenceError(
                    "The transaction heartbeat could not refresh its lease"
                ) from heartbeat_failure

    def _analyze_read_only(self, proposal: TransactionProposal) -> _Analysis:
        verification = verify_ledger(self._root)
        projection = self._projection_factory(self._root)
        return self._analyze(
            proposal,
            projection=projection,
            verification=verification,
            projection_report=None,
        )

    def _analyze(
        self,
        proposal: TransactionProposal,
        *,
        projection: SQLiteProjection,
        verification: LedgerVerification,
        projection_report: RebuildReport | None,
        check_duplicate: bool = True,
    ) -> _Analysis:
        diagnostics: list[TransactionDiagnostic] = []
        if proposal.context_id != self._store.context_id:
            diagnostics.append(
                _diagnostic(
                    "TXN-CONTEXT-MISMATCH",
                    DiagnosticSeverity.ERROR,
                    "The proposal belongs to a different context.",
                    path="context_id",
                )
            )
        if check_duplicate and find_event_by_proposal_id(self._root, proposal.id) is not None:
            diagnostics.append(
                _diagnostic(
                    "TXN-DUPLICATE-PROPOSAL",
                    DiagnosticSeverity.ERROR,
                    "The proposal ID already has a ledger event.",
                    path="id",
                )
            )
        if proposal.base_revision != verification.head_hash:
            diagnostics.append(
                _diagnostic(
                    "TXN-STALE-REVISION",
                    DiagnosticSeverity.ERROR,
                    "The proposal base revision is not the current ledger head.",
                    path="base_revision",
                    repair_action="Revalidate the proposal against the current revision.",
                )
            )

        if projection_report is None:
            try:
                readiness = projection.readiness_trigger()
            except Exception:
                projection_ready = False
            else:
                projection_ready = readiness is None
            if not projection_ready:
                diagnostics.append(
                    _diagnostic(
                        "TXN-PROJECTION-NOT-READY",
                        DiagnosticSeverity.ERROR,
                        "The read-only projection is not ready for reference checks.",
                        repair_action=_PROJECTION_REPAIR,
                    )
                )
        elif projection_report.skipped_documents:
            diagnostics.append(
                _diagnostic(
                    "TXN-PROJECTION-INCOMPLETE",
                    DiagnosticSeverity.ERROR,
                    "The projection skipped canonical documents during preflight.",
                    repair_action="Repair skipped documents, rebuild the index, and retry.",
                )
            )

        diagnostics.extend(_secret_diagnostics(proposal))
        if any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics):
            return _Analysis(tuple(diagnostics), None)
        try:
            projection_consistent = self._projection_matches_canonical_identities(projection)
        except Exception:
            projection_consistent = False
        if not projection_consistent:
            diagnostics.append(
                _diagnostic(
                    "TXN-PROJECTION-NOT-READY",
                    DiagnosticSeverity.ERROR,
                    "The projection does not match current canonical identities.",
                    repair_action=_PROJECTION_REPAIR,
                )
            )
            return _Analysis(tuple(diagnostics), None)

        try:
            compiled, compile_diagnostics = self._compile(proposal, projection)
        except Exception:
            diagnostics.append(
                _diagnostic(
                    "TXN-OPERATION-INVALID",
                    DiagnosticSeverity.ERROR,
                    "An operation could not be compiled safely.",
                    repair_action="Review operation paths, hashes, payload types, and parents.",
                )
            )
            return _Analysis(tuple(diagnostics), None)
        diagnostics.extend(compile_diagnostics)
        diagnostics.extend(
            self._condition_diagnostics(
                proposal.preconditions,
                compiled=compiled,
                projection=projection,
                final_state=False,
                field_name="preconditions",
            )
        )
        diagnostics.extend(
            self._condition_diagnostics(
                proposal.postconditions,
                compiled=compiled,
                projection=projection,
                final_state=True,
                field_name="postconditions",
            )
        )
        diagnostics.extend(self._reference_diagnostics(proposal, compiled, projection))
        return _Analysis(tuple(diagnostics), compiled)

    def _compile(
        self,
        proposal: TransactionProposal,
        projection: SQLiteProjection,
    ) -> tuple[_CompiledProposal, tuple[TransactionDiagnostic, ...]]:
        writes: list[StagedOperation] = []
        effects: list[OperationEffect] = []
        audit_operations: list[AuditOperation] = []
        final_files: dict[str, bytes | None] = {}
        identities: dict[str, str] = {}
        identity_locations: dict[str, str] = {}
        artifact_ids: set[str] = set()
        artifact_digests: set[str] = set()
        diagnostics: list[TransactionDiagnostic] = []

        for order, operation in enumerate(proposal.operations):
            if isinstance(operation, (CreateOperation, UpdateOperation)):
                payload_path = f"operations[{order}].payload"
                diagnostics.extend(
                    _payload_structure_diagnostics(operation.payload, path=payload_path)
                )
                staged_write = self._prepare_document(operation.target, operation.payload)
                target_path = self._resolve_operation_path(operation.target)
                current = self._read_operation_file(target_path)
                if isinstance(operation, CreateOperation):
                    if current is not None:
                        raise ValueError("create target exists")
                    if not target_path.parent.is_dir():
                        raise ValueError("create target parent is missing")
                    preimage_hash = None
                    hand_edits = False
                    audit_operation: AuditOperation = AuditCreateOperation(
                        op="create",
                        target=operation.target,
                        postimage_hash=_content_hash(staged_write.content),
                    )
                else:
                    if current is None:
                        raise ValueError("update target is missing")
                    preimage_hash = _content_hash(current)
                    if preimage_hash != operation.expected_hash:
                        raise ValueError("update preimage hash mismatch")
                    self._require_stable_update_identity(
                        operation.target,
                        operation.payload,
                        current,
                    )
                    hand_edits = self._document_has_hand_edits(
                        operation.target,
                        operation.payload,
                        current,
                    )
                    if hand_edits:
                        diagnostics.append(
                            _diagnostic(
                                "TXN-HAND-EDITS",
                                DiagnosticSeverity.WARNING,
                                "The update will canonicalize a hand-edited document.",
                                path=f"operations[{order}].target",
                                repair_action="Review the exact dry-run hash before approval.",
                            )
                        )
                    audit_operation = AuditUpdateOperation(
                        op="update",
                        target=operation.target,
                        preimage_hash=preimage_hash,
                        postimage_hash=_content_hash(staged_write.content),
                    )

                for identity, location in _payload_identities(
                    proposal.context_id,
                    operation.payload,
                ):
                    identity_path = f"{payload_path}{location}"
                    if identity in identities:
                        diagnostics.append(
                            _diagnostic(
                                "TXN-IDENTITY-COLLISION",
                                DiagnosticSeverity.ERROR,
                                "A staged document identity occurs more than once.",
                                path=identity_path,
                                repair_action="Keep exactly one staged owner for each identity.",
                            )
                        )
                        continue
                    identities[identity] = operation.target
                    identity_locations[identity] = identity_path
                writes.append(staged_write)
                postimage_hash = _content_hash(staged_write.content)
                effects.append(
                    OperationEffect(
                        order=order,
                        op=operation.op,
                        target=operation.target,
                        preimage_hash=preimage_hash,
                        postimage_hash=postimage_hash,
                        hand_edits=hand_edits,
                    )
                )
                audit_operations.append(audit_operation)
                final_files[operation.target] = staged_write.content
                continue

            if isinstance(operation, MoveOperation):
                source_path = self._resolve_operation_path(operation.source)
                destination_path = self._resolve_operation_path(operation.destination)
                current = self._read_operation_file(source_path)
                if current is None or _content_hash(current) != operation.expected_hash:
                    raise ValueError("move preimage hash mismatch")
                if self._read_operation_file(destination_path) is not None:
                    raise ValueError("move destination exists")
                if not destination_path.parent.is_dir():
                    raise ValueError("move destination parent is missing")
                _validate_move_destination(
                    operation.source,
                    operation.destination,
                    current,
                )
                writes.append(StagedMove(operation.source, operation.destination))
                effects.append(
                    OperationEffect(
                        order=order,
                        op="move",
                        target=operation.source,
                        destination=operation.destination,
                        preimage_hash=operation.expected_hash,
                        postimage_hash=operation.expected_hash,
                        hand_edits=False,
                    )
                )
                audit_operations.append(
                    AuditMoveOperation(
                        op="move",
                        source=operation.source,
                        destination=operation.destination,
                        content_hash=operation.expected_hash,
                    )
                )
                final_files[operation.source] = None
                final_files[operation.destination] = current
                continue

            if not isinstance(operation, DeleteGeneratedOperation):
                raise TypeError("unsupported transaction operation")
            target_path = self._resolve_operation_path(operation.target)
            current = self._read_operation_file(target_path)
            if current is None or _content_hash(current) != operation.expected_hash:
                raise ValueError("delete preimage hash mismatch")
            writes.append(StagedDelete(operation.target))
            effects.append(
                OperationEffect(
                    order=order,
                    op="delete_generated",
                    target=operation.target,
                    preimage_hash=operation.expected_hash,
                    postimage_hash=None,
                    hand_edits=False,
                )
            )
            audit_operations.append(
                AuditDeleteGeneratedOperation(
                    op="delete_generated",
                    target=operation.target,
                    preimage_hash=operation.expected_hash,
                )
            )
            final_files[operation.target] = None

        for relative_path, manifest in _final_artifact_manifests(final_files):
            if manifest.id in artifact_ids:
                diagnostics.append(
                    _diagnostic(
                        "TXN-ARTIFACT-IDENTITY-COLLISION",
                        DiagnosticSeverity.ERROR,
                        "A final artifact identity has more than one manifest owner.",
                        path=relative_path,
                        repair_action="Keep exactly one final manifest per artifact ID.",
                    )
                )
            artifact_ids.add(manifest.id)
            artifact_digests.add(manifest.content_hash.removeprefix("sha256:"))
            untouched_owners = set(
                self._canonical_manifest_paths(artifact_id=manifest.id)
            ).difference(final_files)
            if untouched_owners:
                diagnostics.append(
                    _diagnostic(
                        "TXN-ARTIFACT-IDENTITY-COLLISION",
                        DiagnosticSeverity.ERROR,
                        "A final artifact identity already has an untouched manifest owner.",
                        path=relative_path,
                        repair_action=(
                            "Update or move the current manifest in the same proposal, or "
                            "allocate a new artifact ID."
                        ),
                    )
                )

        diagnostics.extend(
            self._overlay_identity_collision_diagnostics(
                final_files,
                projection,
                identity_locations=identity_locations,
            )
        )
        return (
            _CompiledProposal(
                writes=tuple(writes),
                effects=tuple(effects),
                audit_operations=tuple(audit_operations),
                final_files=final_files,
                identities=frozenset(identities),
                artifact_ids=frozenset(artifact_ids),
                artifact_digests=frozenset(artifact_digests),
            ),
            tuple(diagnostics),
        )

    def _prepare_document(self, target: str, payload: DocumentPayload) -> StagedWrite:
        if isinstance(payload, EntityDocumentPayload):
            write = self._store.prepare_entity(target, payload.document, payload.body)
            self._remember_prepared_target(write.target)
            return write
        if isinstance(payload, TaskDocumentPayload):
            write = self._store.prepare_task(target, payload.document, payload.body)
            self._remember_prepared_target(write.target)
            return write
        if isinstance(payload, ArtifactManifestDocumentPayload):
            write = self._store.prepare_artifact_manifest(target, payload.document)
            self._remember_prepared_target(write.target)
            return write
        if isinstance(payload, (ClaimDocumentPayload, ObservationDocumentPayload)):
            self._resolve_operation_path(target)
            return StagedWrite(target, render_markdown_bytes(payload.document, payload.body))
        raise TypeError("unsupported document payload")

    def _resolve_operation_path(self, relative_path: str) -> Path:
        path = self._resolve_cached(relative_path, _OPERATION_ZONES)
        ledger = self._resolve_cached(
            "99_meta/audit/ledger.jsonl",
            (ContextZone.META,),
        )
        if ledger.exists() and path.exists():
            try:
                aliases_ledger = path.samefile(ledger)
            except OSError as exc:
                raise ValueError("Unable to verify transaction path identity") from exc
            if aliases_ledger:
                raise ValueError("Transaction paths cannot alias the audit ledger")
        return path

    def _resolve_cached(
        self,
        relative_path: str,
        zones: tuple[ContextZone, ...],
    ) -> Path:
        cache = self._operation_cache
        key = (relative_path, zones)
        if cache is not None and key in cache.paths:
            return cache.paths[key]
        path = self._store.resolve_path(relative_path, zones=zones)
        if cache is not None:
            cache.paths[key] = path
        return path

    def _remember_prepared_target(self, relative_path: str | Path) -> None:
        cache = self._operation_cache
        if cache is None:
            return
        portable = PurePosixPath(relative_path).as_posix()
        cache.paths[(portable, _OPERATION_ZONES)] = self._root.joinpath(
            *PurePosixPath(portable).parts
        )

    def _read_operation_file(self, path: Path) -> bytes | None:
        cache = self._operation_cache
        if cache is not None and path in cache.contents:
            return cache.contents[path]
        content = _read_optional_regular_file(path)
        if cache is not None:
            cache.contents[path] = content
        return content

    def _operation_identities(self, path: Path) -> frozenset[str]:
        cache = self._operation_cache
        if cache is not None and path in cache.identities_by_path:
            return cache.identities_by_path[path]
        content = self._read_operation_file(path)
        identities = (
            frozenset()
            if content is None
            else _markdown_identities(content, self._store.context_id)
        )
        if cache is not None:
            cache.identities_by_path[path] = identities
        return identities

    def _document_has_hand_edits(
        self,
        target: str,
        payload: DocumentPayload,
        current: bytes,
    ) -> bool:
        if isinstance(payload, EntityDocumentPayload):
            return has_hand_edits_markdown(current, EntityFrontmatter)
        if isinstance(payload, TaskDocumentPayload):
            return has_hand_edits_markdown(current, Task)
        if isinstance(payload, ClaimDocumentPayload):
            return has_hand_edits_markdown(current, Claim)
        if isinstance(payload, ObservationDocumentPayload):
            return has_hand_edits_markdown(current, Observation)
        if isinstance(payload, ArtifactManifestDocumentPayload):
            return self._store.artifact_manifest_has_hand_edits(target)
        raise TypeError("unsupported document payload")

    def _require_stable_update_identity(
        self,
        target: str,
        payload: DocumentPayload,
        current: bytes,
    ) -> None:
        if isinstance(payload, ArtifactManifestDocumentPayload):
            existing_manifest = self._store.read_artifact_manifest(target)
            if existing_manifest.id != payload.document.id:
                raise ValueError("artifact identity cannot change during update")
            return
        if isinstance(payload, EntityDocumentPayload):
            existing_identity = self._store.read_entity(target).frontmatter.uri
        elif isinstance(payload, TaskDocumentPayload):
            existing_identity = self._store.read_task(target).frontmatter.uri
        elif isinstance(payload, ClaimDocumentPayload):
            existing_claim = _load_markdown_frontmatter(current, Claim)
            existing_identity = str(WorkctxUri(self._store.context_id, "claim", existing_claim.id))
        elif isinstance(payload, ObservationDocumentPayload):
            existing_observation = _load_markdown_frontmatter(current, Observation)
            existing_identity = str(
                WorkctxUri(self._store.context_id, "observation", existing_observation.id)
            )
        else:
            raise TypeError("unsupported document payload")
        if existing_identity != _payload_identity(self._store.context_id, payload):
            raise ValueError("document identity cannot change during update")

    def _condition_diagnostics(
        self,
        conditions: Sequence[TransactionCondition],
        *,
        compiled: _CompiledProposal,
        projection: SQLiteProjection,
        final_state: bool,
        field_name: str,
    ) -> tuple[TransactionDiagnostic, ...]:
        diagnostics: list[TransactionDiagnostic] = []
        for index, condition in enumerate(conditions):
            try:
                satisfied = self._condition_satisfied(
                    condition,
                    compiled=compiled,
                    projection=projection,
                    final_state=final_state,
                )
            except (OSError, ValueError):
                diagnostics.append(
                    _diagnostic(
                        "TXN-CONDITION-INVALID",
                        DiagnosticSeverity.ERROR,
                        "A transaction condition does not address a safe regular file.",
                        path=f"{field_name}[{index}]",
                    )
                )
                continue
            if not satisfied:
                diagnostics.append(
                    _diagnostic(
                        "TXN-CONDITION-FAILED",
                        DiagnosticSeverity.ERROR,
                        "A transaction condition is not satisfied.",
                        path=f"{field_name}[{index}]",
                        repair_action="Refresh proposal preconditions and retry.",
                    )
                )
        return tuple(diagnostics)

    def _condition_satisfied(
        self,
        condition: TransactionCondition,
        *,
        compiled: _CompiledProposal,
        projection: SQLiteProjection,
        final_state: bool,
    ) -> bool:
        if isinstance(condition, ReferenceExistsCondition):
            return self._reference_exists(
                condition.reference,
                compiled,
                projection,
                include_overlay=final_state,
            )
        content = self._condition_content(condition.path, compiled, final_state=final_state)
        if isinstance(condition, PathExistsCondition):
            return content is not None
        if isinstance(condition, PathAbsentCondition):
            return content is None
        if isinstance(condition, PathHashCondition):
            return content is not None and _content_hash(content) == condition.content_hash
        raise TypeError("unsupported transaction condition")

    def _live_condition_diagnostics(
        self,
        conditions: Sequence[TransactionCondition],
        *,
        field_name: str,
    ) -> tuple[TransactionDiagnostic, ...]:
        diagnostics: list[TransactionDiagnostic] = []
        for index, condition in enumerate(conditions):
            try:
                if isinstance(condition, ReferenceExistsCondition):
                    satisfied = self._live_reference_exists(condition.reference)
                else:
                    content = self._read_operation_file(
                        self._resolve_operation_path(condition.path)
                    )
                    if isinstance(condition, PathExistsCondition):
                        satisfied = content is not None
                    elif isinstance(condition, PathAbsentCondition):
                        satisfied = content is None
                    elif isinstance(condition, PathHashCondition):
                        satisfied = (
                            content is not None and _content_hash(content) == condition.content_hash
                        )
                    else:  # pragma: no cover - closed discriminated union
                        raise TypeError("unsupported transaction condition")
            except (OSError, UnicodeError, ValueError):
                diagnostics.append(
                    _diagnostic(
                        "TXN-CONDITION-INVALID",
                        DiagnosticSeverity.ERROR,
                        "A transaction condition does not address safe canonical state.",
                        path=f"{field_name}[{index}]",
                    )
                )
                continue
            if not satisfied:
                diagnostics.append(
                    _diagnostic(
                        "TXN-CONDITION-FAILED",
                        DiagnosticSeverity.ERROR,
                        "A transaction condition is not satisfied after apply.",
                        path=f"{field_name}[{index}]",
                        repair_action="Refresh proposal conditions and retry.",
                    )
                )
        return tuple(diagnostics)

    def _live_reference_exists(self, reference: str) -> bool:
        parsed = parse_durable_reference(reference)
        if isinstance(parsed, WorkctxUri):
            if parsed.entity_type == "artifact":
                return self._artifact_id_exists(parsed.entity_id)
            identity = str(parsed)
            for zone in (
                ContextZone.KNOWLEDGE,
                ContextZone.WORK,
                ContextZone.OUTBOX,
            ):
                zone_path = self._resolve_cached(zone.value, (zone,))
                for path in sorted(zone_path.rglob("*.md")):
                    if path.name.casefold() == "readme.md":
                        continue
                    relative = path.relative_to(self._root).as_posix()
                    resolved = self._resolve_cached(relative, (zone,))
                    if identity in self._operation_identities(resolved):
                        return True
            return False
        if isinstance(parsed, ArtifactReference):
            return self._artifact_digest_exists(parsed.digest)
        return False

    def _condition_content(
        self,
        relative_path: str,
        compiled: _CompiledProposal,
        *,
        final_state: bool,
    ) -> bytes | None:
        if final_state and relative_path in compiled.final_files:
            return compiled.final_files[relative_path]
        return self._read_operation_file(self._resolve_operation_path(relative_path))

    def _reference_diagnostics(
        self,
        proposal: TransactionProposal,
        compiled: _CompiledProposal,
        projection: SQLiteProjection,
    ) -> tuple[TransactionDiagnostic, ...]:
        diagnostics: list[TransactionDiagnostic] = []
        references: list[tuple[str, str]] = [
            (reference, f"source_refs[{index}]")
            for index, reference in enumerate(proposal.source_refs)
        ]
        for index, operation in enumerate(proposal.operations):
            if isinstance(operation, (CreateOperation, UpdateOperation)):
                references.extend(
                    (reference, f"operations[{index}].payload{location}")
                    for reference, location in _payload_references(operation.payload)
                )
                diagnostics.extend(
                    self._special_document_reference_diagnostics(
                        operation.payload,
                        compiled,
                        projection,
                        path=f"operations[{index}].payload",
                    )
                )
        for reference, path in references:
            try:
                parsed = parse_durable_reference(reference)
            except ValueError:
                diagnostics.append(
                    _diagnostic(
                        "TXN-REFERENCE-INVALID",
                        DiagnosticSeverity.ERROR,
                        "A reference is not a canonical durable URI.",
                        path=path,
                        repair_action="Use a canonical durable reference URI.",
                    )
                )
                continue
            if not isinstance(parsed, (WorkctxUri, ArtifactReference)):
                continue
            if isinstance(parsed, WorkctxUri) and parsed.context_id != self._store.context_id:
                diagnostics.append(
                    _diagnostic(
                        "TXN-REFERENCE-CONTEXT-MISMATCH",
                        DiagnosticSeverity.ERROR,
                        "A local reference belongs to another context.",
                        path=path,
                        repair_action="Use a URI from the active transaction context.",
                    )
                )
                continue
            if not self._reference_exists(reference, compiled, projection):
                diagnostics.append(
                    _diagnostic(
                        "TXN-REFERENCE-MISSING",
                        DiagnosticSeverity.ERROR,
                        "A referenced local document or artifact does not exist.",
                        path=path,
                        repair_action=(
                            "Create the referenced item in the same proposal or workspace."
                        ),
                    )
                )
        return tuple(diagnostics)

    def _special_document_reference_diagnostics(
        self,
        payload: DocumentPayload,
        compiled: _CompiledProposal,
        projection: SQLiteProjection,
        *,
        path: str,
    ) -> tuple[TransactionDiagnostic, ...]:
        diagnostics: list[TransactionDiagnostic] = []
        if (
            isinstance(payload, TaskDocumentPayload)
            and payload.document.task_type is TaskType.SUBTASK
        ):
            parent_id = payload.document.parent_task
            if parent_id is not None:
                parent_uri = str(WorkctxUri(self._store.context_id, "task", parent_id))
                if not self._reference_exists(parent_uri, compiled, projection):
                    diagnostics.append(
                        _missing_typed_reference_diagnostic(f"{path}.document.parent_task")
                    )
        if isinstance(payload, TaskDocumentPayload):
            task = payload.document
            for field_name in ("dependencies", "blockers"):
                for index, value in enumerate(getattr(task, field_name)):
                    relation_path = f"{path}.document.{field_name}[{index}]"
                    if _TASK_ID.fullmatch(value) is not None:
                        target = str(WorkctxUri(self._store.context_id, "task", value))
                    else:
                        try:
                            parsed = parse_durable_reference(value)
                        except ValueError:
                            diagnostics.append(_invalid_task_relation_diagnostic(relation_path))
                            continue
                        if (
                            not isinstance(parsed, WorkctxUri)
                            or parsed.context_id != self._store.context_id
                            or parsed.entity_type != "task"
                        ):
                            diagnostics.append(_invalid_task_relation_diagnostic(relation_path))
                            continue
                        target = str(parsed)
                    if not self._reference_exists(target, compiled, projection):
                        diagnostics.append(_missing_typed_reference_diagnostic(relation_path))
        if isinstance(payload, ClaimDocumentPayload):
            for field_name in ("supersedes", "superseded_by"):
                claim_id = getattr(payload.document, field_name)
                if claim_id is None:
                    continue
                claim_uri = str(WorkctxUri(self._store.context_id, "claim", claim_id))
                if not self._reference_exists(claim_uri, compiled, projection):
                    diagnostics.append(
                        _missing_typed_reference_diagnostic(f"{path}.document.{field_name}")
                    )
        return tuple(diagnostics)

    def _reference_exists(
        self,
        reference: str,
        compiled: _CompiledProposal,
        projection: SQLiteProjection,
        *,
        include_overlay: bool = True,
    ) -> bool:
        parsed = parse_durable_reference(reference)
        if isinstance(parsed, WorkctxUri):
            if parsed.entity_type == "artifact":
                return (
                    include_overlay and parsed.entity_id in compiled.artifact_ids
                ) or self._artifact_id_exists(
                    parsed.entity_id,
                    excluded_paths=(compiled.final_files if include_overlay else ()),
                )
            canonical = str(parsed)
            if include_overlay and self._overlay_identity_exists(canonical, compiled):
                return True
            record = projection.get_document_by_uri(parsed)
            if (
                record is not None
                and (not include_overlay or record.source_path not in compiled.final_files)
                and self._projection_record_is_current(canonical, record.source_path)
            ):
                return True
            return any(
                not include_overlay or source_path not in compiled.final_files
                for source_path in self._canonical_outbox_identity_paths(canonical)
            )
        if isinstance(parsed, ArtifactReference):
            return (
                include_overlay and parsed.digest in compiled.artifact_digests
            ) or self._artifact_digest_exists(
                parsed.digest,
                excluded_paths=(compiled.final_files if include_overlay else ()),
            )
        return False

    def _projection_matches_canonical_identities(
        self,
        projection: SQLiteProjection,
    ) -> bool:
        if projection._locked_preflight_is_current():
            return True
        for identity, source_path in self._canonical_indexed_identities():
            record = projection.get_document_by_uri(identity)
            if record is None or record.source_path != source_path:
                return False
        return True

    def _overlay_identity_collision_diagnostics(
        self,
        final_files: Mapping[str, bytes | None],
        projection: SQLiteProjection,
        *,
        identity_locations: Mapping[str, str],
    ) -> tuple[TransactionDiagnostic, ...]:
        diagnostics: list[TransactionDiagnostic] = []
        overlay_owners: dict[str, list[str]] = {}
        for relative_path, content in final_files.items():
            if content is None or not _is_narrative_path(relative_path):
                continue
            try:
                document_identities = _markdown_identities(
                    content,
                    self._store.context_id,
                )
            except (UnicodeError, ValueError):
                diagnostics.append(
                    _diagnostic(
                        "TXN-DOCUMENT-INVALID",
                        DiagnosticSeverity.ERROR,
                        "A staged narrative document has invalid record identities.",
                        path=relative_path,
                        repair_action="Correct the staged document identities.",
                    )
                )
                continue
            for identity in sorted(document_identities):
                overlay_owners.setdefault(identity, []).append(relative_path)

        for identity in sorted(overlay_owners):
            staged_paths = overlay_owners[identity]
            unique_staged_paths = set(staged_paths)
            diagnostic_path = identity_locations.get(identity, min(unique_staged_paths))
            if len(unique_staged_paths) > 1:
                diagnostics.append(
                    _diagnostic(
                        "TXN-IDENTITY-COLLISION",
                        DiagnosticSeverity.ERROR,
                        "A final staged identity has more than one document owner.",
                        path=diagnostic_path,
                        repair_action="Keep exactly one final owner for each identity.",
                    )
                )

            current_paths = set(self._canonical_outbox_identity_paths(identity))
            record = projection.get_document_by_uri(identity)
            if record is not None and self._projection_record_is_current(
                identity,
                record.source_path,
            ):
                current_paths.add(record.source_path)
            untouched_current_paths = current_paths.difference(final_files)
            if untouched_current_paths:
                diagnostics.append(
                    _diagnostic(
                        "TXN-IDENTITY-COLLISION",
                        DiagnosticSeverity.ERROR,
                        "A final staged identity already has an untouched canonical owner.",
                        path=diagnostic_path,
                        repair_action=(
                            "Update or move the current owner in the same proposal, or use a "
                            "new identity."
                        ),
                    )
                )
        return tuple(diagnostics)

    def _projection_record_is_current(self, identity: str, source_path: str) -> bool:
        try:
            path = self._resolve_cached(
                source_path,
                (ContextZone.KNOWLEDGE, ContextZone.WORK),
            )
            return identity in self._operation_identities(path)
        except (OSError, UnicodeError, ValueError):
            return False

    def _overlay_identity_exists(
        self,
        identity: str,
        compiled: _CompiledProposal,
    ) -> bool:
        for relative_path, content in compiled.final_files.items():
            if content is None or not _is_narrative_path(relative_path):
                continue
            try:
                if identity in _markdown_identities(content, self._store.context_id):
                    return True
            except (UnicodeError, ValueError):
                continue
        return False

    def _canonical_outbox_identity_paths(self, identity: str) -> tuple[str, ...]:
        cache = self._operation_cache
        if cache is not None and identity in cache.outbox_identity_paths:
            return cache.outbox_identity_paths[identity]
        matches: list[str] = []
        zone = ContextZone.OUTBOX
        zone_path = self._resolve_cached(zone.value, (zone,))
        for path in sorted(zone_path.rglob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            relative = path.relative_to(self._root).as_posix()
            try:
                resolved = self._resolve_cached(relative, (zone,))
                if identity in self._operation_identities(resolved):
                    matches.append(relative)
            except (OSError, UnicodeError, ValueError):
                continue
        result = tuple(matches)
        if cache is not None:
            cache.outbox_identity_paths[identity] = result
        return result

    def _canonical_indexed_identities(self) -> tuple[tuple[str, str], ...]:
        cache = self._operation_cache
        if cache is not None and cache.indexed_identities is not None:
            return cache.indexed_identities
        identities: list[tuple[str, str]] = []
        for zone in (ContextZone.KNOWLEDGE, ContextZone.WORK):
            zone_path = self._resolve_cached(zone.value, (zone,))
            for path in sorted(zone_path.rglob("*.md")):
                if path.name.casefold() == "readme.md":
                    continue
                relative = path.relative_to(self._root).as_posix()
                resolved = self._resolve_cached(relative, (zone,))
                identities.extend(
                    (identity, relative) for identity in self._operation_identities(resolved)
                )
        result = tuple(identities)
        if cache is not None:
            cache.indexed_identities = result
        return result

    def _artifact_digest_exists(
        self,
        digest: str,
        *,
        excluded_paths: Iterable[str] = (),
    ) -> bool:
        return bool(
            self._canonical_manifest_paths(
                digest=digest,
                excluded_paths=excluded_paths,
            )
        )

    def _artifact_id_exists(
        self,
        artifact_id: str,
        *,
        excluded_paths: Iterable[str] = (),
    ) -> bool:
        return bool(
            self._canonical_manifest_paths(
                artifact_id=artifact_id,
                excluded_paths=excluded_paths,
            )
        )

    def _canonical_manifest_paths(
        self,
        *,
        artifact_id: str | None = None,
        digest: str | None = None,
        excluded_paths: Iterable[str] = (),
    ) -> tuple[str, ...]:
        excluded = frozenset(excluded_paths)
        matches: list[str] = []
        for relative, manifest in self._canonical_manifests():
            if relative in excluded:
                continue
            if artifact_id is not None and manifest.id == artifact_id:
                matches.append(relative)
            if digest is not None and manifest.content_hash == f"sha256:{digest}":
                matches.append(relative)
        return tuple(dict.fromkeys(matches))

    def _canonical_manifests(self) -> tuple[tuple[str, ArtifactManifest], ...]:
        cache = self._operation_cache
        if cache is not None and cache.manifests is not None:
            return cache.manifests
        directory = self._resolve_cached("00_inbox/manifests", (ContextZone.INBOX,))
        manifests: list[tuple[str, ArtifactManifest]] = []
        if directory.is_dir():
            for path in sorted(directory.iterdir(), key=lambda item: item.name):
                if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                    continue
                relative = path.relative_to(self._root).as_posix()
                try:
                    manifest = self._store.read_artifact_manifest(relative)
                except (OSError, ValueError):
                    continue
                manifests.append((relative, manifest))
        result = tuple(manifests)
        if cache is not None:
            cache.manifests = result
        return result

    def _raise_apply_conflicts(
        self,
        proposal: TransactionProposal,
        verification: LedgerVerification,
        duplicate: AuditEvent | None,
    ) -> None:
        if duplicate is not None:
            raise DuplicateProposalError()
        if proposal.base_revision != verification.head_hash:
            raise StaleRevisionError()

    def _verify_apply_ledger(
        self,
        proposal_id: str,
    ) -> tuple[LedgerVerification, AuditEvent | None]:
        events = _read_verified_events(self._store)
        duplicate = next((event for event in events if event.proposal_id == proposal_id), None)
        return _verification(self._store, events), duplicate

    def _proposal_event(
        self,
        proposal: TransactionProposal,
        *,
        operations: Sequence[AuditOperation],
        action: Literal["apply", "recovery"],
        result: Literal["committed", "rolled_back"],
        prev_hash: str,
    ) -> AuditEvent:
        content = AuditEventContent(
            schema_version=1,
            id=f"AUD-{proposal.id.removeprefix('TXP-')}",
            proposal_id=proposal.id,
            context_id=proposal.context_id,
            timestamp=self._timestamp(),
            actor=proposal.actor,
            action=action,
            result=result,
            base_revision=prev_hash,
            source_refs=proposal.source_refs,
            operations=list(operations),
            prev_hash=prev_hash,
        )
        return AuditEvent.seal(content)

    def _intent_event(
        self,
        intent: IntentRecord,
        *,
        prev_hash: str,
    ) -> AuditEvent:
        actor = SystemActor(
            type="system",
            id="workctx-transaction-recovery",
            agent=None,
            model=None,
        )
        content = AuditEventContent(
            schema_version=1,
            id=f"AUD-{intent.transaction_id.removeprefix('TXP-')}",
            proposal_id=intent.transaction_id,
            context_id=self._store.context_id,
            timestamp=self._timestamp(),
            actor=actor,
            action="recovery",
            result="rolled_back",
            base_revision=prev_hash,
            source_refs=[],
            operations=list(_intent_audit_operations(intent)),
            prev_hash=prev_hash,
        )
        return AuditEvent.seal(content)

    @staticmethod
    def _verify_appended_event(event: AuditEvent, appended: AuditEvent) -> None:
        if appended != event:
            raise TransactionConflictError("TXN-AUDIT-APPEND-UNCONFIRMED")

    def _finalize_existing_recovery(
        self,
        stager: StagedReplacement,
        inspection: RecoveryInspection,
        event: AuditEvent,
        *,
        lock: ContextLock,
    ) -> None:
        if inspection.intent is None:
            raise RecoveryPendingError(inspection)
        if tuple(event.operations) != _intent_audit_operations(inspection.intent):
            raise RecoveryPendingError(inspection)
        if event.result == "committed":
            if inspection.state is not RecoveryState.FULLY_REPLACED_AWAITING_AUDIT:
                raise RecoveryPendingError(inspection)
            stager.finalize_recovery_after_audit(event.proposal_id, lock=lock)
            return
        if inspection.state is not RecoveryState.PREPARED:
            raise RecoveryPendingError(inspection)
        stager.finalize_rollback_after_audit(event.proposal_id, lock=lock)

    def _already_finalized(
        self,
        strategy: RecoveryStrategy,
        transaction_id: str | None,
        *,
        lock: ContextLock,
    ) -> RecoveryResult:
        if transaction_id is None:
            raise TransactionConflictError("TXN-NO-RECOVERY-PENDING")
        event = _run_with_heartbeat(
            lock,
            lambda: find_event_by_proposal_id(self._root, transaction_id),
        )
        if event is None:
            raise TransactionConflictError("TXN-RECOVERY-NOT-FOUND")
        return RecoveryResult(
            strategy=strategy,
            outcome="already_finalized",
            transaction_id=transaction_id,
            committed_revision=event.event_hash,
            applied_targets=(
                _audit_applied_targets(event.operations) if event.result == "committed" else ()
            ),
            ledger_event_id=event.id,
            ledger_event_hash=event.event_hash,
            projection=self._recovery_projection_status(lock),
        )

    def _recovery_projection_status(self, lock: ContextLock) -> ProjectionStatus:
        try:
            projection = _run_with_heartbeat(
                lock,
                lambda: self._projection_factory(self._root),
            )
        except Exception:
            return self._mark_projection_stale(None)
        return self._refresh_projection_under_lock(projection, lock)

    def _refresh_projection_under_lock(
        self,
        projection: SQLiteProjection,
        lock: ContextLock,
    ) -> ProjectionStatus:
        projection._close_operation_reader()
        try:
            return _run_with_heartbeat(
                lock,
                lambda: self._refresh_projection(projection),
            )
        except LockFenceError:
            return self._mark_projection_stale(projection)

    def _refresh_projection(self, projection: SQLiteProjection) -> ProjectionStatus:
        skipped_documents = 0
        try:
            report = projection.rebuild()
            skipped_documents = len(report.skipped_documents)
            if report.skipped_documents:
                raise RuntimeError("projection skipped canonical documents")
            return ProjectionStatus(
                state=ProjectionState.FRESH,
                skipped_documents=0,
            )
        except Exception:
            return self._mark_projection_stale(
                projection,
                skipped_documents=skipped_documents,
            )

    def _mark_projection_stale(
        self,
        projection: SQLiteProjection | None,
        *,
        skipped_documents: int = 0,
    ) -> ProjectionStatus:
        if projection is None:
            with suppress(Exception):
                projection = SQLiteProjection(self._root)
        invalidation_confirmed = False
        if projection is not None:
            with suppress(Exception):
                projection.invalidate()
                invalidation_confirmed = projection.readiness_trigger() is not None
        return _stale_projection_status(
            invalidation_confirmed=invalidation_confirmed,
            skipped_documents=skipped_documents,
        )

    def _timestamp(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Transaction clocks must return timezone-aware datetimes")
        return value.astimezone(UTC)


def validate_proposal(
    context_root: Path,
    proposal: TransactionProposal,
) -> ProposalValidationResult:
    return TransactionEngine(context_root).validate_proposal(proposal)


def dry_run(context_root: Path, proposal: TransactionProposal) -> DryRunResult:
    return TransactionEngine(context_root).dry_run(proposal)


def apply(
    context_root: Path,
    proposal: TransactionProposal,
    *,
    approved: bool = False,
    session_id: str | None = None,
) -> ApplyResult:
    return TransactionEngine(context_root).apply(
        proposal,
        approved=approved,
        session_id=session_id,
    )


def recover(
    context_root: Path,
    strategy: RecoveryStrategy,
    *,
    proposal: TransactionProposal | None = None,
    transaction_id: str | None = None,
    session_id: str | None = None,
) -> RecoveryResult:
    return TransactionEngine(context_root).recover(
        strategy,
        proposal=proposal,
        transaction_id=transaction_id,
        session_id=session_id,
    )


def _recovery_selector(
    proposal: TransactionProposal | None,
    transaction_id: str | None,
) -> str | None:
    proposal_id = proposal.id if proposal is not None else None
    if proposal_id is not None and transaction_id is not None and proposal_id != transaction_id:
        raise TransactionConflictError("TXN-RECOVERY-TRANSACTION-MISMATCH")
    return transaction_id if transaction_id is not None else proposal_id


def _validation_result(
    proposal: TransactionProposal,
    analysis: _Analysis,
) -> ProposalValidationResult:
    return ProposalValidationResult(
        proposal_id=proposal.id,
        context_id=proposal.context_id,
        base_revision=proposal.base_revision,
        valid=analysis.valid,
        diagnostics=analysis.diagnostics,
    )


def _with_diagnostic(analysis: _Analysis, diagnostic: TransactionDiagnostic) -> _Analysis:
    return _Analysis((*analysis.diagnostics, diagnostic), analysis.compiled)


def _diagnostic(
    code: str,
    severity: DiagnosticSeverity,
    message: str,
    *,
    path: str | None = None,
    repair_action: str | None = None,
) -> TransactionDiagnostic:
    return TransactionDiagnostic(
        code=code,
        severity=severity,
        message=message,
        path=path,
        repair_action=repair_action,
    )


def _missing_typed_reference_diagnostic(path: str) -> TransactionDiagnostic:
    return _diagnostic(
        "TXN-REFERENCE-MISSING",
        DiagnosticSeverity.ERROR,
        "A typed document relation points to a missing local document.",
        path=path,
        repair_action="Create the referenced document in the same proposal or workspace.",
    )


def _invalid_task_relation_diagnostic(path: str) -> TransactionDiagnostic:
    return _diagnostic(
        "TXN-TASK-RELATION-INVALID",
        DiagnosticSeverity.ERROR,
        "A task dependency or blocker is not a local task ID or task URI.",
        path=path,
        repair_action="Use TASK-YYYY-NNN[-STNN] or its canonical local task URI.",
    )


def _workspace_diagnostics(report: ValidationReport) -> tuple[TransactionDiagnostic, ...]:
    severity_map = {
        Severity.ERROR: DiagnosticSeverity.ERROR,
        Severity.WARNING: DiagnosticSeverity.WARNING,
        Severity.ADVISORY: DiagnosticSeverity.ADVISORY,
    }
    diagnostics: list[TransactionDiagnostic] = []
    for issue in report.issues:
        message = issue.message
        if issue.code == "CTX-POSSIBLE-SECRET":
            message = "A workspace location contains a secret-looking value."
        diagnostics.append(
            _diagnostic(
                issue.code,
                severity_map[issue.severity],
                message,
                path=issue.path,
                repair_action=issue.repair_action,
            )
        )
    return tuple(diagnostics)


def _secret_diagnostics(proposal: TransactionProposal) -> tuple[TransactionDiagnostic, ...]:
    serialized = proposal.model_dump(mode="json")
    locations = tuple(_secret_locations(serialized))
    return tuple(
        _diagnostic(
            "TXN-POSSIBLE-SECRET",
            DiagnosticSeverity.ERROR,
            "The proposal contains a secret-looking value.",
            path=location,
            repair_action="Remove the value and use an approved secret reference.",
        )
        for location in locations
    )


def _secret_locations(value: object, path: str = "$") -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.casefold() in _SECRET_KEYS and _nonempty_secret_value(child):
                yield child_path
                continue
            yield from _secret_locations(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _secret_locations(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and (
        _PRIVATE_KEY.search(value)
        or _SECRET_ASSIGNMENT.search(value)
        or _BEARER_TOKEN.search(value)
        or _KNOWN_TOKEN.search(value)
    ):
        yield path


def _nonempty_secret_value(value: object) -> bool:
    return value is not None and value != "" and value != () and value is not False


def _payload_identity(context_id: str, payload: DocumentPayload) -> str | None:
    document = payload.document
    if isinstance(document, (EntityFrontmatter, Task)):
        return document.uri
    if isinstance(document, Claim):
        return str(WorkctxUri(context_id, "claim", document.id))
    if isinstance(document, Observation):
        return str(WorkctxUri(context_id, "observation", document.id))
    return None


def _payload_identities(
    context_id: str,
    payload: DocumentPayload,
) -> tuple[tuple[str, str], ...]:
    identities: list[tuple[str, str]] = []
    identity = _payload_identity(context_id, payload)
    if identity is not None:
        identities.append((identity, ".document.id"))
    identities.extend(
        (
            str(WorkctxUri(context_id, "observation", observation.id)),
            f".document.observations[{index}].id",
        )
        for index, observation in _embedded_observations(payload)
    )
    return tuple(identities)


def _embedded_observations(
    payload: DocumentPayload,
) -> tuple[tuple[int, Observation], ...]:
    if not isinstance(payload, EntityDocumentPayload):
        return ()
    extras = payload.document.model_extra or {}
    raw_observations = extras.get("observations", [])
    if not isinstance(raw_observations, list):
        return ()
    observations: list[tuple[int, Observation]] = []
    for index, value in enumerate(raw_observations):
        try:
            observation = Observation.model_validate(value)
        except (RecursionError, TypeError, ValueError):
            continue
        observations.append((index, observation))
    return tuple(observations)


def _payload_structure_diagnostics(
    payload: DocumentPayload,
    *,
    path: str,
) -> tuple[TransactionDiagnostic, ...]:
    if not isinstance(payload, EntityDocumentPayload):
        return ()
    diagnostics: list[TransactionDiagnostic] = []
    extras = payload.document.model_extra or {}
    if "artifact_ref" in extras:
        artifact_ref = extras["artifact_ref"]
        try:
            if not isinstance(artifact_ref, str):
                raise ValueError("artifact_ref must be a string")
            ArtifactReference.parse(artifact_ref)
        except (TypeError, ValueError):
            diagnostics.append(
                _diagnostic(
                    "TXN-REFERENCE-INVALID",
                    DiagnosticSeverity.ERROR,
                    "The evidence artifact reference is not a canonical artifact URI.",
                    path=f"{path}.document.artifact_ref",
                    repair_action="Use artifact://sha256/<64-lowercase-hex>.",
                )
            )

    if "observations" not in extras:
        return tuple(diagnostics)
    raw_observations = extras["observations"]
    if not isinstance(raw_observations, list):
        diagnostics.append(
            _diagnostic(
                "TXN-DOCUMENT-INVALID",
                DiagnosticSeverity.ERROR,
                "Embedded observations must be a list of typed observations.",
                path=f"{path}.document.observations",
                repair_action="Provide a list of Observation documents.",
            )
        )
        return tuple(diagnostics)

    seen: set[str] = set()
    for index, value in enumerate(raw_observations):
        observation_path = f"{path}.document.observations[{index}]"
        try:
            observation = Observation.model_validate(value)
        except (RecursionError, TypeError, ValueError):
            diagnostics.append(
                _diagnostic(
                    "TXN-DOCUMENT-INVALID",
                    DiagnosticSeverity.ERROR,
                    "An embedded observation does not satisfy the Observation model.",
                    path=observation_path,
                    repair_action="Correct the embedded observation fields and references.",
                )
            )
            continue
        if observation.id in seen:
            diagnostics.append(
                _diagnostic(
                    "TXN-IDENTITY-COLLISION",
                    DiagnosticSeverity.ERROR,
                    "An embedded observation identity occurs more than once.",
                    path=f"{observation_path}.id",
                    repair_action="Keep exactly one embedded observation per identity.",
                )
            )
        seen.add(observation.id)
        if str(payload.document.entity_type) == "evidence" and not observation.id.startswith(
            f"{payload.document.id}#OBS-"
        ):
            diagnostics.append(
                _diagnostic(
                    "TXN-DOCUMENT-INVALID",
                    DiagnosticSeverity.ERROR,
                    "An embedded observation must belong to its evidence identity.",
                    path=f"{observation_path}.id",
                    repair_action=("Prefix the observation ID with the containing evidence ID."),
                )
            )
    return tuple(diagnostics)


def _final_artifact_manifests(
    final_files: Mapping[str, bytes | None],
) -> tuple[tuple[str, ArtifactManifest], ...]:
    manifests: list[tuple[str, ArtifactManifest]] = []
    manifest_parent = PurePosixPath("00_inbox/manifests")
    for relative_path in sorted(final_files):
        content = final_files[relative_path]
        path = PurePosixPath(relative_path)
        if content is None or path.parent != manifest_parent:
            continue
        if path.suffix.lower() == ".json":
            manifest = load_json_model(content, ArtifactManifest)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            manifest = load_yaml_model(content, ArtifactManifest)
        else:
            continue
        manifests.append((relative_path, manifest))
    return tuple(manifests)


def _payload_references(payload: DocumentPayload) -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []
    document = payload.document
    if isinstance(document, (EntityFrontmatter, Task)):
        for index, entity_reference in enumerate(document.references):
            references.append((entity_reference.target, f".document.references[{index}].target"))
            for source_index, source in enumerate(entity_reference.source_observations or []):
                references.append(
                    (
                        source,
                        f".document.references[{index}].source_observations[{source_index}]",
                    )
                )
        if isinstance(payload, EntityDocumentPayload):
            extras = document.model_extra or {}
            artifact_ref = extras.get("artifact_ref")
            if isinstance(artifact_ref, str):
                references.append((artifact_ref, ".document.artifact_ref"))
            for observation_index, observation in _embedded_observations(payload):
                prefix = f".document.observations[{observation_index}]"
                references.append((observation.source.ref, f"{prefix}.source.ref"))
                references.extend(
                    (source, f"{prefix}.derived_from[{source_index}]")
                    for source_index, source in enumerate(observation.derived_from)
                )
                for related_index, related_reference in enumerate(observation.related):
                    related_prefix = f"{prefix}.related[{related_index}]"
                    references.append((related_reference.target, f"{related_prefix}.target"))
                    references.extend(
                        (
                            source,
                            f"{related_prefix}.source_observations[{source_index}]",
                        )
                        for source_index, source in enumerate(related_reference.source_observations)
                    )
        if isinstance(document, Task):
            for index, value in enumerate(document.waiting_on):
                if "://" in value:
                    references.append((value, f".document.waiting_on[{index}]"))
            references.extend(
                (source, f".document.source_observations[{index}]")
                for index, source in enumerate(document.source_observations)
            )
            for field_name in ("owner", "requester"):
                value = getattr(document, field_name)
                if value is not None and "://" in value:
                    references.append((value, f".document.{field_name}"))
    elif isinstance(document, Claim):
        references.append((document.subject, ".document.subject"))
        references.extend(
            (source, f".document.source_observations[{index}]")
            for index, source in enumerate(document.source_observations)
        )
    elif isinstance(document, Observation):
        references.append((document.source.ref, ".document.source.ref"))
        references.extend(
            (source, f".document.derived_from[{index}]")
            for index, source in enumerate(document.derived_from)
        )
        for index, related_reference in enumerate(document.related):
            references.append((related_reference.target, f".document.related[{index}].target"))
            references.extend(
                (
                    source,
                    f".document.related[{index}].source_observations[{source_index}]",
                )
                for source_index, source in enumerate(related_reference.source_observations or [])
            )
    if isinstance(
        payload,
        (
            EntityDocumentPayload,
            TaskDocumentPayload,
            ClaimDocumentPayload,
            ObservationDocumentPayload,
        ),
    ):
        references.extend((reference, ".body") for reference in _body_references(payload.body))
    return tuple(references)


def _body_references(body: str) -> tuple[str, ...]:
    return tuple(
        candidate
        for match in _BODY_REFERENCE.finditer(body)
        if (candidate := match.group(0).rstrip(".,;:)"))
    )


def _load_markdown_frontmatter[ModelT: BaseModel](
    data: bytes,
    model_type: type[ModelT],
) -> ModelT:
    return load_markdown_model(data, model_type).frontmatter


def _is_narrative_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    return (
        len(path.parts) > 1
        and path.parts[0] in {"02_knowledge", "03_work", "05_outbox"}
        and path.suffix.lower() == ".md"
    )


def _markdown_identities(content: bytes, context_id: str) -> frozenset[str]:
    raw, _body = parse_frontmatter(content.decode("utf-8"))
    entity_type = raw.get("entity_type")
    identities: set[str] = set()
    if entity_type is not None:
        if entity_type == "task":
            entity: EntityFrontmatter = Task.model_validate(raw)
        else:
            entity = EntityFrontmatter.model_validate(raw)
        identities.add(entity.uri)
        observations = raw.get("observations", [])
        if not isinstance(observations, list):
            raise ValueError("embedded observations must be a list")
        for value in observations:
            observation = Observation.model_validate(value)
            if entity.entity_type == "evidence" and not observation.id.startswith(
                f"{entity.id}#OBS-"
            ):
                raise ValueError("embedded observation does not belong to its evidence")
            identity = str(WorkctxUri(context_id, "observation", observation.id))
            if identity in identities:
                raise ValueError("document contains duplicate record identities")
            identities.add(identity)
        return frozenset(identities)

    identifier = raw.get("id")
    if isinstance(identifier, str) and identifier.startswith("CLM-"):
        claim = Claim.model_validate(raw)
        identities.add(str(WorkctxUri(context_id, "claim", claim.id)))
        return frozenset(identities)
    if isinstance(identifier, str) and "#OBS-" in identifier:
        observation = Observation.model_validate(raw)
        identities.add(str(WorkctxUri(context_id, "observation", observation.id)))
        return frozenset(identities)
    raise ValueError("unsupported indexed Markdown document")


def _validate_move_destination(source: str, destination: str, content: bytes) -> None:
    narrative_zones = {"02_knowledge", "03_work", "05_outbox"}
    source_zone = PurePosixPath(source).parts[0]
    destination_path = PurePosixPath(destination)
    destination_zone = destination_path.parts[0]
    if source_zone not in narrative_zones and destination_zone not in narrative_zones:
        return
    if destination_zone not in narrative_zones or destination_path.suffix.lower() != ".md":
        raise ValueError("canonical narrative moves must retain a narrative destination")

    document: EntityFrontmatter | Task | Claim | Observation
    try:
        document = _load_markdown_frontmatter(content, Task)
    except ValueError:
        try:
            document = _load_markdown_frontmatter(content, Claim)
        except ValueError:
            try:
                document = _load_markdown_frontmatter(content, Observation)
            except ValueError:
                try:
                    document = _load_markdown_frontmatter(content, EntityFrontmatter)
                except ValueError as exc:
                    raise ValueError(
                        "a move into a narrative zone requires a typed document"
                    ) from exc
    if isinstance(document, Task) and destination_zone != "03_work":
        raise ValueError("task moves must remain in the work zone")
    identifier = document.id
    accepted_stems = {identifier, quote(identifier, safe="-._~")}
    if destination_path.stem not in accepted_stems:
        raise ValueError("move destination filename must match the document identity")


def _intent_audit_operations(intent: IntentRecord) -> tuple[AuditOperation, ...]:
    operations: list[AuditOperation] = []
    for target in intent.targets:
        if target.kind is IntentTargetKind.REPLACE:
            if target.content_hash is None:
                raise ValueError("replacement intent is missing its postimage hash")
            if target.preimage_hash is None:
                operations.append(
                    AuditCreateOperation(
                        op="create",
                        target=target.target,
                        postimage_hash=target.content_hash,
                    )
                )
            else:
                operations.append(
                    AuditUpdateOperation(
                        op="update",
                        target=target.target,
                        preimage_hash=target.preimage_hash,
                        postimage_hash=target.content_hash,
                    )
                )
        elif target.kind is IntentTargetKind.MOVE:
            if target.destination is None or target.content_hash is None:
                raise ValueError("move intent is incomplete")
            operations.append(
                AuditMoveOperation(
                    op="move",
                    source=target.target,
                    destination=target.destination,
                    content_hash=target.content_hash,
                )
            )
        else:
            if target.preimage_hash is None:
                raise ValueError("delete intent is missing its preimage hash")
            operations.append(
                AuditDeleteGeneratedOperation(
                    op="delete_generated",
                    target=target.target,
                    preimage_hash=target.preimage_hash,
                )
            )
    return tuple(operations)


def _intent_matches_operations(
    intent: IntentRecord,
    operations: Sequence[AuditOperation],
) -> bool:
    try:
        return _intent_audit_operations(intent) == tuple(operations)
    except ValueError:
        return False


def _applied_targets(effects: Sequence[OperationEffect]) -> tuple[str, ...]:
    targets: list[str] = []
    for effect in effects:
        targets.append(effect.target)
        if effect.destination is not None:
            targets.append(effect.destination)
    return tuple(targets)


def _audit_applied_targets(operations: Sequence[AuditOperation]) -> tuple[str, ...]:
    targets: list[str] = []
    for operation in operations:
        if isinstance(operation, AuditMoveOperation):
            targets.extend((operation.source, operation.destination))
        else:
            targets.append(operation.target)
    return tuple(targets)


def _content_hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _read_optional_regular_file(path: Path) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("transaction paths must be regular files")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("transaction paths must be regular files")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    except FileNotFoundError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fresh_projection_status() -> ProjectionStatus:
    return ProjectionStatus(state=ProjectionState.FRESH)


def _stale_projection_status(
    *,
    invalidation_confirmed: bool,
    skipped_documents: int = 0,
) -> ProjectionStatus:
    return ProjectionStatus(
        state=ProjectionState.STALE,
        diagnostic_code="TXN-PROJECTION-STALE",
        repair_action=_PROJECTION_REPAIR,
        invalidation_confirmed=invalidation_confirmed,
        skipped_documents=skipped_documents,
    )


def _run_with_heartbeat[ResultT](
    lock: ContextLock,
    operation: Callable[[], ResultT],
) -> ResultT:
    """Run one step under the operation-scoped writer-lease refresher."""

    heartbeat = _ACTIVE_HEARTBEAT.get()
    if heartbeat is None or heartbeat._lock is not lock:
        return operation()
    heartbeat.check()
    result = operation()
    heartbeat.check()
    return result


def _run_stager_step[ResultT](
    lock: ContextLock,
    stager: StagedReplacement,
    operation: Callable[[], ResultT],
) -> ResultT:
    """Reuse intent-path validation only within one synchronous stager call."""

    attribute = "_validated_intent_paths"
    missing = object()
    previous = stager.__dict__.get(attribute, missing)
    validate = stager._validated_intent_paths
    cached_intent: IntentRecord | None = None
    cached_paths: object = missing

    def validate_once(intent: IntentRecord) -> object:
        nonlocal cached_intent, cached_paths
        if cached_intent == intent and cached_paths is not missing:
            return cached_paths
        cached_intent = intent
        cached_paths = validate(intent)
        return cached_paths

    setattr(stager, attribute, validate_once)
    try:
        return _run_with_heartbeat(lock, operation)
    finally:
        if previous is missing:
            delattr(stager, attribute)
        else:
            setattr(stager, attribute, previous)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _acquire_context_lock(context_root: Path, session_id: str) -> ContextLock:
    return ContextLock.acquire(context_root, session_id=session_id)


__all__ = [
    "TransactionEngine",
    "apply",
    "audit_summary",
    "dry_run",
    "recover",
    "validate_proposal",
    "verify_ledger",
]
