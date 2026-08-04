"""Fsynced staging, atomic replacement, and recovery inspection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
import unicodedata
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Self

from workctx.adapters.filesystem._paths import canonical_context_root, resolve_context_path
from workctx.adapters.filesystem.lock import ContextLock, LockFenceError, LockMetadata
from workctx.errors import ConflictError, ContextBoundaryError

_CONTENT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_TRANSACTION_DIRECTORY = re.compile(r"^txn-[0-9a-f]{24}$")
_CANONICAL_TARGET_PREFIXES = (
    "00_inbox",
    "01_processed",
    "02_knowledge",
    "03_work",
    "04_views",
    "05_outbox",
    "90_integrations",
    "99_meta",
)
_ROOT_TARGETS = ("context.yaml",)

ReplaceFunction = Callable[[Path, Path], None]
UnlinkFunction = Callable[[Path], None]
SleepFunction = Callable[[float], None]
BeforeAttemptFunction = Callable[[], None]


class StagingError(ConflictError):
    """Base class for recoverable staged-write conflicts."""


class RecoveryRequiredError(StagingError):
    """Raised when an existing intent must be inspected before another write."""


class RecoverableReplaceError(StagingError):
    """Raised after bounded replacement retries are exhausted."""


class InvalidIntentError(StagingError):
    """Raised when an on-disk intent is malformed or unsafe."""


def _system_unlink(path: Path) -> None:
    path.unlink()


@dataclass(frozen=True, slots=True)
class ReplaceRetryPolicy:
    """Bounded exponential backoff for sharing-violation replacement failures."""

    max_attempts: int = 10
    initial_delay_seconds: float = 0.01
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be non-negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least one")


DEFAULT_RETRY_POLICY = ReplaceRetryPolicy()


@dataclass(frozen=True, slots=True)
class StagedWrite:
    """One context-relative target and its complete desired bytes."""

    target: str | Path
    content: bytes


@dataclass(frozen=True, slots=True)
class StagedMove:
    """One atomic move from an existing source to a missing destination."""

    source: str | Path
    destination: str | Path


@dataclass(frozen=True, slots=True)
class StagedDelete:
    """One preimage-preserving removal of an existing canonical file."""

    target: str | Path


class IntentTargetKind(StrEnum):
    """Operation kinds supported by a staged intent target."""

    REPLACE = "replace"
    MOVE = "move"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class IntentTarget:
    """One ordered postimage and its rollback preimage in a durable intent."""

    target: str
    staged: str | None
    content_hash: str | None
    backup: str | None
    preimage_hash: str | None
    kind: IntentTargetKind = IntentTargetKind.REPLACE
    destination: str | None = None

    def __post_init__(self) -> None:
        _validate_intent_target_fields(self)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "target": self.target,
            "staged": self.staged,
            "content_hash": self.content_hash,
            "backup": self.backup,
            "preimage_hash": self.preimage_hash,
        }
        if self.kind is not IntentTargetKind.REPLACE:
            result["kind"] = self.kind.value
            result["destination"] = self.destination
        return result

    @classmethod
    def from_dict(cls, value: object) -> Self:
        legacy_fields = {
            "target",
            "staged",
            "content_hash",
            "backup",
            "preimage_hash",
        }
        extended_fields = legacy_fields | {"kind", "destination"}
        if not isinstance(value, dict):
            raise ValueError("Intent target has an invalid object shape")
        fields = set(value)
        if fields != legacy_fields and fields != extended_fields:
            raise ValueError("Intent target has an invalid object shape")
        target = _required_string(value["target"], "target")
        staged = _optional_string(value["staged"], "staged")
        content_hash = _optional_hash(value["content_hash"], "content_hash")
        backup = _optional_string(value["backup"], "backup")
        preimage_hash = _optional_hash(value["preimage_hash"], "preimage_hash")
        if "kind" in value:
            try:
                kind = IntentTargetKind(value["kind"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Intent target kind is invalid") from exc
            if kind is IntentTargetKind.REPLACE:
                raise ValueError("Replacement intent targets must use the legacy object shape")
            destination = _optional_string(value["destination"], "destination")
        else:
            kind = IntentTargetKind.REPLACE
            destination = None
        return cls(
            target=target,
            staged=staged,
            content_hash=content_hash,
            backup=backup,
            preimage_hash=preimage_hash,
            kind=kind,
            destination=destination,
        )


@dataclass(frozen=True, slots=True)
class IntentRecord:
    """The ADR 0006 write-ahead intent record."""

    schema_version: int
    transaction_id: str
    nonce: str
    targets: tuple[IntentTarget, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "transaction_id": self.transaction_id,
            "nonce": self.nonce,
            "targets": [target.to_dict() for target in self.targets],
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "transaction_id",
            "nonce",
            "targets",
        }:
            raise ValueError("Intent has an invalid object shape")
        schema_version = value["schema_version"]
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 1
        ):
            raise ValueError("Intent schema_version must be 1")
        transaction_id = _required_string(value["transaction_id"], "transaction_id")
        nonce = _required_string(value["nonce"], "nonce")
        if _NONCE.fullmatch(nonce) is None:
            raise ValueError("Intent nonce must be a lowercase 128-bit hexadecimal value")
        raw_targets = value["targets"]
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ValueError("Intent targets must be a non-empty array")
        targets = tuple(IntentTarget.from_dict(item) for item in raw_targets)
        return cls(1, transaction_id, nonce, targets)


class RecoveryState(StrEnum):
    CLEAN = "clean"
    INVALID_INTENT = "invalid_intent"
    PREPARED = "prepared"
    PARTIALLY_APPLIED = "partially_applied"
    FULLY_REPLACED_AWAITING_AUDIT = "fully_replaced_awaiting_audit"
    RECOVERY_CONFLICT = "recovery_conflict"


class TargetRecoveryState(StrEnum):
    POSTIMAGE_PRESENT = "postimage_present"
    STAGED_POSTIMAGE_AVAILABLE = "staged_postimage_available"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class TargetInspection:
    target: str
    expected_hash: str | None
    preimage_hash: str | None
    current_hash: str | None
    staged_hash: str | None
    backup_hash: str | None
    state: TargetRecoveryState
    kind: IntentTargetKind = IntentTargetKind.REPLACE
    destination: str | None = None
    destination_hash: str | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedOperation:
    kind: IntentTargetKind
    target_path: Path
    target: str
    content: bytes | None = None
    destination_path: Path | None = None
    destination: str | None = None


@dataclass(frozen=True, slots=True)
class _ValidatedIntentTarget:
    intent: IntentTarget
    target_path: Path
    staged_path: Path | None
    backup_path: Path | None
    destination_path: Path | None
    transaction_dir: Path


@dataclass(frozen=True, slots=True)
class _OperationSnapshot:
    current_hash: str | None
    destination_hash: str | None
    staged_hash: str | None
    backup_hash: str | None
    state: TargetRecoveryState


@dataclass(frozen=True, slots=True)
class RecoveryInspection:
    """A read-only classification of durable staged-write state."""

    state: RecoveryState
    intent: IntentRecord | None
    targets: tuple[TargetInspection, ...] = ()
    orphan_staging: tuple[str, ...] = ()
    error: str | None = None

    @property
    def applied_targets(self) -> tuple[str, ...]:
        return tuple(
            target.target
            for target in self.targets
            if target.state is TargetRecoveryState.POSTIMAGE_PRESENT
        )

    @property
    def pending_targets(self) -> tuple[str, ...]:
        return tuple(
            target.target
            for target in self.targets
            if target.state is TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE
        )

    @property
    def conflicted_targets(self) -> tuple[str, ...]:
        return tuple(
            target.target for target in self.targets if target.state is TargetRecoveryState.CONFLICT
        )


class StagedReplacement:
    """Prepare and atomically apply an ordered set of canonical file operations."""

    def __init__(
        self,
        context_root: Path,
        *,
        retry_policy: ReplaceRetryPolicy = DEFAULT_RETRY_POLICY,
        replace_function: ReplaceFunction = os.replace,
        unlink_function: UnlinkFunction = _system_unlink,
        sleep_function: SleepFunction = time.sleep,
    ) -> None:
        self.context_root = canonical_context_root(context_root)
        self.retry_policy = retry_policy
        self._replace_function = replace_function
        self._unlink_function = unlink_function
        self._sleep_function = sleep_function
        self._staging_root = resolve_context_path(
            self.context_root,
            "98_state/staging",
            allowed_prefixes=("98_state",),
        )
        # The resolved staging root proves these fixed descendants' lexical
        # containment. Each use still rejects changed link/junction parents
        # and unsafe leaves before reading or mutating them.
        self._intent_path = self._staging_root / "intent.json"
        self._transactions_root = self._staging_root / "transactions"
        self._resolution_cache_nonce: str | None = None
        self._active_resolution_nonce: str | None = None
        self._resolved_targets: dict[str, Path] = {}
        self._resolved_staging_paths: dict[str, Path] = {}

    def prepare(
        self,
        transaction_id: str,
        nonce: str,
        writes: Iterable[StagedWrite | StagedMove | StagedDelete],
        *,
        lock: ContextLock,
    ) -> IntentRecord:
        """Fsync recovery assets, fence, then durably publish ``intent.json``."""

        if not transaction_id:
            raise ValueError("transaction_id must not be empty")
        if _NONCE.fullmatch(nonce) is None:
            raise ValueError("nonce must be a lowercase 128-bit hexadecimal value")
        self._activate_resolution_cache(lock, expected_nonce=nonce)
        self._ensure_runtime_directories()
        _reject_unsafe_file(self._intent_path, allow_missing=True)
        if self._intent_path.exists():
            raise RecoveryRequiredError("A staged-write intent already requires recovery")

        ordered_operations = tuple(writes)
        if not ordered_operations:
            raise ValueError("At least one staged write is required")
        resolved = self._resolve_operations(ordered_operations)
        stage_id = f"txn-{secrets.token_hex(12)}"
        transaction_dir = resolve_context_path(
            self.context_root,
            f"98_state/staging/transactions/{stage_id}",
            allowed_prefixes=("98_state/staging/transactions",),
        )
        self._remember_staging_path(transaction_dir)
        transaction_dir.mkdir(exist_ok=False)

        targets: list[IntentTarget] = []
        published = False
        try:
            for index, operation in enumerate(resolved):
                _require_plain_parent_chain(self.context_root, operation.target_path)
                if operation.destination_path is not None:
                    _require_plain_parent_chain(
                        self.context_root,
                        operation.destination_path,
                    )
                backup_candidate = transaction_dir / f"{index:08d}.backup"
                backup_relative: str | None = None
                # Bounded memory (D-035): the preimage backup streams in chunks
                # instead of loading moved or deleted files into memory.
                preimage_hash = _copy_optional_regular_file_streaming(
                    operation.target_path, backup_candidate
                )
                if operation.kind is not IntentTargetKind.REPLACE and preimage_hash is None:
                    raise RecoveryRequiredError(
                        f"Operation source disappeared during prepare: {operation.target}"
                    )
                if preimage_hash is not None:
                    if _hash_regular_file(backup_candidate) != preimage_hash:
                        raise StagingError("A staged preimage failed hash verification")
                    backup_relative = backup_candidate.relative_to(self.context_root).as_posix()
                    self._remember_staging_path(backup_candidate)

                staged_relative: str | None = None
                expected_hash: str | None = None
                if operation.kind is IntentTargetKind.REPLACE:
                    if operation.content is None:  # pragma: no cover - resolved invariant
                        raise AssertionError("A replacement must carry content")
                    staged_path = transaction_dir / f"{index:08d}.stage"
                    _write_fsynced(staged_path, operation.content, exclusive=True)
                    expected_hash = _hash_bytes(operation.content)
                    if _hash_regular_file(staged_path) != expected_hash:
                        raise StagingError("A staged postimage failed hash verification")
                    staged_relative = staged_path.relative_to(self.context_root).as_posix()
                    self._remember_staging_path(staged_path)
                elif operation.kind is IntentTargetKind.MOVE:
                    expected_hash = preimage_hash
                targets.append(
                    IntentTarget(
                        target=operation.target,
                        staged=staged_relative,
                        content_hash=expected_hash,
                        backup=backup_relative,
                        preimage_hash=preimage_hash,
                        kind=operation.kind,
                        destination=operation.destination,
                    )
                )
                _require_same_volume(self._staging_root, operation.target_path.parent)
                if operation.destination_path is not None:
                    _require_same_volume(self._staging_root, operation.destination_path.parent)
            _fsync_directory(transaction_dir)
            _fsync_directory(transaction_dir.parent)

            intent = IntentRecord(1, transaction_id, nonce, tuple(targets))
            _verify_lock(self.context_root, lock, expected_nonce=nonce)
            self._publish_intent(intent, transaction_dir)
            published = True
            return intent
        finally:
            if not published and not self._intent_path.exists():
                _remove_owned_directory(transaction_dir)
                self._clear_resolution_cache()

    def apply(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        """Apply postimages in intent order and leave the intent for audit finalization."""

        self._activate_resolution_cache(lock, expected_nonce=intent.nonce)
        durable = self._load_intent()
        if durable != intent:
            raise InvalidIntentError("The durable intent differs from the requested transaction")
        validated = self._validated_intent_paths(durable)
        self._apply_validated(
            validated,
            lock=lock,
            expected_nonce=durable.nonce,
        )
        return durable

    def complete_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        """Complete an interrupted intent under an explicitly verified recovery holder."""

        self._activate_resolution_cache(lock, expected_nonce=None)
        durable = self._load_intent()
        if durable != intent:
            raise InvalidIntentError("The durable intent differs from the requested transaction")
        self._apply_validated(
            self._validated_intent_paths(durable),
            lock=lock,
            expected_nonce=None,
        )
        return durable

    def rollback(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        """Restore every target to its recorded preimage under the intent holder."""

        self._activate_resolution_cache(lock, expected_nonce=intent.nonce)
        durable = self._load_intent()
        if durable != intent:
            raise InvalidIntentError("The durable intent differs from the requested transaction")
        self._rollback_validated(
            self._validated_intent_paths(durable),
            lock=lock,
            expected_nonce=durable.nonce,
        )
        return durable

    def rollback_recovery(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        """Roll back an interrupted intent under an explicitly verified recovery holder."""

        self._activate_resolution_cache(lock, expected_nonce=None)
        durable = self._load_intent()
        if durable != intent:
            raise InvalidIntentError("The durable intent differs from the requested transaction")
        self._rollback_validated(
            self._validated_intent_paths(durable),
            lock=lock,
            expected_nonce=None,
        )
        return durable

    def finalize_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        """Remove a fully applied intent only after the caller's durable audit append."""

        self._activate_resolution_cache(lock, expected_nonce=None)
        self._finalize_applied(
            transaction_id,
            lock=lock,
            expected_nonce_from_intent=True,
        )

    def finalize_recovery_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        """Finalize a completed recovery under the current verified lock holder."""

        self._activate_resolution_cache(lock, expected_nonce=None)
        self._finalize_applied(
            transaction_id,
            lock=lock,
            expected_nonce_from_intent=False,
        )

    def finalize_rollback_after_audit(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
    ) -> None:
        """Finalize a verified rollback after its durable audit record is appended."""

        self._activate_resolution_cache(lock, expected_nonce=None)
        intent = self._load_intent()
        if intent.transaction_id != transaction_id:
            raise InvalidIntentError("The durable intent belongs to another transaction")
        validated = self._validated_intent_paths(intent)
        for operation in validated:
            intent_target = operation.intent
            if intent_target.kind is not IntentTargetKind.REPLACE:
                self._require_operation_parents(operation)
            self._verify_backup(intent_target, operation.backup_path)
            if _hash_optional_regular_file(operation.target_path) != intent_target.preimage_hash:
                raise RecoveryRequiredError(
                    f"Target has not reached its recorded preimage: {intent_target.target}"
                )
            if (
                intent_target.kind is IntentTargetKind.REPLACE
                and operation.staged_path is not None
                and _hash_optional_regular_file(operation.staged_path) != intent_target.content_hash
            ):
                raise RecoveryRequiredError(
                    f"Postimage is unavailable after rollback: {intent_target.target}"
                )
            if (
                intent_target.kind is IntentTargetKind.MOVE
                and operation.destination_path is not None
                and _hash_optional_regular_file(operation.destination_path) is not None
            ):
                raise RecoveryRequiredError(
                    f"Move destination remains after rollback: {intent_target.destination}"
                )
        _verify_lock(self.context_root, lock, expected_nonce=None)
        self._remove_intent_and_staging(validated)

    def _finalize_applied(
        self,
        transaction_id: str,
        *,
        lock: ContextLock,
        expected_nonce_from_intent: bool,
    ) -> None:
        intent = self._load_intent()
        if intent.transaction_id != transaction_id:
            raise InvalidIntentError("The durable intent belongs to another transaction")
        validated = self._validated_intent_paths(intent)
        for operation in validated:
            intent_target = operation.intent
            if intent_target.kind is not IntentTargetKind.REPLACE:
                self._require_operation_parents(operation)
            current_hash = _hash_optional_regular_file(operation.target_path)
            destination_hash = (
                _hash_optional_regular_file(operation.destination_path)
                if operation.destination_path is not None
                else None
            )
            applied = (
                current_hash == intent_target.content_hash
                if intent_target.kind is IntentTargetKind.REPLACE
                else current_hash is None
                and (
                    intent_target.kind is IntentTargetKind.DELETE
                    or destination_hash == intent_target.content_hash
                )
            )
            if not applied:
                raise RecoveryRequiredError(
                    f"Target has not reached its recorded postimage: {intent_target.target}"
                )
        expected_nonce = intent.nonce if expected_nonce_from_intent else None
        _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)
        self._remove_intent_and_staging(validated)

    def _remove_intent_and_staging(
        self,
        validated: tuple[_ValidatedIntentTarget, ...],
    ) -> None:
        _require_plain_parent_chain(self.context_root, self._intent_path)
        self._intent_path.unlink()
        _fsync_directory(self._staging_root)

        transaction_dirs = {operation.transaction_dir for operation in validated}
        for transaction_dir in transaction_dirs:
            _remove_owned_directory(transaction_dir)
        self._clear_resolution_cache()

    def inspect_recovery(self) -> RecoveryInspection:
        """Classify an interrupted staged replacement without changing any files."""

        self._active_resolution_nonce = None
        orphan_staging = self._orphan_transaction_dirs(active=None)
        try:
            _reject_unsafe_file(self._intent_path, allow_missing=True)
        except (ContextBoundaryError, OSError) as exc:
            return RecoveryInspection(
                RecoveryState.INVALID_INTENT,
                None,
                orphan_staging=orphan_staging,
                error=str(exc),
            )
        if not self._intent_path.exists():
            return RecoveryInspection(RecoveryState.CLEAN, None, orphan_staging=orphan_staging)
        try:
            intent = self._load_intent()
            validated = self._validated_intent_paths(intent)
        except (InvalidIntentError, ContextBoundaryError, OSError, ValueError) as exc:
            return RecoveryInspection(
                RecoveryState.INVALID_INTENT,
                None,
                orphan_staging=orphan_staging,
                error=str(exc),
            )

        active_dirs = {operation.transaction_dir.name for operation in validated}
        orphan_staging = self._orphan_transaction_dirs(active=active_dirs)
        inspections: list[TargetInspection] = []
        for operation in validated:
            intent_target = operation.intent
            try:
                snapshot = self._snapshot_operation(operation, require_parents=True)
            except (InvalidIntentError, RecoveryRequiredError, ContextBoundaryError, OSError):
                snapshot = _OperationSnapshot(
                    current_hash=None,
                    destination_hash=None,
                    staged_hash=None,
                    backup_hash=None,
                    state=TargetRecoveryState.CONFLICT,
                )
            inspections.append(
                TargetInspection(
                    target=intent_target.target,
                    expected_hash=intent_target.content_hash,
                    preimage_hash=intent_target.preimage_hash,
                    current_hash=snapshot.current_hash,
                    staged_hash=snapshot.staged_hash,
                    backup_hash=snapshot.backup_hash,
                    state=snapshot.state,
                    kind=intent_target.kind,
                    destination=intent_target.destination,
                    destination_hash=snapshot.destination_hash,
                )
            )

        states = {inspection.state for inspection in inspections}
        if TargetRecoveryState.CONFLICT in states:
            overall = RecoveryState.RECOVERY_CONFLICT
        elif states == {TargetRecoveryState.POSTIMAGE_PRESENT}:
            overall = RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
        elif states == {TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE}:
            overall = RecoveryState.PREPARED
        else:
            overall = RecoveryState.PARTIALLY_APPLIED
        return RecoveryInspection(overall, intent, tuple(inspections), orphan_staging)

    def _apply_validated(
        self,
        validated: tuple[_ValidatedIntentTarget, ...],
        *,
        lock: ContextLock,
        expected_nonce: str | None,
    ) -> None:
        for operation in validated:
            snapshot = self._snapshot_operation(operation, require_parents=True)
            if snapshot.state is TargetRecoveryState.CONFLICT:
                if operation.intent.kind is IntentTargetKind.REPLACE:
                    raise RecoveryRequiredError(
                        f"Target differs from both recorded images: {operation.intent.target}"
                    )
                raise RecoveryRequiredError(
                    f"Target differs from its recorded operation state: {operation.intent.target}"
                )

        _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)
        for operation in validated:
            intent_target = operation.intent
            snapshot = self._snapshot_operation(operation, require_parents=True)
            if snapshot.state is TargetRecoveryState.POSTIMAGE_PRESENT:
                continue
            if snapshot.state is not TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE:
                raise RecoveryRequiredError(
                    f"Target changed during staged operation: {intent_target.target}"
                )
            if intent_target.kind is IntentTargetKind.REPLACE:
                if operation.staged_path is None or intent_target.content_hash is None:
                    raise InvalidIntentError("Replacement intent is missing its postimage")
                self._replace_with_retry(
                    operation.staged_path,
                    operation.target_path,
                    lock=lock,
                    expected_nonce=expected_nonce,
                    expected_source_hash=intent_target.content_hash,
                    expected_target_hash=intent_target.preimage_hash,
                )
            elif intent_target.kind is IntentTargetKind.MOVE:
                if operation.destination_path is None or intent_target.content_hash is None:
                    raise InvalidIntentError("Move intent is missing its destination")
                self._replace_with_retry(
                    operation.target_path,
                    operation.destination_path,
                    lock=lock,
                    expected_nonce=expected_nonce,
                    expected_source_hash=intent_target.content_hash,
                    expected_target_hash=None,
                    revalidate_paths=True,
                )
            else:
                if intent_target.preimage_hash is None:
                    raise InvalidIntentError("Delete intent is missing its preimage")

                def verify_delete_attempt() -> None:
                    _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)

                delete_path = operation.target_path

                def validate_delete_path(path: Path = delete_path) -> None:
                    _revalidate_owned_file(
                        self.context_root,
                        path,
                        allow_missing=True,
                    )

                _unlink_with_retry(
                    operation.target_path,
                    expected_hash=intent_target.preimage_hash,
                    policy=self.retry_policy,
                    unlink_function=self._unlink_function,
                    sleep_function=self._sleep_function,
                    before_validation=validate_delete_path,
                    before_attempt=verify_delete_attempt,
                )
            self._fsync_operation_parents(operation)

    def _rollback_validated(
        self,
        validated: tuple[_ValidatedIntentTarget, ...],
        *,
        lock: ContextLock,
        expected_nonce: str | None,
    ) -> None:
        for operation in validated:
            snapshot = self._snapshot_operation(operation, require_parents=True)
            if snapshot.state is TargetRecoveryState.CONFLICT:
                if operation.intent.kind is IntentTargetKind.REPLACE:
                    raise RecoveryRequiredError(
                        f"Target differs from both recorded images: {operation.intent.target}"
                    )
                raise RecoveryRequiredError(
                    f"Target differs from its recorded operation state: {operation.intent.target}"
                )

        _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)

        def verify_fence_attempt() -> None:
            _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)

        for operation in reversed(validated):
            intent_target = operation.intent
            snapshot = self._snapshot_operation(operation, require_parents=True)
            if snapshot.state is TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE:
                continue
            if snapshot.state is not TargetRecoveryState.POSTIMAGE_PRESENT:
                raise RecoveryRequiredError(
                    f"Target changed during rollback: {intent_target.target}"
                )
            if intent_target.kind is IntentTargetKind.REPLACE:
                if operation.staged_path is None or intent_target.content_hash is None:
                    raise InvalidIntentError("Replacement intent is missing its postimage")
                if snapshot.staged_hash != intent_target.content_hash:
                    postimage = _read_regular_file(operation.target_path)
                    if _hash_bytes(postimage) != intent_target.content_hash:
                        raise RecoveryRequiredError(
                            f"Target changed during rollback: {intent_target.target}"
                        )
                    self._publish_rebuilt_postimage(
                        operation.staged_path,
                        postimage,
                        expected_hash=intent_target.content_hash,
                        lock=lock,
                        expected_nonce=expected_nonce,
                    )
                if intent_target.preimage_hash is None:
                    _unlink_with_retry(
                        operation.target_path,
                        expected_hash=intent_target.content_hash,
                        policy=self.retry_policy,
                        unlink_function=self._unlink_function,
                        sleep_function=self._sleep_function,
                        before_attempt=verify_fence_attempt,
                    )
                else:
                    if operation.backup_path is None:
                        raise InvalidIntentError(
                            f"Preimage backup is unavailable for target {intent_target.target}"
                        )
                    self._restore_backup(
                        operation,
                        expected_target_hash=intent_target.content_hash,
                        lock=lock,
                        expected_nonce=expected_nonce,
                    )
            elif intent_target.kind is IntentTargetKind.MOVE:
                if operation.destination_path is None or intent_target.content_hash is None:
                    raise InvalidIntentError("Move intent is missing its destination")
                self._replace_with_retry(
                    operation.destination_path,
                    operation.target_path,
                    lock=lock,
                    expected_nonce=expected_nonce,
                    expected_source_hash=intent_target.content_hash,
                    expected_target_hash=None,
                    revalidate_paths=True,
                )
            else:
                if operation.backup_path is None or intent_target.preimage_hash is None:
                    raise InvalidIntentError(
                        f"Preimage backup is unavailable for target {intent_target.target}"
                    )
                self._restore_backup(
                    operation,
                    expected_target_hash=None,
                    lock=lock,
                    expected_nonce=expected_nonce,
                )
            self._fsync_operation_parents(operation)

    def _snapshot_operation(
        self,
        operation: _ValidatedIntentTarget,
        *,
        require_parents: bool,
    ) -> _OperationSnapshot:
        intent_target = operation.intent
        if require_parents:
            self._require_operation_parents(operation)
        current_hash = _hash_optional_regular_file(operation.target_path)
        destination_hash = (
            _hash_optional_regular_file(operation.destination_path)
            if operation.destination_path is not None
            else None
        )
        staged_hash = (
            _hash_optional_regular_file(operation.staged_path)
            if operation.staged_path is not None
            else None
        )
        backup_hash = (
            _hash_optional_regular_file(operation.backup_path)
            if operation.backup_path is not None
            else None
        )
        self._verify_backup(intent_target, operation.backup_path)
        if intent_target.kind is IntentTargetKind.REPLACE:
            if current_hash == intent_target.content_hash:
                state = TargetRecoveryState.POSTIMAGE_PRESENT
            elif current_hash == intent_target.preimage_hash:
                if staged_hash != intent_target.content_hash:
                    raise InvalidIntentError(
                        f"Staged postimage is unavailable for target {intent_target.target}"
                    )
                state = TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE
            else:
                state = TargetRecoveryState.CONFLICT
        elif intent_target.kind is IntentTargetKind.MOVE:
            if current_hash is None and destination_hash == intent_target.content_hash:
                state = TargetRecoveryState.POSTIMAGE_PRESENT
            elif current_hash == intent_target.preimage_hash and destination_hash is None:
                state = TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE
            else:
                state = TargetRecoveryState.CONFLICT
        elif current_hash is None:
            state = TargetRecoveryState.POSTIMAGE_PRESENT
        elif current_hash == intent_target.preimage_hash:
            state = TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE
        else:
            state = TargetRecoveryState.CONFLICT
        return _OperationSnapshot(
            current_hash=current_hash,
            destination_hash=destination_hash,
            staged_hash=staged_hash,
            backup_hash=backup_hash,
            state=state,
        )

    def _require_operation_parents(self, operation: _ValidatedIntentTarget) -> None:
        intent_target = operation.intent
        if operation.staged_path is not None:
            _require_plain_parent_chain(self.context_root, operation.staged_path)
        if operation.backup_path is not None:
            _require_plain_parent_chain(self.context_root, operation.backup_path)
        if intent_target.kind is IntentTargetKind.REPLACE:
            try:
                _require_plain_parent_chain(self.context_root, operation.target_path)
            except (ContextBoundaryError, OSError) as exc:
                raise RecoveryRequiredError(
                    f"Target parent is unavailable for {intent_target.target}"
                ) from exc
        else:
            try:
                _revalidate_owned_file(
                    self.context_root,
                    operation.target_path,
                    allow_missing=True,
                )
            except (ContextBoundaryError, OSError) as exc:
                raise RecoveryRequiredError(
                    f"Target parent is unavailable for {intent_target.target}"
                ) from exc
            if operation.destination_path is not None:
                try:
                    _revalidate_owned_file(
                        self.context_root,
                        operation.destination_path,
                        allow_missing=True,
                    )
                except (ContextBoundaryError, OSError) as exc:
                    raise RecoveryRequiredError(
                        f"Move destination parent is unavailable for {intent_target.destination}"
                    ) from exc
        if not _target_parent_is_usable(self._staging_root, operation.target_path.parent):
            raise RecoveryRequiredError(f"Target parent is unavailable for {intent_target.target}")
        if operation.destination_path is not None and not _target_parent_is_usable(
            self._staging_root, operation.destination_path.parent
        ):
            raise RecoveryRequiredError(
                f"Move destination parent is unavailable for {intent_target.destination}"
            )

    def _fsync_operation_parents(self, operation: _ValidatedIntentTarget) -> None:
        _fsync_directory(operation.target_path.parent)
        if (
            operation.destination_path is not None
            and operation.destination_path.parent != operation.target_path.parent
        ):
            _fsync_directory(operation.destination_path.parent)

    def _restore_backup(
        self,
        operation: _ValidatedIntentTarget,
        *,
        expected_target_hash: str | None,
        lock: ContextLock,
        expected_nonce: str | None,
    ) -> None:
        intent_target = operation.intent
        if operation.backup_path is None or intent_target.preimage_hash is None:
            raise InvalidIntentError(
                f"Preimage backup is unavailable for target {intent_target.target}"
            )
        preimage = _read_regular_file(operation.backup_path)
        if _hash_bytes(preimage) != intent_target.preimage_hash:
            raise InvalidIntentError(
                f"Preimage backup is unavailable for target {intent_target.target}"
            )
        rollback_path = operation.backup_path.with_suffix(".rollback")
        _reject_unsafe_file(rollback_path, allow_missing=True)
        _write_fsynced(rollback_path, preimage, exclusive=False)
        self._replace_with_retry(
            rollback_path,
            operation.target_path,
            lock=lock,
            expected_nonce=expected_nonce,
            expected_source_hash=intent_target.preimage_hash,
            expected_target_hash=expected_target_hash,
            revalidate_paths=intent_target.kind is not IntentTargetKind.REPLACE,
        )

    @staticmethod
    def _verify_backup(intent_target: IntentTarget, backup_path: Path | None) -> None:
        if intent_target.preimage_hash is None:
            if backup_path is not None:
                raise InvalidIntentError("A target without a preimage must not have a backup")
            return
        if (
            backup_path is None
            or _hash_optional_regular_file(backup_path) != intent_target.preimage_hash
        ):
            raise InvalidIntentError(
                f"Preimage backup is unavailable for target {intent_target.target}"
            )

    def _activate_resolution_cache(
        self,
        lock: ContextLock,
        *,
        expected_nonce: str | None,
    ) -> None:
        # Cache identity is tied to the acquired lease object and nonce. The
        # existing on-disk fence checks still run before every canonical
        # commit/retry; cached paths receive dynamic parent and leaf checks.
        if not isinstance(lock, ContextLock):
            raise LockFenceError("Staged mutation requires an acquired context lock")
        if lock.context_root != self.context_root:
            raise LockFenceError("The context lock belongs to another context")
        if lock.released:
            raise LockFenceError("The context lock lease has already been released")
        nonce = lock.nonce
        if expected_nonce is not None and not secrets.compare_digest(nonce, expected_nonce):
            raise LockFenceError("The verified context lock does not own the intent nonce")
        if self._resolution_cache_nonce != nonce:
            self._clear_resolution_cache()
            self._resolution_cache_nonce = nonce
        self._active_resolution_nonce = nonce

    def _clear_resolution_cache(self) -> None:
        self._resolution_cache_nonce = None
        self._active_resolution_nonce = None
        self._resolved_targets.clear()
        self._resolved_staging_paths.clear()

    def _resolve_target(self, target: str | Path) -> Path:
        key = os.fspath(target)
        if self._active_resolution_nonce is not None and key in self._resolved_targets:
            return self._resolved_targets[key]
        resolved = _resolve_target(self.context_root, target)
        if self._active_resolution_nonce is not None:
            canonical = resolved.relative_to(self.context_root).as_posix()
            self._resolved_targets[canonical] = resolved
        return resolved

    def _resolve_staging_path(self, relative_path: str) -> Path:
        if (
            self._active_resolution_nonce is not None
            and relative_path in self._resolved_staging_paths
        ):
            return self._resolved_staging_paths[relative_path]
        resolved = resolve_context_path(
            self.context_root,
            relative_path,
            allowed_prefixes=("98_state/staging/transactions",),
        )
        if self._active_resolution_nonce is not None:
            self._resolved_staging_paths[relative_path] = resolved
        return resolved

    def _remember_staging_path(self, path: Path) -> None:
        if self._active_resolution_nonce is None:
            return
        relative = path.relative_to(self.context_root).as_posix()
        self._resolved_staging_paths[relative] = path

    def _ensure_runtime_directories(self) -> None:
        state = self._staging_root.parent
        state.mkdir(exist_ok=True)
        _require_plain_directory(state)
        _reject_nested_context_marker(self.context_root, state)
        _fsync_directory(self.context_root)
        self._staging_root.mkdir(exist_ok=True)
        _require_plain_directory(self._staging_root)
        _reject_nested_context_marker(self.context_root, self._staging_root)
        _fsync_directory(state)
        self._transactions_root.mkdir(exist_ok=True)
        _require_plain_directory(self._transactions_root)
        _reject_nested_context_marker(self.context_root, self._transactions_root)
        _fsync_directory(self._staging_root)

    def _resolve_operations(
        self,
        writes: tuple[StagedWrite | StagedMove | StagedDelete, ...],
    ) -> tuple[_ResolvedOperation, ...]:
        resolved: list[_ResolvedOperation] = []
        collision_keys: set[str] = set()
        legacy_writes_only = all(isinstance(write, StagedWrite) for write in writes)

        def claim(path: str, *, replacement: bool = False) -> None:
            collision_key = unicodedata.normalize("NFC", path).casefold()
            if collision_key in collision_keys:
                if replacement:
                    raise StagingError(f"Duplicate staged target: {path}")
                raise StagingError(f"Duplicate staged operation path: {path}")
            collision_keys.add(collision_key)

        for write in writes:
            if isinstance(write, StagedWrite):
                if not isinstance(write.content, bytes):
                    raise TypeError("Staged content must be bytes")
                target_path = self._resolve_target(write.target)
                if not target_path.parent.is_dir():
                    raise StagingError(f"Target parent directory does not exist: {write.target}")
                _reject_unsafe_file(target_path, allow_missing=True)
                target = target_path.relative_to(self.context_root).as_posix()
                claim(target, replacement=legacy_writes_only)
                resolved.append(
                    _ResolvedOperation(
                        kind=IntentTargetKind.REPLACE,
                        target_path=target_path,
                        target=target,
                        content=write.content,
                    )
                )
            elif isinstance(write, StagedMove):
                source_path = self._resolve_target(write.source)
                destination_path = self._resolve_target(write.destination)
                if not source_path.parent.is_dir() or not destination_path.parent.is_dir():
                    raise StagingError("Move source and destination parents must exist")
                _reject_unsafe_file(source_path, allow_missing=False)
                _reject_unsafe_file(destination_path, allow_missing=True)
                _require_plain_parent_chain(self.context_root, source_path)
                _require_plain_parent_chain(self.context_root, destination_path)
                if destination_path.exists():
                    raise StagingError(f"Move destination already exists: {write.destination}")
                source = source_path.relative_to(self.context_root).as_posix()
                destination = destination_path.relative_to(self.context_root).as_posix()
                claim(source)
                claim(destination)
                resolved.append(
                    _ResolvedOperation(
                        kind=IntentTargetKind.MOVE,
                        target_path=source_path,
                        target=source,
                        destination_path=destination_path,
                        destination=destination,
                    )
                )
            elif isinstance(write, StagedDelete):
                target_path = self._resolve_target(write.target)
                if not target_path.parent.is_dir():
                    raise StagingError(f"Target parent directory does not exist: {write.target}")
                _reject_unsafe_file(target_path, allow_missing=False)
                _require_plain_parent_chain(self.context_root, target_path)
                target = target_path.relative_to(self.context_root).as_posix()
                claim(target)
                resolved.append(
                    _ResolvedOperation(
                        kind=IntentTargetKind.DELETE,
                        target_path=target_path,
                        target=target,
                    )
                )
            else:
                raise TypeError("Staged operations must be writes, moves, or deletes")
        return tuple(resolved)

    def _publish_intent(self, intent: IntentRecord, transaction_dir: Path) -> None:
        temp_path = transaction_dir / "intent.complete.tmp"
        _write_fsynced(temp_path, _encode_intent(intent), exclusive=True)
        _require_plain_parent_chain(self.context_root, self._intent_path)
        descriptor = os.open(self._intent_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        _fsync_directory(self._staging_root)
        _system_replace_with_retry(
            temp_path,
            self._intent_path,
            policy=self.retry_policy,
            sleep_function=self._sleep_function,
        )
        _fsync_directory(self._staging_root)

    def _load_intent(self) -> IntentRecord:
        _require_plain_parent_chain(self.context_root, self._intent_path)
        _reject_unsafe_file(self._intent_path, allow_missing=False)
        try:
            with self._intent_path.open("rb") as stream:
                loaded: Any = json.load(stream, object_pairs_hook=_reject_duplicate_object_keys)
            intent = IntentRecord.from_dict(loaded)
            self._validated_intent_paths(intent)
            return intent
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise InvalidIntentError("The staged-write intent is malformed") from exc

    def _validated_intent_paths(
        self,
        intent: IntentRecord,
    ) -> tuple[_ValidatedIntentTarget, ...]:
        collision_keys: set[str] = set()
        staged_paths: set[str] = set()
        backup_paths: set[str] = set()
        transaction_dir: Path | None = None
        validated: list[_ValidatedIntentTarget] = []
        for index, item in enumerate(intent.targets):
            target_path = self._resolve_target(item.target)
            canonical_target = target_path.relative_to(self.context_root).as_posix()
            if item.target != canonical_target:
                raise InvalidIntentError("Intent target path is not canonical")
            staged_path: Path | None = None
            staged_parent: Path | None = None
            stage_name = f"{index:08d}.stage"
            if item.staged is not None:
                staged_path = self._resolve_staging_path(item.staged)
                relative_staged = PurePosixPath(item.staged)
                if (
                    len(relative_staged.parts) != 5
                    or _TRANSACTION_DIRECTORY.fullmatch(relative_staged.parts[3]) is None
                    or relative_staged.name != stage_name
                    or item.staged != staged_path.relative_to(self.context_root).as_posix()
                ):
                    raise InvalidIntentError("Intent staged path does not use the owned layout")
                staged_parent = staged_path.parent
            backup_path: Path | None = None
            backup_parent: Path | None = None
            if item.backup is not None:
                backup_path = self._resolve_staging_path(item.backup)
                relative_backup = PurePosixPath(item.backup)
                if (
                    len(relative_backup.parts) != 5
                    or _TRANSACTION_DIRECTORY.fullmatch(relative_backup.parts[3]) is None
                    or relative_backup.name != f"{index:08d}.backup"
                    or item.backup != backup_path.relative_to(self.context_root).as_posix()
                ):
                    raise InvalidIntentError("Intent backup path does not use the owned layout")
                backup_parent = backup_path.parent
            owned_parent = staged_parent or backup_parent
            if owned_parent is None:  # pragma: no cover - target-shape validation invariant
                raise InvalidIntentError("Intent target has no owned recovery asset")
            if staged_parent is not None and backup_parent not in {None, staged_parent}:
                raise InvalidIntentError("Intent recovery assets span staging directories")
            if transaction_dir is None:
                transaction_dir = owned_parent
            elif owned_parent != transaction_dir:
                raise InvalidIntentError("Intent targets span multiple staging directories")

            destination_path: Path | None = None
            claimed_paths = [item.target]
            if item.destination is not None:
                destination_path = self._resolve_target(item.destination)
                canonical_destination = destination_path.relative_to(self.context_root).as_posix()
                if item.destination != canonical_destination:
                    raise InvalidIntentError("Intent move destination path is not canonical")
                claimed_paths.append(item.destination)
            for claimed_path in claimed_paths:
                collision_key = unicodedata.normalize("NFC", claimed_path).casefold()
                if collision_key in collision_keys:
                    raise InvalidIntentError("Intent contains duplicate operation paths")
                collision_keys.add(collision_key)

            if item.staged is not None:
                staged_key = unicodedata.normalize("NFC", item.staged).casefold()
                if staged_key in staged_paths or staged_key in backup_paths:
                    raise InvalidIntentError("Intent contains duplicate staged paths")
                staged_paths.add(staged_key)
            if item.backup is not None:
                backup_key = unicodedata.normalize("NFC", item.backup).casefold()
                if backup_key in backup_paths or backup_key in staged_paths:
                    raise InvalidIntentError("Intent contains duplicate backup paths")
                backup_paths.add(backup_key)
            validated.append(
                _ValidatedIntentTarget(
                    intent=item,
                    target_path=target_path,
                    staged_path=staged_path,
                    backup_path=backup_path,
                    destination_path=destination_path,
                    transaction_dir=owned_parent,
                )
            )
        return tuple(validated)

    def _replace_with_retry(
        self,
        source: Path,
        target: Path,
        *,
        lock: ContextLock,
        expected_nonce: str | None,
        expected_source_hash: str,
        expected_target_hash: str | None,
        revalidate_paths: bool = False,
    ) -> None:
        def verify_attempt() -> None:
            if revalidate_paths:
                _revalidate_owned_file(self.context_root, source, allow_missing=False)
                _revalidate_owned_file(self.context_root, target, allow_missing=True)
            _require_source_hash(source, expected_source_hash)
            _require_target_hash(target, expected_target_hash)
            if revalidate_paths:
                _revalidate_owned_file(self.context_root, source, allow_missing=False)
                _revalidate_owned_file(self.context_root, target, allow_missing=True)
            _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)

        _replace_with_retry(
            source,
            target,
            replace_function=self._replace_function,
            policy=self.retry_policy,
            sleep_function=self._sleep_function,
            before_attempt=verify_attempt,
        )

    def _publish_rebuilt_postimage(
        self,
        staged_path: Path,
        payload: bytes,
        *,
        expected_hash: str,
        lock: ContextLock,
        expected_nonce: str | None,
    ) -> None:
        rebuild_path = staged_path.with_name(
            f"{staged_path.stem}.rebuild-{secrets.token_hex(8)}.tmp"
        )
        _write_fsynced(rebuild_path, payload, exclusive=True)
        try:
            if _hash_regular_file(rebuild_path) != expected_hash:
                raise StagingError("A reconstructed postimage failed hash verification")

            def verify_attempt() -> None:
                _require_source_hash(rebuild_path, expected_hash)
                _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)

            _system_replace_with_retry(
                rebuild_path,
                staged_path,
                policy=self.retry_policy,
                sleep_function=self._sleep_function,
                before_attempt=verify_attempt,
            )
            _fsync_directory(staged_path.parent)
        finally:
            with suppress(FileNotFoundError):
                rebuild_path.unlink()

    def _orphan_transaction_dirs(self, active: set[str] | None) -> tuple[str, ...]:
        transactions = resolve_context_path(
            self.context_root,
            "98_state/staging/transactions",
            allowed_prefixes=("98_state/staging/transactions",),
        )
        if not transactions.is_dir():
            return ()
        active_names = active or set()
        return tuple(
            path.relative_to(self.context_root).as_posix()
            for path in sorted(transactions.iterdir(), key=lambda item: item.name)
            if path.is_dir() and path.name not in active_names
        )


