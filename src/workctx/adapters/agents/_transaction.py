"""Rollback-capable, intent-recorded adapter file transactions."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from ._lock import AdapterLock
from ._safe_fs import (
    FileSnapshot,
    SafeRoot,
    collision_key,
    is_credential_capable_path,
    validate_relative_path,
)
from .errors import RecoveryConflictError, RecoveryRequiredError
from .layout import InstallationLayout
from .models import AgentClient, FileOperation
from .renderers import content_hash


@dataclass(frozen=True, slots=True)
class FileMutation:
    """One exact expected-to-desired file transition."""

    path: str
    expected: FileSnapshot
    desired: bytes | None

    @property
    def operation(self) -> FileOperation:
        if not self.expected.exists:
            if self.desired is None:
                raise ValueError("Absent-to-absent is not a mutation")
            return FileOperation.CREATE
        if self.desired is None:
            return FileOperation.DELETE
        return FileOperation.REPLACE

    @property
    def desired_hash(self) -> str | None:
        return None if self.desired is None else content_hash(self.desired)


@dataclass(frozen=True, slots=True)
class TransactionInspection:
    """Read-only staging state used before ordinary status evaluation."""

    intents: tuple[str, ...]
    orphan_directories: tuple[str, ...]
    invalid: bool = False
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryTransition:
    """Trusted-record binding for one parsed project-local transaction intent."""

    manifest_before: str | None
    manifest_after: str | None
    operations_digest: str


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "." if parent == "." else parent


def _snapshot_state(snapshot: FileSnapshot) -> str:
    return "absent" if not snapshot.exists else str(snapshot.content_hash)


def _state_digest(value: object) -> str | None:
    if value == "absent":
        return None
    if not _is_hash(value):
        raise RecoveryConflictError("Transaction manifest state is invalid")
    assert isinstance(value, str)
    return value


def _operations_digest(
    operations: list[dict[str, object]],
    client: AgentClient,
    manifest_path: str,
) -> str:
    logical = [
        {
            "operation": operation["operation"],
            "target": operation["target"],
            "expected": operation["expected"],
            "postimage": operation["postimage"],
        }
        for operation in operations
    ]
    payload = b"workctx-agent-operations-v1\0" + json.dumps(
        {
            "adapter": client.value,
            "manifest_path": manifest_path,
            "operations": logical,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return content_hash(payload)


def mutation_operations_digest(
    mutations: tuple[FileMutation, ...],
    *,
    client: AgentClient,
    manifest_path: str,
) -> str:
    """Return the recovery-stable logical digest for a prepared mutation sequence."""

    operations: list[dict[str, object]] = [
        {
            "operation": mutation.operation.value,
            "target": mutation.path,
            "expected": _snapshot_state(mutation.expected),
            "postimage": mutation.desired_hash or "absent",
        }
        for mutation in mutations
    ]
    return _operations_digest(operations, client, manifest_path)


def _recovery_transition(
    operations: list[dict[str, object]],
    client: AgentClient,
    manifest_path: str,
) -> RecoveryTransition:
    manifest_operation = next(
        (operation for operation in operations if operation["target"] == manifest_path),
        None,
    )
    if manifest_operation is None:
        raise RecoveryConflictError("Transaction intent does not include its manifest")
    return RecoveryTransition(
        manifest_before=_state_digest(manifest_operation["expected"]),
        manifest_after=_state_digest(manifest_operation["postimage"]),
        operations_digest=_operations_digest(operations, client, manifest_path),
    )


def _matches_expected(actual: FileSnapshot, expected: FileSnapshot) -> bool:
    if actual.exists != expected.exists:
        return False
    if not expected.exists:
        return True
    return actual.identity == expected.identity and actual.content_hash == expected.content_hash


def _matches_recorded_state(snapshot: FileSnapshot, state: object) -> bool:
    """Return whether a target matches one hash-or-absence state from an intent."""

    if state == "absent":
        return not snapshot.exists
    return isinstance(state, str) and snapshot.exists and snapshot.content_hash == state


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


_BACKUP_STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}(?:\.[0-9]+)?Z$")


def _portable_skill_name(value: str) -> bool:
    return bool(value) and all(
        part and all(character in "abcdefghijklmnopqrstuvwxyz0123456789" for character in part)
        for part in value.split("-")
    )


def _safe_skill_target(path: str, client: AgentClient) -> bool:
    parts = path.split("/")
    return (
        len(parts) >= 4
        and parts[0] == f".{client.value}"
        and parts[1] == "skills"
        and _portable_skill_name(parts[2])
        and not is_credential_capable_path("/".join(parts[3:]))
        and all(part for part in parts[3:])
    )


def _safe_backup_target(path: str, client: AgentClient) -> bool:
    parts = path.split("/")
    if len(parts) < 4 or parts[:2] != [".workctx", "backups"]:
        return False
    if _BACKUP_STAMP.fullmatch(parts[2]) is None:
        return False
    remainder = parts[3:]
    while remainder:
        if remainder[0] == client.value:
            remainder = remainder[1:]
            continue
        if (
            len(remainder) >= 3
            and remainder[:2] == [".workctx", "backups"]
            and _BACKUP_STAMP.fullmatch(remainder[2]) is not None
        ):
            remainder = remainder[3:]
            continue
        break
    bridge = {
        AgentClient.CODEX: ["AGENTS.md"],
        AgentClient.CLAUDE: ["CLAUDE.md"],
        AgentClient.GEMINI: ["GEMINI.md"],
    }[client]
    return remainder == bridge or _safe_skill_target("/".join(remainder), client)


def _allowed_transaction_target(
    target: str,
    operation: object,
    client: AgentClient,
    manifest_path: str,
) -> bool:
    if target == manifest_path:
        return True
    bridge = {
        AgentClient.CODEX: "AGENTS.md",
        AgentClient.CLAUDE: "CLAUDE.md",
        AgentClient.GEMINI: "GEMINI.md",
    }[client]
    if (
        target == bridge
        or _safe_skill_target(target, client)
        or _safe_backup_target(target, client)
    ):
        return True
    if client is AgentClient.CODEX and operation == "create":
        if target == ".agents/skills/registry.yaml":
            return True
        parts = target.split("/")
        return (
            len(parts) >= 4
            and parts[:2] == [".agents", "skills"]
            and _portable_skill_name(parts[2])
            and not is_credential_capable_path("/".join(parts[3:]))
            and all(part for part in parts[3:])
        )
    return False


def _validated_recovery_operations(
    value: object,
    *,
    transaction_root: str,
    manifest_path: str,
    client: AgentClient,
) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise RecoveryConflictError("Transaction operations must be a nonempty list")
    required_fields = {
        "operation",
        "target",
        "expected",
        "postimage",
        "staged",
        "backup",
        "removed",
    }
    operations: list[dict[str, object]] = []
    target_keys: set[str] = set()
    internal_keys: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != required_fields:
            raise RecoveryConflictError("Transaction operation has an invalid object shape")
        operation = item["operation"]
        target = item["target"]
        expected = item["expected"]
        postimage = item["postimage"]
        staged = item["staged"]
        backup = item["backup"]
        removed = item["removed"]
        if operation not in {"create", "replace", "delete"} or not isinstance(target, str):
            raise RecoveryConflictError("Transaction operation or target is invalid")
        try:
            target = validate_relative_path(target)
        except ValueError as error:
            raise RecoveryConflictError("Transaction target path is unsafe") from error
        if not _allowed_transaction_target(target, operation, client, manifest_path):
            raise RecoveryConflictError(
                "Transaction target is outside the adapter's credential-safe ownership set"
            )
        key = collision_key(target)
        if key in target_keys:
            raise RecoveryConflictError("Transaction target paths collide")
        target_keys.add(key)
        if operation == "create":
            valid_shape = (
                expected == "absent"
                and _is_hash(postimage)
                and isinstance(staged, str)
                and backup is None
                and removed is None
            )
        elif operation == "replace":
            valid_shape = (
                _is_hash(expected)
                and _is_hash(postimage)
                and isinstance(staged, str)
                and isinstance(backup, str)
                and removed is None
            )
        else:
            valid_shape = (
                _is_hash(expected)
                and postimage == "absent"
                and staged is None
                and isinstance(backup, str)
                and isinstance(removed, str)
            )
        if not valid_shape:
            raise RecoveryConflictError("Transaction operation state is inconsistent")
        for field, internal, directory in (
            ("staged", staged, "staged"),
            ("backup", backup, "verified"),
            ("removed", removed, "removed"),
        ):
            if internal is None:
                continue
            assert isinstance(internal, str)
            try:
                internal = validate_relative_path(internal)
            except ValueError as error:
                raise RecoveryConflictError(f"Transaction {field} path is unsafe") from error
            if not internal.startswith(f"{transaction_root}/{directory}/"):
                raise RecoveryConflictError(f"Transaction {field} path escapes its directory")
            internal_key = collision_key(internal)
            if internal_key in internal_keys:
                raise RecoveryConflictError("Transaction internal paths collide")
            internal_keys.add(internal_key)
        operations.append(dict(item))
    if operations[-1]["target"] != manifest_path:
        raise RecoveryConflictError("Transaction manifest operation must be last")
    return operations


def _parse_intent(
    content: bytes,
    *,
    intent_path: str,
    layout: InstallationLayout,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecoveryConflictError("Transaction intent is invalid JSON") from error
    fields = {
        "schema_version",
        "transaction_id",
        "lock_nonce",
        "adapter",
        "manifest_path",
        "operations",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RecoveryConflictError("Transaction intent has an invalid object shape")
    transaction_id = value["transaction_id"]
    intent_nonce = value["lock_nonce"]
    expected_id = PurePosixPath(intent_path).parent.name
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["adapter"] != layout.client.value
        or value["manifest_path"] != layout.manifest_path
        or not isinstance(transaction_id, str)
        or transaction_id != expected_id
        or len(transaction_id) != 32
        or any(character not in "0123456789abcdef" for character in transaction_id)
        or not isinstance(intent_nonce, str)
        or len(intent_nonce) != 32
        or any(character not in "0123456789abcdef" for character in intent_nonce)
    ):
        raise RecoveryConflictError("Transaction intent does not match its derived location")
    transaction_root = str(PurePosixPath(intent_path).parent)
    operations = _validated_recovery_operations(
        value["operations"],
        transaction_root=transaction_root,
        manifest_path=layout.manifest_path,
        client=layout.client,
    )
    return value, operations


def _validate_staging_tree(safe: SafeRoot, root: str) -> None:
    """Walk every staging entry with no-follow primitives before classification."""

    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in safe.list_directory(directory):
            if entry.is_directory:
                pending.append(entry.path)


def inspect_transactions(layout: InstallationLayout) -> TransactionInspection:
    """Safely discover exactly one intent or harmless orphan transaction directories."""

    safe = SafeRoot(layout.root)
    try:
        entries = safe.list_directory(layout.staging_path)
    except FileNotFoundError:
        return TransactionInspection((), ())
    except Exception as error:
        return TransactionInspection((), (), invalid=True, detail=str(error))
    intents: list[str] = []
    orphans: list[str] = []
    for entry in entries:
        if not entry.is_directory:
            return TransactionInspection(
                (), (), invalid=True, detail=f"Unexpected staging entry: {entry.path}"
            )
        try:
            _validate_staging_tree(safe, entry.path)
        except Exception as error:
            return TransactionInspection((), (), invalid=True, detail=str(error))
        intent_path = f"{entry.path}/intent.json"
        try:
            intent = safe.inspect_file(intent_path)
        except Exception as error:
            return TransactionInspection((), (), invalid=True, detail=str(error))
        if intent.exists:
            if intent.content is None:
                return TransactionInspection(
                    (), (), invalid=True, detail="Transaction intent content is unavailable"
                )
            try:
                _parse_intent(intent.content, intent_path=intent_path, layout=layout)
            except RecoveryConflictError as error:
                return TransactionInspection((), (), invalid=True, detail=str(error))
            intents.append(intent_path)
        else:
            orphans.append(entry.path)
    if len(intents) > 1:
        return TransactionInspection(
            tuple(sorted(intents)),
            tuple(sorted(orphans)),
            invalid=True,
            detail="More than one adapter transaction intent exists",
        )
    return TransactionInspection(tuple(intents), tuple(sorted(orphans)))


def _validate_recovery_internal_files(
    safe: SafeRoot,
    operations: list[dict[str, object]],
    targets: list[FileSnapshot],
) -> None:
    """Validate all intent-referenced evidence before intent removal or rollback writes."""

    for operation, target in zip(operations, targets, strict=True):
        expected = operation["expected"]
        postimage = operation["postimage"]
        staged_path = operation["staged"]
        backup_path = operation["backup"]
        removed_path = operation["removed"]
        if isinstance(staged_path, str):
            staged = safe.inspect_file(staged_path)
            if staged.exists and staged.content_hash != postimage:
                raise RecoveryConflictError("Transaction staged postimage was modified")
            if not staged.exists and not (
                _matches_recorded_state(target, postimage)
                or _matches_recorded_state(target, expected)
            ):
                raise RecoveryConflictError("Required transaction staged postimage is missing")
        if isinstance(backup_path, str):
            backup = safe.inspect_file(backup_path)
            if backup.exists and backup.content_hash != expected:
                raise RecoveryConflictError("Transaction verified preimage backup is invalid")
            if not backup.exists and not _matches_recorded_state(target, expected):
                raise RecoveryConflictError("Required transaction preimage backup is missing")
        if isinstance(removed_path, str):
            removed = safe.inspect_file(removed_path)
            if removed.exists and removed.content_hash != expected:
                raise RecoveryConflictError("Transaction removed preimage was modified")
            if not removed.exists and not _matches_recorded_state(target, expected):
                raise RecoveryConflictError("Deleted target and its moved preimage are both absent")


def _require_unchanged_intent(
    safe: SafeRoot,
    intent_path: str,
    expected: FileSnapshot,
) -> FileSnapshot:
    """Require the same intent identity and bytes that were parsed for recovery."""

    current = safe.inspect_file(intent_path)
    if not _matches_expected(current, expected):
        raise RecoveryConflictError("Transaction intent changed during recovery")
    return current


def _inspect_recovery_targets(
    safe: SafeRoot,
    operations: list[dict[str, object]],
) -> list[FileSnapshot]:
    targets: list[FileSnapshot] = []
    for operation in operations:
        target_path = operation["target"]
        if not isinstance(target_path, str):  # pragma: no cover - parsed intents guarantee this
            raise RecoveryConflictError("Transaction operation is malformed")
        targets.append(safe.inspect_file(target_path))
    return targets


def _require_recorded_target(
    safe: SafeRoot,
    operation: dict[str, object],
) -> FileSnapshot:
    """Freshly require a target to match its exact recorded pre- or postimage."""

    target_path = operation["target"]
    if not isinstance(target_path, str):  # pragma: no cover - parsed intents guarantee this
        raise RecoveryConflictError("Transaction operation is malformed")
    target = safe.inspect_file(target_path)
    if not (
        _matches_recorded_state(target, operation["expected"])
        or _matches_recorded_state(target, operation["postimage"])
    ):
        raise RecoveryConflictError("A recovery target matches neither recorded state")
    return target


def _fence_recovery_step(
    lock: AdapterLock,
    safe: SafeRoot,
    intent_path: str,
    intent_snapshot: FileSnapshot,
) -> None:
    """Refresh the writer lease and bind one recovery step to its parsed intent."""

    lock.heartbeat()
    _require_unchanged_intent(safe, intent_path, intent_snapshot)


def _cleanup_recovery_artifacts(
    safe: SafeRoot,
    lock: AdapterLock,
    transaction_root: str,
    operations: list[dict[str, object]],
) -> None:
    """Remove only unchanged transaction evidence after its intent is resolved."""

    for operation in reversed(operations):
        for field, state_field in (
            ("staged", "postimage"),
            ("backup", "expected"),
            ("removed", "expected"),
        ):
            path = operation[field]
            if not isinstance(path, str):
                continue
            lock.heartbeat()
            artifact = safe.inspect_file(path)
            if not artifact.exists:
                continue
            if not _matches_recorded_state(artifact, operation[state_field]):
                raise RecoveryConflictError(f"Transaction {field} evidence changed before cleanup")
            if not safe.unlink(path, expected=artifact):
                raise RecoveryConflictError(
                    f"Transaction {field} evidence disappeared during cleanup"
                )
            if safe.inspect_file(path).exists:
                raise RecoveryConflictError(f"Transaction {field} evidence remains after cleanup")
    for child in ("staged", "verified", "removed"):
        lock.heartbeat()
        safe.remove_empty_directory(f"{transaction_root}/{child}")
    lock.heartbeat()
    safe.remove_empty_directory(transaction_root)


def _resolve_recovered_transaction(
    *,
    safe: SafeRoot,
    lock: AdapterLock,
    transaction_root: str,
    intent_path: str,
    intent_snapshot: FileSnapshot,
    operations: list[dict[str, object]],
    resolved_state: str,
    authority_check: Callable[[], None] | None = None,
) -> None:
    """Revalidate a recovered result and atomically retire its unchanged intent."""

    if resolved_state not in {"expected", "postimage"}:
        raise ValueError("resolved_state must be expected or postimage")
    _fence_recovery_step(lock, safe, intent_path, intent_snapshot)
    targets = _inspect_recovery_targets(safe, operations)
    if not all(
        _matches_recorded_state(target, operation[resolved_state])
        for operation, target in zip(operations, targets, strict=True)
    ):
        raise RecoveryConflictError("Recovered targets changed before transaction resolution")
    _validate_recovery_internal_files(safe, operations, targets)

    # Repeat the fence, intent binding, and complete target-set check immediately
    # before retiring the only durable recovery record.
    _fence_recovery_step(lock, safe, intent_path, intent_snapshot)
    targets = _inspect_recovery_targets(safe, operations)
    _validate_recovery_internal_files(safe, operations, targets)
    targets = _inspect_recovery_targets(safe, operations)
    if not all(
        _matches_recorded_state(target, operation[resolved_state])
        for operation, target in zip(operations, targets, strict=True)
    ):
        raise RecoveryConflictError("Recovered targets changed immediately before intent removal")
    current_intent = _require_unchanged_intent(safe, intent_path, intent_snapshot)
    if authority_check is not None:
        authority_check()
    if not safe.unlink(intent_path, expected=current_intent):
        raise RecoveryConflictError("Resolved transaction intent disappeared")
    if safe.inspect_file(intent_path).exists:
        raise RecoveryConflictError("Resolved transaction intent remains")
    safe.fsync_directory(transaction_root)
    _cleanup_recovery_artifacts(safe, lock, transaction_root, operations)


class AtomicAdapterTransaction:
    """Apply preflighted output transitions with a flushed recovery record."""

    def __init__(
        self,
        layout: InstallationLayout,
        client: AgentClient,
        lock: AdapterLock,
    ) -> None:
        self._layout = layout
        self._client = client
        self._lock = lock
        self._safe = SafeRoot(layout.root)

    def apply(
        self,
        mutations: tuple[FileMutation, ...],
        *,
        target_snapshots: tuple[tuple[str, FileSnapshot], ...] = (),
        authority_check: Callable[[], None] | None = None,
    ) -> tuple[str, ...]:
        if not mutations:
            return ()
        paths = [validate_relative_path(mutation.path) for mutation in mutations]
        if len({collision_key(path) for path in paths}) != len(paths):
            raise ValueError("Transaction target paths collide")
        if paths[-1] != self._layout.manifest_path:
            raise ValueError("The manifest mutation must be ordered last")
        plan_targets = target_snapshots or tuple(
            (mutation.path, mutation.expected) for mutation in mutations
        )
        plan_paths = [validate_relative_path(path) for path, _snapshot in plan_targets]
        if len({collision_key(path) for path in plan_paths}) != len(plan_paths):
            raise ValueError("Plan target snapshot paths collide")
        snapshots_by_path = dict(plan_targets)
        for mutation in mutations:
            observed = snapshots_by_path.get(mutation.path)
            if observed is None or not _matches_expected(observed, mutation.expected):
                raise ValueError(f"Mutation lacks its exact plan target snapshot: {mutation.path}")
        self._require_plan_target_states(
            plan_targets,
            mutations,
            committed_count=0,
            detail="Precondition changed before staging",
        )

        transaction_id = secrets.token_hex(16)
        transaction_root = f"{self._layout.staging_path}/{transaction_id}"
        staged_root = f"{transaction_root}/staged"
        verified_root = f"{transaction_root}/verified"
        removed_root = f"{transaction_root}/removed"
        self._safe.ensure_directories(staged_root)
        self._safe.ensure_directories(verified_root)
        self._safe.ensure_directories(removed_root)

        operations: list[dict[str, object]] = []
        staged_snapshots: dict[int, FileSnapshot] = {}
        for index, mutation in enumerate(mutations):
            staged_path = None
            backup_path = None
            removed_path = None
            if mutation.desired is not None:
                staged_path = f"{staged_root}/{index:04d}.post"
                staged = self._safe.write_exclusive(staged_path, mutation.desired)
                if staged.content_hash != mutation.desired_hash:
                    raise RecoveryRequiredError("Staged postimage hash mismatch")
                staged_snapshots[index] = staged
            if mutation.expected.exists:
                if mutation.expected.content is None:
                    raise RecoveryRequiredError("Preimage content is unavailable")
                backup_path = f"{verified_root}/{index:04d}.pre"
                backup = self._safe.write_exclusive(backup_path, mutation.expected.content)
                if backup.content_hash != mutation.expected.content_hash:
                    raise RecoveryRequiredError("Staged preimage hash mismatch")
                if mutation.desired is None:
                    removed_path = f"{removed_root}/{index:04d}.pre"
            operations.append(
                {
                    "operation": mutation.operation.value,
                    "target": mutation.path,
                    "expected": _snapshot_state(mutation.expected),
                    "postimage": mutation.desired_hash or "absent",
                    "staged": staged_path,
                    "backup": backup_path,
                    "removed": removed_path,
                }
            )

        intent = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "lock_nonce": self._lock.nonce,
            "adapter": self._client.value,
            "manifest_path": self._layout.manifest_path,
            "operations": operations,
        }
        intent_path = f"{transaction_root}/intent.json"
        intent_bytes = (json.dumps(intent, indent=2) + "\n").encode("utf-8")
        self._lock.heartbeat()
        intent_snapshot = self._safe.write_exclusive(intent_path, intent_bytes)
        self._safe.fsync_directory(transaction_root)

        applied = 0
        reservations: dict[int, FileSnapshot] = {}
        try:
            for index, mutation in enumerate(mutations):
                self._lock.heartbeat()
                if index in {0, len(mutations) - 1}:
                    phase = (
                        "immediately before the first mutation"
                        if index == 0
                        else "immediately before manifest commit"
                    )
                    self._require_plan_target_states(
                        plan_targets,
                        mutations,
                        committed_count=index,
                        detail=f"Plan target changed {phase}",
                    )
                if authority_check is not None:
                    authority_check()
                actual = self._safe.inspect_file(mutation.path)
                if not _matches_expected(actual, mutation.expected):
                    raise RecoveryConflictError(
                        f"Precondition changed before mutation: {mutation.path}"
                    )
                self._safe.ensure_directories(_parent(mutation.path))
                staged_value = operations[index]["staged"]
                removed_value = operations[index]["removed"]
                applied = index + 1
                if mutation.operation is FileOperation.CREATE:
                    reservation = self._safe.reserve_empty(mutation.path)
                    if reservation.size != 0:
                        raise RecoveryConflictError("Exclusive reservation was not empty")
                    reservations[index] = reservation
                    if not isinstance(staged_value, str):
                        raise AssertionError("Create operation lacks staged bytes")
                    self._safe.replace(
                        staged_value,
                        mutation.path,
                        expected_source=staged_snapshots[index],
                        expected_target=reservation,
                    )
                elif mutation.operation is FileOperation.REPLACE:
                    if not isinstance(staged_value, str):
                        raise AssertionError("Replace operation lacks staged bytes")
                    self._safe.replace(
                        staged_value,
                        mutation.path,
                        expected_source=staged_snapshots[index],
                        expected_target=actual,
                    )
                else:
                    if not isinstance(removed_value, str):
                        raise AssertionError("Delete operation lacks removed path")
                    self._safe.move(
                        mutation.path,
                        removed_value,
                        expected_source=actual,
                    )
            self._lock.heartbeat()
            self._require_plan_target_states(
                plan_targets,
                mutations,
                committed_count=len(mutations),
                detail="Plan target changed before final transaction resolution",
            )
            for mutation in mutations:
                final = self._safe.inspect_file(mutation.path)
                if mutation.desired is None:
                    if final.exists:
                        raise RecoveryRequiredError(f"Deleted target remains: {mutation.path}")
                elif final.content_hash != mutation.desired_hash:
                    raise RecoveryRequiredError(f"Target postimage mismatch: {mutation.path}")
        except Exception as error:
            if not self._rollback(mutations, operations, applied, reservations):
                raise RecoveryRequiredError(
                    f"Adapter transaction failed and rollback was incomplete: {transaction_id}"
                ) from error
            self._resolve_and_cleanup(
                transaction_root,
                intent_path,
                operations,
                mutations,
                intent_snapshot,
                resolved_state="expected",
            )
            raise

        self._resolve_and_cleanup(
            transaction_root,
            intent_path,
            operations,
            mutations,
            intent_snapshot,
            resolved_state="postimage",
            target_snapshots=plan_targets,
        )
        return tuple(paths)

    def _require_plan_target_states(
        self,
        target_snapshots: tuple[tuple[str, FileSnapshot], ...],
        mutations: tuple[FileMutation, ...],
        *,
        committed_count: int,
        detail: str,
    ) -> None:
        """Fence complete plan state, including unchanged and preserved targets."""

        mutation_indexes = {mutation.path: index for index, mutation in enumerate(mutations)}
        for path, expected in target_snapshots:
            current = self._safe.inspect_file(path)
            index = mutation_indexes.get(path)
            if index is None or index >= committed_count:
                matches = _matches_expected(current, expected)
            else:
                mutation = mutations[index]
                matches = _matches_recorded_state(
                    current,
                    mutation.desired_hash or "absent",
                )
            if not matches:
                raise RecoveryConflictError(f"{detail}: {path}")

    def _rollback(
        self,
        mutations: tuple[FileMutation, ...],
        operations: list[dict[str, object]],
        applied: int,
        reservations: dict[int, FileSnapshot],
    ) -> bool:
        try:
            for index in range(applied - 1, -1, -1):
                self._lock.heartbeat()
                mutation = mutations[index]
                operation = operations[index]
                current = self._safe.inspect_file(mutation.path)
                if _matches_expected(current, mutation.expected):
                    continue
                if mutation.expected.exists:
                    source = operation["removed"] or operation["backup"]
                    if not isinstance(source, str):
                        return False
                    source_snapshot = self._safe.inspect_file(source)
                    if (
                        not source_snapshot.exists
                        or source_snapshot.content_hash != mutation.expected.content_hash
                    ):
                        return False
                    if mutation.desired is None and current.exists:
                        return False
                    if mutation.desired is not None and (
                        not current.exists or current.content_hash != mutation.desired_hash
                    ):
                        return False
                    self._safe.replace(
                        source,
                        mutation.path,
                        expected_source=source_snapshot,
                        expected_target=current,
                    )
                else:
                    if current.exists:
                        reservation = reservations.get(index)
                        is_reservation = (
                            reservation is not None
                            and current.identity == reservation.identity
                            and current.content_hash == reservation.content_hash
                        )
                        if current.content_hash != mutation.desired_hash and not is_reservation:
                            return False
                        self._safe.unlink(mutation.path, expected=current)
            self._lock.heartbeat()
            return all(
                _matches_recorded_state(
                    self._safe.inspect_file(item.path),
                    _snapshot_state(item.expected),
                )
                for item in mutations
            )
        except Exception:
            return False

    def _resolve_and_cleanup(
        self,
        transaction_root: str,
        intent_path: str,
        operations: list[dict[str, object]],
        mutations: tuple[FileMutation, ...],
        intent_snapshot: FileSnapshot,
        *,
        resolved_state: str,
        target_snapshots: tuple[tuple[str, FileSnapshot], ...] = (),
    ) -> None:
        if resolved_state not in {"expected", "postimage"}:
            raise ValueError("resolved_state must be expected or postimage")
        self._lock.heartbeat()
        _require_unchanged_intent(self._safe, intent_path, intent_snapshot)
        if not self._mutations_match_state(mutations, resolved_state):
            raise RecoveryRequiredError("Resolved adapter targets changed before cleanup")
        if target_snapshots:
            self._require_plan_target_states(
                target_snapshots,
                mutations,
                committed_count=len(mutations),
                detail="Plan target changed before cleanup",
            )
        artifacts: list[tuple[str, FileSnapshot]] = []
        for operation in reversed(operations):
            for field, state_field in (
                ("staged", "postimage"),
                ("backup", "expected"),
                ("removed", "expected"),
            ):
                path = operation[field]
                if not isinstance(path, str):
                    continue
                artifact = self._safe.inspect_file(path)
                if not artifact.exists:
                    continue
                if not _matches_recorded_state(artifact, operation[state_field]):
                    raise RecoveryRequiredError(
                        f"Transaction {field} evidence changed before cleanup"
                    )
                artifacts.append((path, artifact))
        self._lock.heartbeat()
        _require_unchanged_intent(self._safe, intent_path, intent_snapshot)
        if not self._mutations_match_state(mutations, resolved_state):
            raise RecoveryRequiredError(
                "Resolved adapter targets changed immediately before intent removal"
            )
        if target_snapshots:
            self._require_plan_target_states(
                target_snapshots,
                mutations,
                committed_count=len(mutations),
                detail="Plan target changed immediately before intent removal",
            )
        if not self._safe.unlink(intent_path, expected=intent_snapshot):
            raise RecoveryRequiredError("Resolved adapter intent could not be removed")
        self._safe.fsync_directory(transaction_root)
        for path, artifact in artifacts:
            self._lock.heartbeat()
            self._safe.unlink(path, expected=artifact)
        for child in ("staged", "verified", "removed"):
            self._lock.heartbeat()
            self._safe.remove_empty_directory(f"{transaction_root}/{child}")
        self._lock.heartbeat()
        self._safe.remove_empty_directory(transaction_root)

    def _mutations_match_state(
        self,
        mutations: tuple[FileMutation, ...],
        resolved_state: str,
    ) -> bool:
        for mutation in mutations:
            target = self._safe.inspect_file(mutation.path)
            state = (
                _snapshot_state(mutation.expected)
                if resolved_state == "expected"
                else mutation.desired_hash or "absent"
            )
            if not _matches_recorded_state(target, state):
                return False
        return True


def inspect_recovery_transition(
    layout: InstallationLayout,
    client: AgentClient,
) -> RecoveryTransition | None:
    """Parse the pending intent's manifest transition without mutating project state."""

    inspection = inspect_transactions(layout)
    if inspection.invalid:
        raise RecoveryConflictError(inspection.detail or "Invalid transaction state")
    if not inspection.intents:
        return None
    if client is not layout.client:
        raise RecoveryConflictError("Recovery client does not match the derived layout")
    intent_path = inspection.intents[0]
    safe = SafeRoot(layout.root)
    intent_snapshot = safe.inspect_file(intent_path)
    if intent_snapshot.content is None:
        raise RecoveryConflictError("Transaction intent content is unavailable")
    _value, operations = _parse_intent(
        intent_snapshot.content,
        intent_path=intent_path,
        layout=layout,
    )
    return _recovery_transition(operations, client, layout.manifest_path)


