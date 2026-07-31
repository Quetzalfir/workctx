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
class IntentTarget:
    """One ordered postimage and its rollback preimage in a durable intent."""

    target: str
    staged: str
    content_hash: str
    backup: str | None
    preimage_hash: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "target": self.target,
            "staged": self.staged,
            "content_hash": self.content_hash,
            "backup": self.backup,
            "preimage_hash": self.preimage_hash,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict) or set(value) != {
            "target",
            "staged",
            "content_hash",
            "backup",
            "preimage_hash",
        }:
            raise ValueError("Intent target has an invalid object shape")
        target = _required_string(value["target"], "target")
        staged = _required_string(value["staged"], "staged")
        content_hash = _required_string(value["content_hash"], "content_hash")
        if _CONTENT_HASH.fullmatch(content_hash) is None:
            raise ValueError("Intent target content_hash is invalid")
        backup = _optional_string(value["backup"], "backup")
        preimage_hash = _optional_string(value["preimage_hash"], "preimage_hash")
        if preimage_hash is not None and _CONTENT_HASH.fullmatch(preimage_hash) is None:
            raise ValueError("Intent target preimage_hash is invalid")
        if (backup is None) != (preimage_hash is None):
            raise ValueError("Intent target backup and preimage_hash must both be null or present")
        return cls(
            target=target,
            staged=staged,
            content_hash=content_hash,
            backup=backup,
            preimage_hash=preimage_hash,
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
    expected_hash: str
    preimage_hash: str | None
    current_hash: str | None
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
    """Prepare and atomically replace an ordered set of canonical files."""

    def __init__(
        self,
        context_root: Path,
        *,
        retry_policy: ReplaceRetryPolicy = DEFAULT_RETRY_POLICY,
        replace_function: ReplaceFunction = os.replace,
        sleep_function: SleepFunction = time.sleep,
    ) -> None:
        self.context_root = canonical_context_root(context_root)
        self.retry_policy = retry_policy
        self._replace_function = replace_function
        self._sleep_function = sleep_function
        self._staging_root = resolve_context_path(
            self.context_root,
            "98_state/staging",
            allowed_prefixes=("98_state",),
        )
        self._intent_path = resolve_context_path(
            self.context_root,
            "98_state/staging/intent.json",
            allowed_prefixes=("98_state/staging",),
        )

    def prepare(
        self,
        transaction_id: str,
        nonce: str,
        writes: Iterable[StagedWrite],
        *,
        lock: ContextLock,
    ) -> IntentRecord:
        """Fsync all postimages, fence, then durably publish ``intent.json``."""

        if not transaction_id:
            raise ValueError("transaction_id must not be empty")
        if _NONCE.fullmatch(nonce) is None:
            raise ValueError("nonce must be a lowercase 128-bit hexadecimal value")
        _ensure_runtime_directories(self.context_root)
        _reject_unsafe_file(self._intent_path, allow_missing=True)
        if self._intent_path.exists():
            raise RecoveryRequiredError("A staged-write intent already requires recovery")

        ordered_writes = tuple(writes)
        if not ordered_writes:
            raise ValueError("At least one staged write is required")
        resolved = self._resolve_writes(ordered_writes)
        stage_id = f"txn-{secrets.token_hex(12)}"
        transaction_dir = resolve_context_path(
            self.context_root,
            f"98_state/staging/transactions/{stage_id}",
            allowed_prefixes=("98_state/staging/transactions",),
        )
        transaction_dir.mkdir(exist_ok=False)

        targets: list[IntentTarget] = []
        published = False
        try:
            for index, (write, target_path, target_relative) in enumerate(resolved):
                preimage = _read_optional_regular_file(target_path)
                backup_path: Path | None = None
                backup_relative: str | None = None
                preimage_hash: str | None = None
                if preimage is not None:
                    backup_path = transaction_dir / f"{index:08d}.backup"
                    _write_fsynced(backup_path, preimage, exclusive=True)
                    preimage_hash = _hash_bytes(preimage)
                    if _hash_regular_file(backup_path) != preimage_hash:
                        raise StagingError("A staged preimage failed hash verification")
                    backup_relative = backup_path.relative_to(self.context_root).as_posix()

                staged_path = transaction_dir / f"{index:08d}.stage"
                _write_fsynced(staged_path, write.content, exclusive=True)
                expected_hash = _hash_bytes(write.content)
                if _hash_regular_file(staged_path) != expected_hash:
                    raise StagingError("A staged postimage failed hash verification")
                staged_relative = staged_path.relative_to(self.context_root).as_posix()
                targets.append(
                    IntentTarget(
                        target=target_relative,
                        staged=staged_relative,
                        content_hash=expected_hash,
                        backup=backup_relative,
                        preimage_hash=preimage_hash,
                    )
                )
                _require_same_volume(self._staging_root, target_path.parent)
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

    def apply(self, intent: IntentRecord, *, lock: ContextLock) -> IntentRecord:
        """Apply postimages in intent order and leave the intent for audit finalization."""

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

        intent = self._load_intent()
        if intent.transaction_id != transaction_id:
            raise InvalidIntentError("The durable intent belongs to another transaction")
        validated = self._validated_intent_paths(intent)
        for intent_target, target_path, staged_path, backup_path in validated:
            self._verify_backup(intent_target, backup_path)
            if _hash_optional_regular_file(target_path) != intent_target.preimage_hash:
                raise RecoveryRequiredError(
                    f"Target has not reached its recorded preimage: {intent_target.target}"
                )
            if _hash_optional_regular_file(staged_path) != intent_target.content_hash:
                raise RecoveryRequiredError(
                    f"Postimage is unavailable after rollback: {intent_target.target}"
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
        for intent_target, target_path, _staged_path, _backup_path in validated:
            if _hash_optional_regular_file(target_path) != intent_target.content_hash:
                raise RecoveryRequiredError(
                    f"Target has not reached its recorded postimage: {intent_target.target}"
                )
        expected_nonce = intent.nonce if expected_nonce_from_intent else None
        _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)
        self._remove_intent_and_staging(validated)

    def _remove_intent_and_staging(
        self,
        validated: tuple[tuple[IntentTarget, Path, Path, Path | None], ...],
    ) -> None:
        self._intent_path.unlink()
        _fsync_directory(self._staging_root)

        transaction_dirs = {
            staged_path.parent for _target, _path, staged_path, _backup_path in validated
        }
        for transaction_dir in transaction_dirs:
            _remove_owned_directory(transaction_dir)

    def inspect_recovery(self) -> RecoveryInspection:
        """Classify an interrupted staged replacement without changing any files."""

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

        active_dirs = {
            staged_path.parent.name for _target, _path, staged_path, _backup_path in validated
        }
        orphan_staging = self._orphan_transaction_dirs(active=active_dirs)
        inspections: list[TargetInspection] = []
        for intent_target, target_path, staged_path, backup_path in validated:
            parent_usable = _target_parent_is_usable(self._staging_root, target_path.parent)
            try:
                current_hash = _hash_optional_regular_file(target_path)
                staged_hash = _hash_optional_regular_file(staged_path)
                backup_hash = (
                    _hash_optional_regular_file(backup_path) if backup_path is not None else None
                )
            except (ContextBoundaryError, OSError):
                current_hash = None
                staged_hash = None
                backup_hash = None
            backup_valid = (
                intent_target.preimage_hash is None or backup_hash == intent_target.preimage_hash
            )
            if not parent_usable or not backup_valid:
                state = TargetRecoveryState.CONFLICT
            elif current_hash == intent_target.content_hash:
                state = TargetRecoveryState.POSTIMAGE_PRESENT
            elif (
                current_hash == intent_target.preimage_hash
                and staged_hash == intent_target.content_hash
            ):
                state = TargetRecoveryState.STAGED_POSTIMAGE_AVAILABLE
            else:
                state = TargetRecoveryState.CONFLICT
            inspections.append(
                TargetInspection(
                    target=intent_target.target,
                    expected_hash=intent_target.content_hash,
                    preimage_hash=intent_target.preimage_hash,
                    current_hash=current_hash,
                    staged_hash=staged_hash,
                    backup_hash=backup_hash,
                    state=state,
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
        validated: tuple[tuple[IntentTarget, Path, Path, Path | None], ...],
        *,
        lock: ContextLock,
        expected_nonce: str | None,
    ) -> None:
        for intent_target, target_path, staged_path, backup_path in validated:
            if not _target_parent_is_usable(self._staging_root, target_path.parent):
                raise RecoveryRequiredError(
                    f"Target parent is unavailable for {intent_target.target}"
                )
            current_hash = _hash_optional_regular_file(target_path)
            self._verify_backup(intent_target, backup_path)
            if current_hash == intent_target.content_hash:
                continue
            if current_hash != intent_target.preimage_hash:
                raise RecoveryRequiredError(
                    f"Target differs from both recorded images: {intent_target.target}"
                )
            if _hash_optional_regular_file(staged_path) != intent_target.content_hash:
                raise InvalidIntentError(
                    f"Staged postimage is unavailable for target {intent_target.target}"
                )

        _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)
        for intent_target, target_path, staged_path, _backup_path in validated:
            current_hash = _hash_optional_regular_file(target_path)
            if current_hash == intent_target.content_hash:
                continue
            if current_hash != intent_target.preimage_hash:
                raise RecoveryRequiredError(
                    f"Target changed during replacement: {intent_target.target}"
                )
            if _hash_optional_regular_file(staged_path) != intent_target.content_hash:
                raise InvalidIntentError(
                    f"Staged postimage is unavailable for target {intent_target.target}"
                )
            self._replace_with_retry(
                staged_path,
                target_path,
                lock=lock,
                expected_nonce=expected_nonce,
                expected_source_hash=intent_target.content_hash,
                expected_target_hash=intent_target.preimage_hash,
            )
            _fsync_directory(target_path.parent)

    def _rollback_validated(
        self,
        validated: tuple[tuple[IntentTarget, Path, Path, Path | None], ...],
        *,
        lock: ContextLock,
        expected_nonce: str | None,
    ) -> None:
        for intent_target, target_path, staged_path, backup_path in validated:
            if not _target_parent_is_usable(self._staging_root, target_path.parent):
                raise RecoveryRequiredError(
                    f"Target parent is unavailable for {intent_target.target}"
                )
            current_hash = _hash_optional_regular_file(target_path)
            self._verify_backup(intent_target, backup_path)
            if current_hash not in {intent_target.content_hash, intent_target.preimage_hash}:
                raise RecoveryRequiredError(
                    f"Target differs from both recorded images: {intent_target.target}"
                )
            if (
                current_hash == intent_target.preimage_hash
                and _hash_optional_regular_file(staged_path) != intent_target.content_hash
            ):
                raise InvalidIntentError(
                    f"Staged postimage is unavailable for target {intent_target.target}"
                )

        _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)

        def verify_fence_attempt() -> None:
            _verify_lock(self.context_root, lock, expected_nonce=expected_nonce)

        for intent_target, target_path, staged_path, backup_path in reversed(validated):
            current_hash = _hash_optional_regular_file(target_path)
            if current_hash == intent_target.preimage_hash:
                continue
            if current_hash != intent_target.content_hash:
                raise RecoveryRequiredError(
                    f"Target changed during rollback: {intent_target.target}"
                )

            staged_hash = _hash_optional_regular_file(staged_path)
            if staged_hash != intent_target.content_hash:
                postimage = _read_regular_file(target_path)
                if _hash_bytes(postimage) != intent_target.content_hash:
                    raise RecoveryRequiredError(
                        f"Target changed during rollback: {intent_target.target}"
                    )
                self._publish_rebuilt_postimage(
                    staged_path,
                    postimage,
                    expected_hash=intent_target.content_hash,
                    lock=lock,
                    expected_nonce=expected_nonce,
                )

            if intent_target.preimage_hash is None:
                _unlink_with_retry(
                    target_path,
                    expected_hash=intent_target.content_hash,
                    policy=self.retry_policy,
                    sleep_function=self._sleep_function,
                    before_attempt=verify_fence_attempt,
                )
            else:
                if backup_path is None:
                    raise InvalidIntentError(
                        f"Preimage backup is unavailable for target {intent_target.target}"
                    )
                preimage = _read_regular_file(backup_path)
                if _hash_bytes(preimage) != intent_target.preimage_hash:
                    raise InvalidIntentError(
                        f"Preimage backup is unavailable for target {intent_target.target}"
                    )
                rollback_path = staged_path.with_suffix(".rollback")
                _reject_unsafe_file(rollback_path, allow_missing=True)
                _write_fsynced(rollback_path, preimage, exclusive=False)
                self._replace_with_retry(
                    rollback_path,
                    target_path,
                    lock=lock,
                    expected_nonce=expected_nonce,
                    expected_source_hash=intent_target.preimage_hash,
                    expected_target_hash=intent_target.content_hash,
                )
            _fsync_directory(target_path.parent)

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

    def _resolve_writes(
        self,
        writes: tuple[StagedWrite, ...],
    ) -> tuple[tuple[StagedWrite, Path, str], ...]:
        resolved: list[tuple[StagedWrite, Path, str]] = []
        collision_keys: set[str] = set()
        for write in writes:
            if not isinstance(write.content, bytes):
                raise TypeError("Staged content must be bytes")
            target_path = _resolve_target(self.context_root, write.target)
            if not target_path.parent.is_dir():
                raise StagingError(f"Target parent directory does not exist: {write.target}")
            _reject_unsafe_file(target_path, allow_missing=True)
            target_relative = target_path.relative_to(self.context_root).as_posix()
            collision_key = unicodedata.normalize("NFC", target_relative).casefold()
            if collision_key in collision_keys:
                raise StagingError(f"Duplicate staged target: {target_relative}")
            collision_keys.add(collision_key)
            resolved.append((write, target_path, target_relative))
        return tuple(resolved)

    def _publish_intent(self, intent: IntentRecord, transaction_dir: Path) -> None:
        temp_path = transaction_dir / "intent.complete.tmp"
        _write_fsynced(temp_path, _encode_intent(intent), exclusive=True)
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
    ) -> tuple[tuple[IntentTarget, Path, Path, Path | None], ...]:
        collision_keys: set[str] = set()
        staged_paths: set[str] = set()
        backup_paths: set[str] = set()
        transaction_dir: Path | None = None
        validated: list[tuple[IntentTarget, Path, Path, Path | None]] = []
        for index, item in enumerate(intent.targets):
            target_path = _resolve_target(self.context_root, item.target)
            canonical_target = target_path.relative_to(self.context_root).as_posix()
            if item.target != canonical_target:
                raise InvalidIntentError("Intent target path is not canonical")
            staged_path = resolve_context_path(
                self.context_root,
                item.staged,
                allowed_prefixes=("98_state/staging/transactions",),
            )
            relative_staged = PurePosixPath(item.staged)
            if (
                len(relative_staged.parts) != 5
                or _TRANSACTION_DIRECTORY.fullmatch(relative_staged.parts[3]) is None
                or relative_staged.name != f"{index:08d}.stage"
                or item.staged != staged_path.relative_to(self.context_root).as_posix()
            ):
                raise InvalidIntentError("Intent staged path does not use the owned layout")
            if transaction_dir is None:
                transaction_dir = staged_path.parent
            elif staged_path.parent != transaction_dir:
                raise InvalidIntentError("Intent postimages span multiple staging directories")
            backup_path: Path | None = None
            if item.backup is not None:
                backup_path = resolve_context_path(
                    self.context_root,
                    item.backup,
                    allowed_prefixes=("98_state/staging/transactions",),
                )
                relative_backup = PurePosixPath(item.backup)
                if (
                    len(relative_backup.parts) != 5
                    or relative_backup.parts[3] != relative_staged.parts[3]
                    or relative_backup.name != f"{index:08d}.backup"
                    or backup_path.parent != staged_path.parent
                    or item.backup != backup_path.relative_to(self.context_root).as_posix()
                ):
                    raise InvalidIntentError("Intent backup path does not use the owned layout")
            collision_key = unicodedata.normalize("NFC", item.target).casefold()
            if collision_key in collision_keys:
                raise InvalidIntentError("Intent contains duplicate target paths")
            collision_keys.add(collision_key)
            staged_key = unicodedata.normalize("NFC", item.staged).casefold()
            if staged_key in staged_paths or staged_key in backup_paths:
                raise InvalidIntentError("Intent contains duplicate staged paths")
            staged_paths.add(staged_key)
            if item.backup is not None:
                backup_key = unicodedata.normalize("NFC", item.backup).casefold()
                if backup_key in backup_paths or backup_key in staged_paths:
                    raise InvalidIntentError("Intent contains duplicate backup paths")
                backup_paths.add(backup_key)
            validated.append((item, target_path, staged_path, backup_path))
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
    ) -> None:
        def verify_attempt() -> None:
            _require_source_hash(source, expected_source_hash)
            _require_target_hash(target, expected_target_hash)
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