def atomic_replace_bytes(
    context_root: Path,
    target: str | Path,
    content: bytes,
    *,
    nonce: str,
    lock: ContextLock,
    retry_policy: ReplaceRetryPolicy = DEFAULT_RETRY_POLICY,
    replace_function: ReplaceFunction = os.replace,
    sleep_function: SleepFunction = time.sleep,
) -> None:
    """Stage, fsync, fence, and atomically replace one canonical file."""

    if _NONCE.fullmatch(nonce) is None:
        raise ValueError("nonce must be a lowercase 128-bit hexadecimal value")
    root = canonical_context_root(context_root)
    staging_root = _ensure_runtime_directories(root)
    intent_path = resolve_context_path(
        root,
        "98_state/staging/intent.json",
        allowed_prefixes=("98_state/staging",),
    )
    _reject_unsafe_file(intent_path, allow_missing=True)
    if intent_path.exists():
        raise RecoveryRequiredError("A staged-write intent already requires recovery")
    target_path = _resolve_target(root, target)
    if not target_path.parent.is_dir():
        raise StagingError(f"Target parent directory does not exist: {target}")
    _reject_unsafe_file(target_path, allow_missing=True)
    _require_same_volume(staging_root, target_path.parent)
    temp_path = staging_root / f"single-{secrets.token_hex(12)}.stage"
    _write_fsynced(temp_path, content, exclusive=True)
    try:
        _verify_lock(root, lock, expected_nonce=nonce)
        expected_source_hash = _hash_bytes(content)

        def verify_attempt() -> None:
            _require_source_hash(temp_path, expected_source_hash)
            _verify_lock(root, lock, expected_nonce=nonce)

        _replace_with_retry(
            temp_path,
            target_path,
            replace_function=replace_function,
            policy=retry_policy,
            sleep_function=sleep_function,
            before_attempt=verify_attempt,
        )
        _fsync_directory(target_path.parent)
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()