def recover_transaction(
    layout: InstallationLayout,
    client: AgentClient,
    lock: AdapterLock,
    *,
    expected_transition: RecoveryTransition | None = None,
    authority_check: Callable[[], None] | None = None,
) -> tuple[str, ...]:
    """Finalize an all-postimage transaction or roll a partial transaction back."""

    inspection = inspect_transactions(layout)
    if inspection.invalid:
        raise RecoveryConflictError(inspection.detail or "Invalid transaction state")
    if not inspection.intents:
        return ()
    intent_path = inspection.intents[0]
    safe = SafeRoot(layout.root)
    if client is not layout.client:
        raise RecoveryConflictError("Recovery client does not match the derived layout")
    lock.heartbeat()
    current_inspection = inspect_transactions(layout)
    if current_inspection.invalid:
        raise RecoveryConflictError(current_inspection.detail or "Invalid transaction state")
    if current_inspection.intents != (intent_path,):
        raise RecoveryConflictError("Transaction set changed before recovery")
    intent_snapshot = safe.inspect_file(intent_path)
    if intent_snapshot.content is None:
        raise RecoveryConflictError("Transaction intent content is unavailable")
    _value, operations = _parse_intent(
        intent_snapshot.content,
        intent_path=intent_path,
        layout=layout,
    )
    current_transition = _recovery_transition(operations, client, layout.manifest_path)
    if expected_transition is not None and current_transition != expected_transition:
        raise RecoveryConflictError("Transaction intent changed after authority preflight")
    if authority_check is not None:
        authority_check()
    _fence_recovery_step(lock, safe, intent_path, intent_snapshot)
    transaction_root = str(PurePosixPath(intent_path).parent)
    targets = _inspect_recovery_targets(safe, operations)
    _validate_recovery_internal_files(safe, operations, targets)
    post_matches = all(
        _matches_recorded_state(target, operation["postimage"])
        for operation, target in zip(operations, targets, strict=True)
    )
    if post_matches:
        _resolve_recovered_transaction(
            safe=safe,
            lock=lock,
            transaction_root=transaction_root,
            intent_path=intent_path,
            intent_snapshot=intent_snapshot,
            operations=operations,
            resolved_state="postimage",
            authority_check=authority_check,
        )
        return tuple(str(operation["target"]) for operation in operations)

    manifest_index = next(
        (
            index
            for index, operation in enumerate(operations)
            if operation.get("target") == layout.manifest_path
        ),
        None,
    )
    if manifest_index is None:
        raise RecoveryConflictError("Transaction intent does not include its manifest")
    manifest_pre = operations[manifest_index]["expected"]
    manifest_target = targets[manifest_index]
    if not _matches_recorded_state(manifest_target, manifest_pre):
        raise RecoveryConflictError("Committed manifest has an incomplete target set")
    for operation, target in zip(operations, targets, strict=True):
        if not (
            _matches_recorded_state(target, operation["expected"])
            or _matches_recorded_state(target, operation["postimage"])
        ):
            raise RecoveryConflictError("A recovery target matches neither recorded state")
    for operation in reversed(operations):
        _fence_recovery_step(lock, safe, intent_path, intent_snapshot)
        target = _require_recorded_target(safe, operation)
        expected = operation["expected"]
        target_path = operation["target"]
        assert isinstance(target_path, str)
        if _matches_recorded_state(target, expected):
            continue
        if expected == "absent":
            if not _matches_recorded_state(target, operation["postimage"]):
                raise RecoveryConflictError("Create target changed before recovery removal")
            if authority_check is not None:
                authority_check()
            if not safe.unlink(target_path, expected=target):
                raise RecoveryConflictError("Create target disappeared during recovery removal")
            if safe.inspect_file(target_path).exists:
                raise RecoveryConflictError("Create target remains after recovery removal")
            continue

        # A restore consumes one exact verified preimage.  Re-read both the target
        # and every candidate after the fresh lock fence; never act on the snapshots
        # collected during initial classification.
        candidate_paths = [operation.get("removed"), operation.get("backup")]
        backup_path = None
        backup_snapshot = None
        for candidate in candidate_paths:
            if not isinstance(candidate, str):
                continue
            candidate_snapshot = safe.inspect_file(candidate)
            if candidate_snapshot.exists and not _matches_recorded_state(
                candidate_snapshot,
                expected,
            ):
                raise RecoveryConflictError("Recovery preimage evidence was modified")
            if _matches_recorded_state(candidate_snapshot, expected):
                backup_path = candidate
                backup_snapshot = candidate_snapshot
                break
        if backup_path is None or backup_snapshot is None:
            raise RecoveryConflictError("Recovery preimage backup is invalid")
        target = _require_recorded_target(safe, operation)
        if _matches_recorded_state(target, expected):
            continue
        if not _matches_recorded_state(target, operation["postimage"]):
            raise RecoveryConflictError("Recovery target changed before preimage restore")
        backup_snapshot = safe.inspect_file(backup_path)
        if not _matches_recorded_state(backup_snapshot, expected):
            raise RecoveryConflictError("Recovery preimage changed before restore")
        if authority_check is not None:
            authority_check()
        safe.replace(
            backup_path,
            target_path,
            expected_source=backup_snapshot,
            expected_target=target,
        )
        restored = safe.inspect_file(target_path)
        if not _matches_recorded_state(restored, expected):
            raise RecoveryConflictError("Recovery preimage restore did not hold")

    _resolve_recovered_transaction(
        safe=safe,
        lock=lock,
        transaction_root=transaction_root,
        intent_path=intent_path,
        intent_snapshot=intent_snapshot,
        operations=operations,
        resolved_state="expected",
        authority_check=authority_check,
    )
    return tuple(str(operation["target"]) for operation in operations)