def _ensure_runtime_directories(root: Path) -> Path:
    state = resolve_context_path(root, "98_state", allowed_prefixes=("98_state",))
    state.mkdir(exist_ok=True)
    _require_plain_directory(state)
    _fsync_directory(root)
    staging = resolve_context_path(root, "98_state/staging", allowed_prefixes=("98_state",))
    staging.mkdir(exist_ok=True)
    _require_plain_directory(staging)
    _fsync_directory(state)
    transactions = resolve_context_path(
        root,
        "98_state/staging/transactions",
        allowed_prefixes=("98_state/staging",),
    )
    transactions.mkdir(exist_ok=True)
    _require_plain_directory(transactions)
    _fsync_directory(staging)
    return staging


def _require_plain_directory(path: Path) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ContextBoundaryError(f"Runtime directory must not be a symlink or junction: {path}")
    if not path.is_dir():
        raise ContextBoundaryError(f"Runtime path must be a directory: {path}")


def _reject_unsafe_file(path: Path, *, allow_missing: bool) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
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
    sleep_function: SleepFunction,
    before_attempt: BeforeAttemptFunction | None = None,
) -> None:
    delay = policy.initial_delay_seconds
    for attempt in range(policy.max_attempts):
        _require_target_hash(target, expected_hash)
        if before_attempt is not None:
            before_attempt()
        try:
            target.unlink()
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