def atomic_append_line_bytes(
    context_root: Path,
    target: str | Path,
    line: bytes,
    *,
    nonce: str,
    lock: ContextLock,
    retry_policy: ReplaceRetryPolicy = DEFAULT_RETRY_POLICY,
    replace_function: ReplaceFunction = os.replace,
    sleep_function: SleepFunction = time.sleep,
) -> None:
    """Durably append one complete LF-terminated line under a verified lock fence."""

    if _NONCE.fullmatch(nonce) is None:
        raise ValueError("nonce must be a lowercase 128-bit hexadecimal value")
    if not isinstance(line, bytes):
        raise TypeError("Appended line must be bytes")
    if not line or not line.endswith(b"\n") or line.count(b"\n") != 1 or b"\r" in line:
        raise ValueError("Appended bytes must contain exactly one LF-terminated line")

    root = canonical_context_root(context_root)
    target_path = _resolve_target(root, target)
    _verify_lock(root, lock, expected_nonce=nonce)
    _validate_active_intent_if_present(root)
    staging_root = _ensure_runtime_directories(root)
    _ensure_target_parent_directories(
        root,
        target_path,
        lock=lock,
        expected_nonce=nonce,
    )
    _revalidate_owned_file(root, target_path, allow_missing=True)
    _require_same_volume(staging_root, target_path.parent)
    preimage = _read_optional_regular_file(target_path)
    if preimage is not None and preimage and not preimage.endswith(b"\n"):
        raise RecoveryRequiredError("Append target has an incomplete final line")
    expected_target_hash = _hash_bytes(preimage) if preimage is not None else None
    postimage = (preimage or b"") + line
    expected_source_hash = _hash_bytes(postimage)
    temp_path = staging_root / f"append-{secrets.token_hex(12)}.stage"
    try:
        _verify_lock(root, lock, expected_nonce=nonce)
        _write_fsynced(temp_path, postimage, exclusive=True)
        if _hash_regular_file(temp_path) != expected_source_hash:
            raise StagingError("An appended postimage failed hash verification")

        def verify_attempt() -> None:
            _revalidate_owned_file(root, temp_path, allow_missing=False)
            _revalidate_owned_file(root, target_path, allow_missing=True)
            _require_source_hash(temp_path, expected_source_hash)
            _require_target_hash(target_path, expected_target_hash)
            _revalidate_owned_file(root, temp_path, allow_missing=False)
            _revalidate_owned_file(root, target_path, allow_missing=True)
            _verify_lock(root, lock, expected_nonce=nonce)

        _replace_with_retry(
            temp_path,
            target_path,
            replace_function=replace_function,
            policy=retry_policy,
            sleep_function=sleep_function,
            before_attempt=verify_attempt,
        )
        _fsync_directory(target_path.parent)
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()


