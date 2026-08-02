"""Trusted per-project agent-install records stored outside project roots.

The project-local adapter manifest is bookkeeping, not mutation authority.  This
module provides the user-config trust anchor required to authenticate its exact
bytes.  A pending transition spans the unavoidable cross-filesystem window between
the user-config record and the rollback-capable project transaction.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_config_path

from ._safe_fs import FileSnapshot, SafeFilesystemError, SafeRoot
from .errors import AdapterConflictError, InvalidAdapterStateError
from .models import AgentClient

INSTALL_RECORD_SCHEMA_VERSION = 1
INSTALL_RECORD_FILENAME = "agent-adapter-installs.json"
_GUARD_TIMEOUT_SECONDS = 5.0
_GUARD_POLL_SECONDS = 0.01
_HASH_PREFIX = "sha256:"
_HASH_LENGTH = len(_HASH_PREFIX) + 64


class InstallRecordError(InvalidAdapterStateError):
    """Raised when the trusted install-record store is unsafe or malformed."""


class InstallRecordConflictError(AdapterConflictError):
    """Raised when a trusted-record compare-and-swap precondition changed."""


class RecoveryDisposition(StrEnum):
    """Which authenticated side of a pending project transaction is present."""

    PREIMAGE = "preimage"
    POSTIMAGE = "postimage"


@dataclass(frozen=True, slots=True)
class InstallTransition:
    """Trusted old/new manifest and exact operation-set binding."""

    from_manifest_digest: str | None
    to_manifest_digest: str | None
    operations_digest: str

    def __post_init__(self) -> None:
        _require_optional_hash(self.from_manifest_digest, "from_manifest_digest")
        _require_optional_hash(self.to_manifest_digest, "to_manifest_digest")
        _require_hash(self.operations_digest, "operations_digest")
        if self.from_manifest_digest is None and self.to_manifest_digest is None:
            raise ValueError("An install transition must have a preimage or postimage")

    def to_dict(self) -> dict[str, object]:
        return {
            "from_manifest_digest": self.from_manifest_digest,
            "to_manifest_digest": self.to_manifest_digest,
            "operations_digest": self.operations_digest,
        }


@dataclass(frozen=True, slots=True)
class TrustedInstallRecord:
    """One root/client manifest authority entry."""

    root: Path
    client: AgentClient
    manifest_path: str
    trusted_manifest_digest: str | None
    pending_transition: InstallTransition | None = None

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise ValueError("Trusted install roots must be absolute")
        _require_manifest_path(self.manifest_path, self.client)
        _require_optional_hash(self.trusted_manifest_digest, "trusted_manifest_digest")
        if self.pending_transition is None:
            if self.trusted_manifest_digest is None:
                raise ValueError("A stable install record requires a trusted manifest digest")
        elif self.pending_transition.from_manifest_digest != self.trusted_manifest_digest:
            raise ValueError("Pending transition preimage must equal the trusted digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "adapter": self.client.value,
            "manifest_path": self.manifest_path,
            "trusted_manifest_digest": self.trusted_manifest_digest,
            "pending_transition": (
                None if self.pending_transition is None else self.pending_transition.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class InstallRecordObservation:
    """One selected record state captured during a read-only plan."""

    root: Path
    client: AgentClient
    manifest_path: str
    record: TrustedInstallRecord | None
    fingerprint: str

    @property
    def has_pending_transition(self) -> bool:
        return self.record is not None and self.record.pending_transition is not None

    @property
    def pending(self) -> PendingInstallRecord | None:
        """Return the exact recovery token when this observation is pending."""

        if self.record is None or self.record.pending_transition is None:
            return None
        return _pending_token(self.record)

    def authenticates(self, manifest_digest: str | None) -> bool:
        """Authenticate stable state only; pending state must go through recovery."""

        return (
            self.record is not None
            and self.record.pending_transition is None
            and self.record.trusted_manifest_digest is not None
            and manifest_digest == self.record.trusted_manifest_digest
        )


@dataclass(frozen=True, slots=True)
class PendingInstallRecord:
    """Exact trusted-record CAS token returned before project mutation."""

    root: Path
    client: AgentClient
    manifest_path: str
    record: TrustedInstallRecord
    fingerprint: str

    @property
    def transition(self) -> InstallTransition:
        transition = self.record.pending_transition
        if transition is None:  # pragma: no cover - constructor is internal
            raise AssertionError("Pending token does not carry a transition")
        return transition


@dataclass(frozen=True, slots=True)
class _InstallRecordSnapshot:
    records: tuple[TrustedInstallRecord, ...] = ()

    def selected(
        self,
        root: Path,
        client: AgentClient,
    ) -> TrustedInstallRecord | None:
        key = _root_key(root)
        return next(
            (
                record
                for record in self.records
                if _root_key(record.root) == key and record.client is client
            ),
            None,
        )

    def replacing(
        self,
        root: Path,
        client: AgentClient,
        replacement: TrustedInstallRecord | None,
    ) -> _InstallRecordSnapshot:
        key = _root_key(root)
        retained = [
            record
            for record in self.records
            if not (_root_key(record.root) == key and record.client is client)
        ]
        if replacement is not None:
            retained.append(replacement)
        return _InstallRecordSnapshot(tuple(sorted(retained, key=_record_sort_key)))

    def to_dict(self) -> dict[str, object]:
        projects: list[dict[str, object]] = []
        grouped: dict[str, list[TrustedInstallRecord]] = {}
        display_roots: dict[str, Path] = {}
        for record in self.records:
            key = _root_key(record.root)
            grouped.setdefault(key, []).append(record)
            display_roots[key] = record.root
        for key in sorted(grouped):
            projects.append(
                {
                    "root": str(display_roots[key]),
                    "adapters": [
                        record.to_dict()
                        for record in sorted(grouped[key], key=lambda item: item.client.value)
                    ],
                }
            )
        return {
            "schema_version": INSTALL_RECORD_SCHEMA_VERSION,
            "projects": projects,
        }


class TrustedInstallStore:
    """Atomic API over the platform-appropriate per-user trust record."""

    def __init__(self) -> None:
        self.path = (
            user_config_path("workctx", appauthor=False) / INSTALL_RECORD_FILENAME
        ).absolute()

    def observe(
        self,
        root: Path,
        client: AgentClient,
        manifest_path: str,
    ) -> InstallRecordObservation:
        """Read one exact root/client entry without mutating user configuration."""

        canonical_root = _canonical_root(root)
        _require_manifest_path(manifest_path, client)
        snapshot, _file = self._load(canonical_root)
        record = snapshot.selected(canonical_root, client)
        if record is not None and record.manifest_path != manifest_path:
            raise InstallRecordError(
                "Trusted install record manifest path does not match the derived layout"
            )
        return _observation(canonical_root, client, manifest_path, record)

    def begin_transition(
        self,
        observation: InstallRecordObservation,
        *,
        next_manifest_digest: str | None,
        operations_digest: str,
    ) -> PendingInstallRecord:
        """Persist an exact old/new transition after selected-entry CAS validation."""

        _validate_observation(observation)
        _require_optional_hash(next_manifest_digest, "next_manifest_digest")
        _require_hash(operations_digest, "operations_digest")
        if observation.has_pending_transition:
            raise InstallRecordConflictError(
                "A trusted install transition already requires recovery"
            )
        current_digest = (
            None if observation.record is None else observation.record.trusted_manifest_digest
        )
        if current_digest is None and next_manifest_digest is None:
            raise ValueError("An absent install cannot transition to another absent state")
        transition = InstallTransition(
            from_manifest_digest=current_digest,
            to_manifest_digest=next_manifest_digest,
            operations_digest=operations_digest,
        )
        pending_record = TrustedInstallRecord(
            root=observation.root,
            client=observation.client,
            manifest_path=observation.manifest_path,
            trusted_manifest_digest=current_digest,
            pending_transition=transition,
        )
        with self._mutation_guard(observation.root):
            snapshot, file_snapshot = self._load(observation.root)
            selected = snapshot.selected(observation.root, observation.client)
            if selected != observation.record:
                raise InstallRecordConflictError(
                    "Trusted install record changed after the dry run; replan"
                )
            updated = snapshot.replacing(
                observation.root,
                observation.client,
                pending_record,
            )
            self._save(updated, file_snapshot, observation.root)
        return _pending_token(pending_record)

    def verify_recovery(
        self,
        pending: PendingInstallRecord,
        *,
        operations_digest: str,
        actual_manifest_digest: str | None,
    ) -> RecoveryDisposition:
        """Verify operation binding and classify an exact pre/post manifest state."""

        _validate_pending(pending)
        _require_hash(operations_digest, "operations_digest")
        _require_optional_hash(actual_manifest_digest, "actual_manifest_digest")
        transition = pending.transition
        if operations_digest != transition.operations_digest:
            raise InstallRecordConflictError(
                "Recovery operation set does not match the trusted install transition"
            )
        if actual_manifest_digest == transition.to_manifest_digest:
            return RecoveryDisposition.POSTIMAGE
        if actual_manifest_digest == transition.from_manifest_digest:
            return RecoveryDisposition.PREIMAGE
        raise InstallRecordConflictError(
            "Project manifest matches neither trusted transition state"
        )

    def resolve_transition(
        self,
        pending: PendingInstallRecord,
        *,
        operations_digest: str,
        actual_manifest_digest: str | None,
    ) -> InstallRecordObservation:
        """CAS-resolve a pending transition to its authenticated actual state."""

        disposition = self.verify_recovery(
            pending,
            operations_digest=operations_digest,
            actual_manifest_digest=actual_manifest_digest,
        )
        transition = pending.transition
        resolved_digest = (
            transition.to_manifest_digest
            if disposition is RecoveryDisposition.POSTIMAGE
            else transition.from_manifest_digest
        )
        replacement = (
            None
            if resolved_digest is None
            else TrustedInstallRecord(
                root=pending.root,
                client=pending.client,
                manifest_path=pending.manifest_path,
                trusted_manifest_digest=resolved_digest,
            )
        )
        with self._mutation_guard(pending.root):
            snapshot, file_snapshot = self._load(pending.root)
            selected = snapshot.selected(pending.root, pending.client)
            if selected != pending.record:
                raise InstallRecordConflictError(
                    "Trusted install transition changed before resolution"
                )
            updated = snapshot.replacing(pending.root, pending.client, replacement)
            self._save(updated, file_snapshot, pending.root)
        return _observation(
            pending.root,
            pending.client,
            pending.manifest_path,
            replacement,
        )

    @contextmanager
    def _mutation_guard(self, root: Path) -> Iterator[None]:
        _require_store_outside_root(self.path, root)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstallRecordError(
                "Unable to create the trusted install-record directory"
            ) from error
        _require_store_outside_root(self.path, root)
        resolved_parent = _require_directory(self.path.parent)
        guard_path = resolved_parent / f".{INSTALL_RECORD_FILENAME}.lock"
        descriptor = _open_guard(guard_path)
        deadline = time.monotonic() + _GUARD_TIMEOUT_SECONDS
        try:
            while not _try_lock_guard(descriptor):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InstallRecordConflictError(
                        "Trusted install records are currently being modified"
                    ) from None
                time.sleep(min(_GUARD_POLL_SECONDS, remaining))
            yield
        finally:
            with suppress(OSError):
                _unlock_guard(descriptor)
            os.close(descriptor)

    def _load(
        self,
        current_root: Path,
    ) -> tuple[_InstallRecordSnapshot, FileSnapshot | None]:
        _require_store_outside_root(self.path, current_root)
        if not self.path.parent.exists():
            return _InstallRecordSnapshot(), None
        resolved_parent = _require_directory(self.path.parent)
        safe = SafeRoot(resolved_parent)
        try:
            file_snapshot = safe.inspect_file(INSTALL_RECORD_FILENAME)
        except SafeFilesystemError as error:
            raise InstallRecordError("Trusted install record is unsafe") from error
        if not file_snapshot.exists:
            return _InstallRecordSnapshot(), file_snapshot
        if file_snapshot.content is None:
            raise InstallRecordError("Trusted install record content is unavailable")
        try:
            raw: Any = json.loads(
                file_snapshot.content,
                object_pairs_hook=_reject_duplicate_object_keys,
            )
            snapshot = _parse_snapshot(raw)
        except (
            OSError,
            RuntimeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            raise InstallRecordError("Trusted install record is malformed") from error
        for record in snapshot.records:
            _require_store_outside_root(self.path, record.root)
        return snapshot, file_snapshot

    def _save(
        self,
        snapshot: _InstallRecordSnapshot,
        expected_file: FileSnapshot | None,
        current_root: Path,
    ) -> None:
        _require_store_outside_root(self.path, current_root)
        resolved_parent = _require_directory(self.path.parent)
        safe = SafeRoot(resolved_parent)
        try:
            current = safe.inspect_file(INSTALL_RECORD_FILENAME)
        except SafeFilesystemError as error:
            raise InstallRecordError("Trusted install record became unsafe") from error
        if expected_file is None:
            if current.exists:
                raise InstallRecordConflictError(
                    "Trusted install-record file appeared during mutation"
                )
        elif not current.matches(expected_file.identity, expected_file.content_hash):
            raise InstallRecordConflictError("Trusted install-record file changed during mutation")
        payload = (
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
        temp_name = f".{INSTALL_RECORD_FILENAME}.{secrets.token_hex(8)}.tmp"
        staged: FileSnapshot | None = None
        try:
            staged = safe.write_exclusive(temp_name, payload, mode=0o600)
            safe.replace(
                temp_name,
                INSTALL_RECORD_FILENAME,
                expected_source=staged,
                expected_target=current,
            )
            safe.fsync_directory(".")
        except (OSError, SafeFilesystemError) as error:
            raise InstallRecordError("Unable to atomically save trusted install records") from error
        finally:
            if staged is not None:
                with suppress(OSError, SafeFilesystemError):
                    remaining = safe.inspect_file(temp_name)
                    if remaining.exists:
                        safe.unlink(temp_name, expected=remaining)


def _parse_snapshot(value: object) -> _InstallRecordSnapshot:
    if not isinstance(value, dict) or set(value) != {"schema_version", "projects"}:
        raise ValueError("Install-record file has an invalid object shape")
    version = value["schema_version"]
    if type(version) is not int or version != INSTALL_RECORD_SCHEMA_VERSION:
        raise ValueError("Install-record schema version is unsupported")
    projects = value["projects"]
    if not isinstance(projects, list):
        raise ValueError("Install-record projects must be an array")
    records: list[TrustedInstallRecord] = []
    root_keys: set[str] = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != {"root", "adapters"}:
            raise ValueError("Install-record project has an invalid object shape")
        raw_root = project["root"]
        adapters = project["adapters"]
        if not isinstance(raw_root, str) or not raw_root:
            raise ValueError("Install-record project root must be a nonempty string")
        root = Path(raw_root)
        if not root.is_absolute():
            raise ValueError("Install-record project roots must be absolute")
        if root.resolve(strict=False) != root:
            raise ValueError("Install-record project roots must be canonical physical paths")
        root_key = _root_key(root)
        if root_key in root_keys:
            raise ValueError("Install-record project roots collide")
        root_keys.add(root_key)
        if not isinstance(adapters, list) or not adapters:
            raise ValueError("Install-record project adapters must be a nonempty array")
        clients: set[AgentClient] = set()
        for adapter in adapters:
            record = _parse_record(root, adapter)
            if record.client in clients:
                raise ValueError("Install-record adapters must be unique per project")
            clients.add(record.client)
            records.append(record)
    return _InstallRecordSnapshot(tuple(sorted(records, key=_record_sort_key)))


def _parse_record(root: Path, value: object) -> TrustedInstallRecord:
    fields = {
        "adapter",
        "manifest_path",
        "trusted_manifest_digest",
        "pending_transition",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Install-record adapter has an invalid object shape")
    try:
        client = AgentClient(value["adapter"])
    except (TypeError, ValueError) as error:
        raise ValueError("Install-record adapter name is invalid") from error
    manifest_path = value["manifest_path"]
    if not isinstance(manifest_path, str):
        raise ValueError("Install-record manifest path must be a string")
    trusted = value["trusted_manifest_digest"]
    if trusted is not None and not isinstance(trusted, str):
        raise ValueError("Install-record trusted digest is invalid")
    raw_transition = value["pending_transition"]
    transition = None
    if raw_transition is not None:
        transition = _parse_transition(raw_transition)
    return TrustedInstallRecord(
        root=root,
        client=client,
        manifest_path=manifest_path,
        trusted_manifest_digest=trusted,
        pending_transition=transition,
    )


def _parse_transition(value: object) -> InstallTransition:
    fields = {
        "from_manifest_digest",
        "to_manifest_digest",
        "operations_digest",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Install-record transition has an invalid object shape")
    before = value["from_manifest_digest"]
    after = value["to_manifest_digest"]
    operations = value["operations_digest"]
    if before is not None and not isinstance(before, str):
        raise ValueError("Install-record transition preimage digest is invalid")
    if after is not None and not isinstance(after, str):
        raise ValueError("Install-record transition postimage digest is invalid")
    if not isinstance(operations, str):
        raise ValueError("Install-record operation digest is invalid")
    return InstallTransition(before, after, operations)


def _observation(
    root: Path,
    client: AgentClient,
    manifest_path: str,
    record: TrustedInstallRecord | None,
) -> InstallRecordObservation:
    value = {
        "root": str(root),
        "adapter": client.value,
        "manifest_path": manifest_path,
        "record": None if record is None else record.to_dict(),
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = _digest(canonical)
    return InstallRecordObservation(root, client, manifest_path, record, fingerprint)


def _pending_token(record: TrustedInstallRecord) -> PendingInstallRecord:
    observation = _observation(record.root, record.client, record.manifest_path, record)
    return PendingInstallRecord(
        root=record.root,
        client=record.client,
        manifest_path=record.manifest_path,
        record=record,
        fingerprint=observation.fingerprint,
    )


def _validate_observation(observation: InstallRecordObservation) -> None:
    canonical_root = _canonical_root(observation.root)
    if canonical_root != observation.root:
        raise ValueError("Install-record observation root is not canonical")
    _require_manifest_path(observation.manifest_path, observation.client)
    expected = _observation(
        observation.root,
        observation.client,
        observation.manifest_path,
        observation.record,
    )
    if expected != observation:
        raise ValueError("Install-record observation fingerprint is invalid")
    if observation.record is not None and (
        observation.record.root != observation.root
        or observation.record.client is not observation.client
        or observation.record.manifest_path != observation.manifest_path
    ):
        raise ValueError("Install-record observation fields do not match its record")


def _validate_pending(pending: PendingInstallRecord) -> None:
    canonical_root = _canonical_root(pending.root)
    if canonical_root != pending.root:
        raise ValueError("Pending install-record root is not canonical")
    if (
        pending.record.root != pending.root
        or pending.record.client is not pending.client
        or pending.record.manifest_path != pending.manifest_path
        or pending.record.pending_transition is None
    ):
        raise ValueError("Pending install-record token is inconsistent")
    if _pending_token(pending.record) != pending:
        raise ValueError("Pending install-record fingerprint is invalid")


def _canonical_root(root: Path) -> Path:
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallRecordError("Installation root is unavailable") from error
    if not resolved.is_dir():
        raise InstallRecordError("Installation root must be a directory")
    return resolved


def _require_manifest_path(path: str, client: AgentClient) -> None:
    expected_suffix = f"agent-adapters/{client.value}/skill-manifest.json"
    if path not in {
        f".workctx/{expected_suffix}",
        f"98_state/{expected_suffix}",
    }:
        raise ValueError("Trusted install manifest path does not match its adapter")


def _require_hash(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _HASH_LENGTH
        or not value.startswith(_HASH_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[len(_HASH_PREFIX) :])
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _require_optional_hash(value: object, field: str) -> None:
    if value is not None:
        _require_hash(value, field)


def _root_key(root: Path) -> str:
    return os.path.normcase(os.path.normpath(str(root)))


def _record_sort_key(record: TrustedInstallRecord) -> tuple[str, str]:
    return (_root_key(record.root), record.client.value)


def _digest(content: bytes) -> str:
    return _HASH_PREFIX + hashlib.sha256(content).hexdigest()


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Install-record JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _require_store_outside_root(path: Path, root: Path) -> None:
    try:
        store = path.resolve(strict=False)
        project = root.resolve(strict=False)
        common = os.path.commonpath((os.path.normcase(store), os.path.normcase(project)))
    except ValueError:
        # Different Windows drives cannot contain one another.
        return
    except (OSError, RuntimeError) as error:
        raise InstallRecordError("Unable to verify the install-record trust boundary") from error
    if common == os.path.normcase(project):
        raise InstallRecordError("Trusted install records must be outside every project root")


def _require_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallRecordError("Trusted install-record directory is unavailable") from error
    if not resolved.is_dir():
        raise InstallRecordError("Trusted install-record parent must be a directory")
    return resolved


def _open_guard(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise InstallRecordError("Unable to open trusted install-record guard") from error
    try:
        opened = os.fstat(descriptor)
        linked = path.lstat()
        attributes = getattr(linked, "st_file_attributes", 0)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(linked.st_mode)
            or bool(attributes & 0x400)
            or not os.path.samestat(opened, linked)
        ):
            raise InstallRecordError("Trusted install-record guard must be a non-link regular file")
        if opened.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
            _fsync_directory(path.parent)
        return descriptor
    except InstallRecordError:
        os.close(descriptor)
        raise
    except OSError as error:
        os.close(descriptor)
        raise InstallRecordError("Unable to inspect trusted install-record guard") from error
    except BaseException:
        os.close(descriptor)
        raise


def _try_lock_guard(descriptor: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(  # type: ignore[attr-defined]
                descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
            )
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise InstallRecordError("Unable to lock trusted install-record guard") from error
    return True


def _unlock_guard(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)  # type: ignore[attr-defined]


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


__all__ = [
    "INSTALL_RECORD_FILENAME",
    "INSTALL_RECORD_SCHEMA_VERSION",
    "InstallRecordConflictError",
    "InstallRecordError",
    "InstallRecordObservation",
    "InstallTransition",
    "PendingInstallRecord",
    "RecoveryDisposition",
    "TrustedInstallRecord",
    "TrustedInstallStore",
]