def inspect_recovery(context_root: Path) -> RecoveryInspection:
    """Convenience wrapper for read-only recovery inspection."""

    return StagedReplacement(context_root).inspect_recovery()


def _resolve_target(root: Path, target: str | Path) -> Path:
    return resolve_context_path(
        root,
        target,
        allowed_prefixes=_CANONICAL_TARGET_PREFIXES,
        allowed_root_files=_ROOT_TARGETS,
    )


def _validate_active_intent_if_present(root: Path) -> None:
    intent_path = root / "98_state" / "staging" / "intent.json"
    _require_plain_existing_parent_chain(root, intent_path)
    _reject_unsafe_file(intent_path, allow_missing=True)
    if intent_path.exists():
        StagedReplacement(root)._load_intent()


def _revalidate_owned_file(root: Path, path: Path, *, allow_missing: bool) -> None:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:  # pragma: no cover - internal path invariant
        raise ContextBoundaryError(f"Owned path escapes the context root: {path}") from exc
    # Callers cache only paths that completed full resolution earlier in the
    # same locked operation. Recheck the lexical zone plus every mutable
    # parent and leaf here so retry-time link substitutions still fail closed.
    if not (
        relative == "98_state/staging"
        or relative.startswith("98_state/staging/")
        or relative in _ROOT_TARGETS
        or any(
            relative == prefix or relative.startswith(f"{prefix}/")
            for prefix in _CANONICAL_TARGET_PREFIXES
        )
    ):
        raise ContextBoundaryError(f"Owned path is outside a writable context zone: {relative}")
    _require_plain_parent_chain(root, path)
    _reject_unsafe_file(path, allow_missing=allow_missing)


def _require_plain_parent_chain(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        if current.is_symlink() or _is_junction(current):
            raise ContextBoundaryError(f"File parent must not be a symlink or junction: {current}")
        if not current.is_dir():
            raise ContextBoundaryError(f"File parent must be a directory: {current}")
        if (current / "context.yaml").is_file():
            relative = current.relative_to(root).as_posix()
            raise ContextBoundaryError(f"Path crosses nested context boundary: {relative}")


def _require_plain_existing_parent_chain(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        if current.is_symlink() or _is_junction(current):
            raise ContextBoundaryError(f"File parent must not be a symlink or junction: {current}")
        if not current.exists():
            return
        if not current.is_dir():
            raise ContextBoundaryError(f"File parent must be a directory: {current}")
        if (current / "context.yaml").is_file():
            relative = current.relative_to(root).as_posix()
            raise ContextBoundaryError(f"Path crosses nested context boundary: {relative}")


def _ensure_runtime_directories(root: Path) -> Path:
    # ``root`` is canonical at both call sites. These are fixed descendants;
    # plain-directory and nested-context checks replace redundant resolution
    # without trusting any filesystem object merely because its name matches.
    state = root / "98_state"
    state.mkdir(exist_ok=True)
    _require_plain_directory(state)
    _reject_nested_context_marker(root, state)
    _fsync_directory(root)
    staging = state / "staging"
    staging.mkdir(exist_ok=True)
    _require_plain_directory(staging)
    _reject_nested_context_marker(root, staging)
    _fsync_directory(state)
    transactions = staging / "transactions"
    transactions.mkdir(exist_ok=True)
    _require_plain_directory(transactions)
    _reject_nested_context_marker(root, transactions)
    _fsync_directory(staging)
    return staging


def _ensure_target_parent_directories(
    root: Path,
    target_path: Path,
    *,
    lock: ContextLock,
    expected_nonce: str,
) -> None:
    # ``target_path`` already passed full boundary and nested-context
    # resolution. Parent creation stays under the held lock and rechecks each
    # component's link/junction, directory, and nested-context state.
    current = root
    for part in target_path.relative_to(root).parts[:-1]:
        current /= part
        if current.exists() or current.is_symlink() or _is_junction(current):
            _require_plain_directory(current)
            _reject_nested_context_marker(root, current)
            continue
        _verify_lock(root, lock, expected_nonce=expected_nonce)
        try:
            current.mkdir()
        except FileExistsError:
            _require_plain_directory(current)
        else:
            _fsync_directory(current.parent)
        _require_plain_directory(current)
        _reject_nested_context_marker(root, current)


def _require_plain_directory(path: Path) -> None:
    if path.is_symlink() or _is_junction(path):
        raise ContextBoundaryError(f"Runtime directory must not be a symlink or junction: {path}")
    if not path.is_dir():
        raise ContextBoundaryError(f"Runtime path must be a directory: {path}")


def _reject_nested_context_marker(root: Path, directory: Path) -> None:
    if (directory / "context.yaml").is_file():
        relative = directory.relative_to(root).as_posix()
        raise ContextBoundaryError(f"Path crosses nested context boundary: {relative}")


def _is_junction(path: Path) -> bool:
    return hasattr(path, "is_junction") and path.is_junction()


def _reject_unsafe_file(path: Path, *, allow_missing: bool) -> None:
    if path.is_symlink() or _is_junction(path):
        raise ContextBoundaryError(f"File must not be a symlink or junction: {path}")
    if path.exists() and not path.is_file():
        raise ContextBoundaryError(f"Path must be a regular file: {path}")
    if not allow_missing and not path.is_file():
        raise ContextBoundaryError(f"Required file is missing: {path}")


def _require_same_volume(staging_root: Path, target_parent: Path) -> None:
    if staging_root.stat().st_dev != target_parent.stat().st_dev:
        raise StagingError("Staged postimage and target must be on the same volume")


def _target_parent_is_usable(staging_root: Path, target_parent: Path) -> bool:
    try:
        return target_parent.is_dir() and staging_root.stat().st_dev == target_parent.stat().st_dev
    except OSError:
        return False


def _write_fsynced(path: Path, payload: bytes, *, exclusive: bool) -> None:
    mode = "xb" if exclusive else "wb"
    with path.open(mode) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _encode_intent(intent: IntentRecord) -> bytes:
    return (json.dumps(intent.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _hash_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _hash_regular_file(path: Path) -> str:
    _reject_unsafe_file(path, allow_missing=False)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _hash_optional_regular_file(path: Path) -> str | None:
    _reject_unsafe_file(path, allow_missing=True)
    if not path.exists():
        return None
    return _hash_regular_file(path)


def _read_regular_file(path: Path) -> bytes:
    _reject_unsafe_file(path, allow_missing=False)
    with path.open("rb") as stream:
        return stream.read()


def _copy_optional_regular_file_streaming(source: Path, dest: Path) -> str | None:
    """Copy ``source`` to ``dest`` in bounded-memory chunks, fsynced.

    Returns the sha256 of the copied bytes, or ``None`` when the source does
    not exist. Applies the same safety checks as ``_read_optional_regular_file``.
    """

    _reject_unsafe_file(source, allow_missing=True)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, dest.open("xb") as writer:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except FileNotFoundError:
        dest.unlink(missing_ok=True)
        return None
    return f"sha256:{digest.hexdigest()}"


def _read_optional_regular_file(path: Path) -> bytes | None:
    _reject_unsafe_file(path, allow_missing=True)
    try:
        with path.open("rb") as stream:
            return stream.read()
    except FileNotFoundError:
        return None


def _replace_with_retry(
    source: Path,
    target: Path,
    *,
    replace_function: ReplaceFunction,
    policy: ReplaceRetryPolicy,
    sleep_function: SleepFunction,
    before_attempt: BeforeAttemptFunction | None = None,
) -> None:
    delay = policy.initial_delay_seconds
    for attempt in range(policy.max_attempts):
        if before_attempt is not None:
            before_attempt()
        try:
            replace_function(source, target)
            return
        except PermissionError as exc:
            if attempt + 1 >= policy.max_attempts:
                raise RecoverableReplaceError(
                    f"Atomic replacement remained unavailable after {policy.max_attempts} attempts"
                ) from exc
            sleep_function(delay)
            delay *= policy.multiplier
    raise AssertionError("Retry loop must return or raise")  # pragma: no cover


def _system_replace_with_retry(
    source: Path,
    target: Path,
    *,
    policy: ReplaceRetryPolicy,
    sleep_function: SleepFunction,
    before_attempt: BeforeAttemptFunction | None = None,
) -> None:
    _replace_with_retry(
        source,
        target,
        replace_function=os.replace,
        policy=policy,
        sleep_function=sleep_function,
        before_attempt=before_attempt,
    )


def _unlink_with_retry(
    target: Path,
    *,
    expected_hash: str,
    policy: ReplaceRetryPolicy,
    unlink_function: UnlinkFunction = _system_unlink,
    sleep_function: SleepFunction,
    before_validation: BeforeAttemptFunction | None = None,
    before_attempt: BeforeAttemptFunction | None = None,
) -> None:
    delay = policy.initial_delay_seconds
    for attempt in range(policy.max_attempts):
        if before_validation is not None:
            before_validation()
        current_hash = _hash_optional_regular_file(target)
        if before_validation is not None:
            before_validation()
        if before_attempt is not None:
            before_attempt()
        if current_hash is None:
            return
        if current_hash != expected_hash:
            raise RecoveryRequiredError(f"Target changed during atomic replacement: {target.name}")
        try:
            unlink_function(target)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if attempt + 1 >= policy.max_attempts:
                raise RecoverableReplaceError(
                    f"Atomic removal remained unavailable after {policy.max_attempts} attempts"
                ) from exc
            sleep_function(delay)
            delay *= policy.multiplier
    raise AssertionError("Retry loop must return or raise")  # pragma: no cover


def _require_target_hash(target: Path, expected_hash: str | None) -> None:
    if _hash_optional_regular_file(target) != expected_hash:
        raise RecoveryRequiredError(f"Target changed during atomic replacement: {target.name}")


def _require_source_hash(source: Path, expected_hash: str) -> None:
    if _hash_optional_regular_file(source) != expected_hash:
        raise InvalidIntentError(f"Staged source changed during atomic replacement: {source.name}")


def _remove_owned_directory(directory: Path) -> None:
    if not directory.exists():
        return
    shutil.rmtree(directory)


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Intent {field_name} must be a non-empty string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name)


def _optional_hash(value: object, field_name: str) -> str | None:
    result = _optional_string(value, field_name)
    if result is not None and _CONTENT_HASH.fullmatch(result) is None:
        raise ValueError(f"Intent target {field_name} is invalid")
    return result


def _validate_intent_target_fields(target: IntentTarget) -> None:
    _required_string(target.target, "target")
    content_hash = _optional_hash(target.content_hash, "content_hash")
    preimage_hash = _optional_hash(target.preimage_hash, "preimage_hash")
    backup = _optional_string(target.backup, "backup")
    staged = _optional_string(target.staged, "staged")
    destination = _optional_string(target.destination, "destination")
    if not isinstance(target.kind, IntentTargetKind):
        raise ValueError("Intent target kind is invalid")
    if (backup is None) != (preimage_hash is None):
        raise ValueError("Intent target backup and preimage_hash must both be null or present")
    if target.kind is IntentTargetKind.REPLACE:
        if staged is None or content_hash is None or destination is not None:
            raise ValueError("Replacement intent target fields are invalid")
        return
    if backup is None or preimage_hash is None or staged is not None:
        raise ValueError("Move and delete intents require one preimage backup")
    if target.kind is IntentTargetKind.MOVE:
        if destination is None or content_hash != preimage_hash:
            raise ValueError("Move intent target fields are invalid")
        return
    if destination is not None or content_hash is not None:
        raise ValueError("Delete intent target fields are invalid")


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Intent JSON contains a duplicate object key: {key}")
        result[key] = value
    return result


def _verify_lock(
    context_root: Path,
    lock: ContextLock,
    *,
    expected_nonce: str | None,
) -> LockMetadata:
    if not isinstance(lock, ContextLock):
        raise LockFenceError("Staged mutation requires an acquired context lock")
    if lock.context_root != context_root:
        raise LockFenceError("The context lock belongs to another context")
    metadata = lock.verify_fence()
    if expected_nonce is not None and not secrets.compare_digest(metadata.nonce, expected_nonce):
        raise LockFenceError("The verified context lock does not own the intent nonce")
    return metadata


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
