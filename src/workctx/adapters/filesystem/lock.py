"""Nonce-based context writer lock with stale takeover and fencing."""

from __future__ import annotations

import errno
import json
import os
import secrets
import socket
import stat
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from workctx import __version__
from workctx.adapters.filesystem._paths import canonical_context_root, resolve_context_path
from workctx.errors import ConflictError, ContextBoundaryError

DEFAULT_STALE_AFTER = timedelta(minutes=10)
_NONCE_HEX_LENGTH = 32
_REPLACE_ATTEMPTS = 10
_REPLACE_INITIAL_DELAY_SECONDS = 0.01
_GUARD_ATTEMPTS = 20
_GUARD_INITIAL_DELAY_SECONDS = 0.001
_GUARD_MAX_DELAY_SECONDS = 0.05
_GUARD_MALFORMED_STALE_AFTER = timedelta(hours=1)
_LOCK_FIELDS = (
    "pid",
    "hostname",
    "session_id",
    "tool_version",
    "acquired_at",
    "heartbeat_at",
    "nonce",
)
_GUARD_FIELDS = ("pid", "hostname", "acquired_at", "nonce", "ticket")
_GUARD_CHOOSING_PREFIX = "lock.guard.choosing-"
_GUARD_TICKET_PREFIX = "lock.guard.ticket-"
_GUARD_CANCELLED_PREFIX = "lock.guard.cancelled-"


class LockError(ConflictError):
    """Base class for expected context-lock conflicts."""


class LockHeldError(LockError):
    """Raised when another non-stale holder owns the context lock."""


class LockFenceError(LockError):
    """Raised when a lease no longer owns the nonce in ``lock.json``."""


@dataclass(frozen=True, slots=True)
class LockMetadata:
    """Validated owner metadata stored in ``98_state/lock.json``."""

    pid: int
    hostname: str
    session_id: str
    tool_version: str
    acquired_at: datetime
    heartbeat_at: datetime
    nonce: str

    def to_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "hostname": self.hostname,
            "session_id": self.session_id,
            "tool_version": self.tool_version,
            "acquired_at": _format_utc(self.acquired_at),
            "heartbeat_at": _format_utc(self.heartbeat_at),
            "nonce": self.nonce,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict) or set(value) != set(_LOCK_FIELDS):
            raise ValueError("Lock metadata has an invalid object shape")
        pid = value["pid"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Lock pid must be a positive integer")
        hostname = _required_string(value["hostname"], "hostname")
        session_id = _required_string(value["session_id"], "session_id")
        tool_version = _required_string(value["tool_version"], "tool_version")
        acquired_at = _parse_utc(value["acquired_at"], "acquired_at")
        heartbeat_at = _parse_utc(value["heartbeat_at"], "heartbeat_at")
        nonce = _required_string(value["nonce"], "nonce")
        if len(nonce) != _NONCE_HEX_LENGTH or any(char not in "0123456789abcdef" for char in nonce):
            raise ValueError("Lock nonce must be a lowercase 128-bit hexadecimal value")
        return cls(
            pid=pid,
            hostname=hostname,
            session_id=session_id,
            tool_version=tool_version,
            acquired_at=acquired_at,
            heartbeat_at=heartbeat_at,
            nonce=nonce,
        )


@dataclass(frozen=True, slots=True)
class LockInspection:
    """Read-only assessment of the current lock file."""

    exists: bool
    stale: bool
    reason: str
    metadata: LockMetadata | None = None


@dataclass(frozen=True, slots=True)
class _LockSnapshot:
    payload: bytes
    mtime_ns: int
    file_id: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _GuardMetadata:
    pid: int
    hostname: str
    acquired_at: datetime
    nonce: str
    ticket: int

    def to_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "hostname": self.hostname,
            "acquired_at": _format_utc(self.acquired_at),
            "nonce": self.nonce,
            "ticket": self.ticket,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, dict) or set(value) != set(_GUARD_FIELDS):
            raise ValueError("Mutation-guard metadata has an invalid object shape")
        pid = value["pid"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Mutation-guard pid must be a positive integer")
        hostname = _required_string(value["hostname"], "hostname")
        acquired_at = _parse_utc(value["acquired_at"], "acquired_at")
        nonce = _required_string(value["nonce"], "nonce")
        if len(nonce) != _NONCE_HEX_LENGTH or any(char not in "0123456789abcdef" for char in nonce):
            raise ValueError("Mutation-guard nonce must be a lowercase 128-bit hexadecimal value")
        ticket = value["ticket"]
        if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket < 0:
            raise ValueError("Mutation-guard ticket must be a non-negative integer")
        return cls(
            pid=pid,
            hostname=hostname,
            acquired_at=acquired_at,
            nonce=nonce,
            ticket=ticket,
        )


@dataclass(frozen=True, slots=True)
class _MutationGuard:
    path: Path
    metadata: _GuardMetadata


class ContextLock:
    """An acquired, nonce-fenced writer lease for one context."""

    def __init__(
        self,
        context_root: Path,
        metadata: LockMetadata,
        *,
        stale_after: timedelta,
    ) -> None:
        self._context_root = context_root
        self._metadata = metadata
        self._stale_after = stale_after
        self._released = False

    @classmethod
    def acquire(
        cls,
        context_root: Path,
        *,
        session_id: str,
        tool_version: str = __version__,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> Self:
        """Acquire the context lock, observably taking over a stale holder."""

        if not session_id:
            raise ValueError("session_id must not be empty")
        if not tool_version:
            raise ValueError("tool_version must not be empty")
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")

        root = canonical_context_root(context_root)
        state_dir = resolve_context_path(root, "98_state", allowed_prefixes=("98_state",))
        state_dir.mkdir(parents=False, exist_ok=True)
        if not state_dir.is_dir():
            raise ContextBoundaryError("98_state must be a directory")
        lock_path = resolve_context_path(
            root,
            "98_state/lock.json",
            allowed_prefixes=("98_state",),
        )

        guard = _acquire_mutation_guard(root)
        try:
            for _attempt in range(32):
                now = _utc_now()
                metadata = LockMetadata(
                    pid=_local_pid(),
                    hostname=_local_hostname(),
                    session_id=session_id,
                    tool_version=tool_version,
                    acquired_at=now,
                    heartbeat_at=now,
                    nonce=_new_nonce(),
                )
                try:
                    _write_exclusive(lock_path, _encode_metadata(metadata))
                except FileExistsError:
                    snapshot = _read_snapshot(lock_path)
                    if snapshot is None:
                        continue
                    inspection = _inspect_snapshot(
                        snapshot,
                        stale_after=stale_after,
                        now=_utc_now(),
                        local_hostname=_local_hostname(),
                    )
                    if not inspection.stale:
                        raise LockHeldError(
                            "The context write lock is held by another session"
                        ) from None
                    if not _archive_if_unchanged(
                        lock_path,
                        snapshot,
                        stale_after=stale_after,
                    ):
                        continue
                    continue
                _fsync_directory(state_dir)
                return cls(root, metadata, stale_after=stale_after)
            raise LockHeldError("Unable to acquire the context write lock after concurrent changes")
        finally:
            _release_mutation_guard(guard)

    @property
    def context_root(self) -> Path:
        return self._context_root

    @property
    def lock_path(self) -> Path:
        return resolve_context_path(
            self._context_root,
            "98_state/lock.json",
            allowed_prefixes=("98_state",),
        )

    @property
    def metadata(self) -> LockMetadata:
        return self._metadata

    @property
    def nonce(self) -> str:
        return self._metadata.nonce

    @property
    def released(self) -> bool:
        return self._released

    def verify_fence(self) -> LockMetadata:
        """Verify that ``lock.json`` still carries this lease's nonce."""

        if self._released:
            raise LockFenceError("The context lock lease has already been released")
        return verify_lock_fence(self._context_root, self.nonce)

    def heartbeat(self) -> LockMetadata:
        """Atomically refresh the heartbeat while preserving owner identity."""

        refreshed = LockMetadata(
            pid=self._metadata.pid,
            hostname=self._metadata.hostname,
            session_id=self._metadata.session_id,
            tool_version=self._metadata.tool_version,
            acquired_at=self._metadata.acquired_at,
            heartbeat_at=_utc_now(),
            nonce=self._metadata.nonce,
        )
        tmp_path = resolve_context_path(
            self._context_root,
            "98_state/lock.json.tmp",
            allowed_prefixes=("98_state",),
        )
        guard = _acquire_mutation_guard(self._context_root)
        try:
            self.verify_fence()
            _reject_unsafe_file_leaf(tmp_path, allow_missing=True)
            _write_fsynced(tmp_path, _encode_metadata(refreshed))
            _replace_heartbeat_with_retry(self, tmp_path, self.lock_path)
        finally:
            _release_mutation_guard(guard)
        _fsync_directory(self.lock_path.parent)
        self._metadata = refreshed
        return refreshed

    def release(self) -> None:
        """Release this lease without ever unlinking a successor's lock."""

        if self._released:
            return
        guard = _acquire_mutation_guard(self._context_root)
        try:
            self.verify_fence()
            try:
                _unlink_with_retry(self.lock_path)
            except FileNotFoundError as exc:
                raise LockFenceError("The context lock disappeared before release") from exc
        finally:
            _release_mutation_guard(guard)
        _fsync_directory(self.lock_path.parent)
        self._released = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def inspect_context_lock(
    context_root: Path,
    *,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> LockInspection:
    """Inspect lock freshness without modifying the context."""

    root = canonical_context_root(context_root)
    lock_path = resolve_context_path(root, "98_state/lock.json", allowed_prefixes=("98_state",))
    snapshot = _read_snapshot(lock_path)
    if snapshot is None:
        return LockInspection(exists=False, stale=False, reason="absent")
    return _inspect_snapshot(
        snapshot,
        stale_after=stale_after,
        now=_utc_now(),
        local_hostname=_local_hostname(),
    )


def verify_lock_fence(context_root: Path, expected_nonce: str) -> LockMetadata:
    """Verify nonce ownership directly from the context's canonical lock path."""

    root = canonical_context_root(context_root)
    lock_path = resolve_context_path(root, "98_state/lock.json", allowed_prefixes=("98_state",))
    snapshot = _read_snapshot(lock_path)
    if snapshot is None:
        raise LockFenceError("The context write lock is absent")
    try:
        metadata = _decode_metadata(snapshot.payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise LockFenceError("The context write lock is malformed") from exc
    if not secrets.compare_digest(metadata.nonce, expected_nonce):
        raise LockFenceError("The context write lock is owned by another session")
    return metadata


def _inspect_snapshot(
    snapshot: _LockSnapshot,
    *,
    stale_after: timedelta,
    now: datetime,
    local_hostname: str,
) -> LockInspection:
    try:
        metadata = _decode_metadata(snapshot.payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        modified_at = datetime.fromtimestamp(snapshot.mtime_ns / 1_000_000_000, tz=UTC)
        stale = now - modified_at > stale_after
        return LockInspection(
            exists=True,
            stale=stale,
            reason="unparseable_mtime_expired" if stale else "unparseable_recent",
        )

    if now - metadata.heartbeat_at > stale_after:
        return LockInspection(True, True, "heartbeat_expired", metadata)
    if metadata.hostname == local_hostname and not _pid_is_alive(metadata.pid):
        return LockInspection(True, True, "same_host_pid_missing", metadata)
    return LockInspection(True, False, "active", metadata)


def _archive_if_unchanged(
    lock_path: Path,
    expected: _LockSnapshot,
    *,
    stale_after: timedelta,
) -> bool:
    current = _read_snapshot(lock_path)
    if current is None or current != expected:
        return False
    inspection = _inspect_snapshot(
        current,
        stale_after=stale_after,
        now=_utc_now(),
        local_hostname=_local_hostname(),
    )
    if not inspection.stale:
        return False

    timestamp = _utc_now().strftime("%Y%m%dT%H%M%S.%fZ")
    for suffix in range(1000):
        discriminator = "" if suffix == 0 else f"-{suffix}"
        archive = lock_path.with_name(f"lock.stale-{timestamp}{discriminator}.json")
        try:
            _link_no_replace(lock_path, archive)
        except FileNotFoundError:
            return False
        except FileExistsError:
            continue
        except OSError as exc:
            raise LockError(f"Unable to preserve stale-lock evidence: {exc}") from exc

        archived = _read_snapshot(archive)
        current = _read_snapshot(lock_path)
        if archived != expected or current != expected:
            if archived is not None:
                _unlink_if_unchanged(archive, archived)
            return False
        _fsync_directory(lock_path.parent)
        _unlink_with_retry(lock_path)
        _fsync_directory(lock_path.parent)
        return True
    raise LockError("Unable to allocate a unique stale-lock archive name")


def _acquire_mutation_guard(context_root: Path) -> _MutationGuard:
    staging_dir = resolve_context_path(
        context_root,
        "98_state/staging",
        allowed_prefixes=("98_state/staging",),
    )
    staging_dir.mkdir(exist_ok=True)
    if (
        staging_dir.is_symlink()
        or (hasattr(staging_dir, "is_junction") and staging_dir.is_junction())
        or not staging_dir.is_dir()
    ):
        raise ContextBoundaryError("98_state/staging must be a regular directory")
    nonce = _new_nonce()
    choosing_path = staging_dir / f"{_GUARD_CHOOSING_PREFIX}{nonce}.json"
    choosing = _MutationGuard(
        path=choosing_path,
        metadata=_GuardMetadata(
            pid=_local_pid(),
            hostname=_local_hostname(),
            acquired_at=_utc_now(),
            nonce=nonce,
            ticket=0,
        ),
    )
    ticket_guard: _MutationGuard | None = None
    try:
        _write_exclusive(choosing.path, _encode_guard_metadata(choosing.metadata))
        _fsync_directory(staging_dir)
        tickets = _read_guard_entries(
            staging_dir,
            prefix=_GUARD_TICKET_PREFIX,
            own_nonce=nonce,
        )
        ticket = max((metadata.ticket for _path, metadata in tickets), default=0) + 1
        ticket_guard = _MutationGuard(
            path=staging_dir / f"{_GUARD_TICKET_PREFIX}{nonce}.json",
            metadata=_GuardMetadata(
                pid=choosing.metadata.pid,
                hostname=choosing.metadata.hostname,
                acquired_at=choosing.metadata.acquired_at,
                nonce=nonce,
                ticket=ticket,
            ),
        )
        _write_exclusive(ticket_guard.path, _encode_guard_metadata(ticket_guard.metadata))
        _fsync_directory(staging_dir)
        _release_mutation_guard(choosing)
        delay = _GUARD_INITIAL_DELAY_SECONDS
        for _attempt in range(_GUARD_ATTEMPTS):
            choosing_entries = _read_guard_entries(
                staging_dir,
                prefix=_GUARD_CHOOSING_PREFIX,
                own_nonce=nonce,
            )
            ticket_entries = _read_guard_entries(
                staging_dir,
                prefix=_GUARD_TICKET_PREFIX,
                own_nonce=nonce,
            )
            if ticket_guard is None or not any(
                metadata.nonce == nonce for _path, metadata in ticket_entries
            ):
                raise LockError("The context lock mutation ticket disappeared")
            owner = min(
                ticket_entries,
                key=lambda entry: (entry[1].ticket, entry[1].nonce),
            )
            if not choosing_entries and owner[1].nonce == nonce:
                return ticket_guard
            _sleep(delay)
            delay = min(delay * 2, _GUARD_MAX_DELAY_SECONDS)
        raise LockHeldError("The context lock mutation guard is held by another process")
    except BaseException:
        for guard in (choosing, ticket_guard):
            if guard is not None:
                with suppress(LockError):
                    _release_mutation_guard(guard)
        raise


def _release_mutation_guard(guard: _MutationGuard) -> None:
    try:
        snapshot = _read_guard_snapshot_with_retry(guard.path)
    except LockHeldError:
        _publish_guard_cancellation(guard)
        return
    if snapshot is None:
        raise LockError("The context lock mutation guard disappeared")
    try:
        metadata = _decode_guard_metadata(snapshot.payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise LockError("The context lock mutation guard is malformed") from exc
    if not secrets.compare_digest(metadata.nonce, guard.metadata.nonce):
        raise LockError("The context lock mutation guard is owned by another process")
    try:
        _unlink_with_retry(guard.path)
    except FileNotFoundError:
        return
    except LockError:
        _publish_guard_cancellation(guard)
        return
    _fsync_directory(guard.path.parent)


def _publish_guard_cancellation(guard: _MutationGuard) -> None:
    cancellation_path = _guard_cancellation_path(guard.path, guard.metadata)
    payload = _encode_guard_metadata(guard.metadata)
    try:
        _write_exclusive(cancellation_path, payload)
    except FileExistsError:
        snapshot = _read_guard_snapshot_with_retry(cancellation_path)
        if snapshot is None or snapshot.payload != payload:
            raise LockError("The mutation guard cancellation record is invalid") from None
    _fsync_directory(cancellation_path.parent)


def _read_guard_entries(
    staging_dir: Path,
    *,
    prefix: str,
    own_nonce: str,
) -> tuple[tuple[Path, _GuardMetadata], ...]:
    entries: list[tuple[Path, _GuardMetadata]] = []
    for path in sorted(staging_dir.glob(f"{prefix}*.json"), key=lambda item: item.name):
        snapshot = _read_guard_snapshot_with_retry(path)
        if snapshot is None:
            continue
        try:
            metadata = _decode_guard_metadata(snapshot.payload)
            expected_name = f"{prefix}{metadata.nonce}.json"
            valid_ticket = (
                metadata.ticket == 0 if prefix == _GUARD_CHOOSING_PREFIX else metadata.ticket >= 1
            )
            if path.name != expected_name or not valid_ticket:
                raise ValueError("Mutation-guard filename and metadata disagree")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            modified_at = datetime.fromtimestamp(snapshot.mtime_ns / 1_000_000_000, tz=UTC)
            if _utc_now() - modified_at > _GUARD_MALFORMED_STALE_AFTER:
                if _unlink_if_unchanged(path, snapshot):
                    _fsync_directory(staging_dir)
                continue
            raise LockHeldError("A recent mutation guard entry is malformed") from exc

        if _guard_entry_is_cancelled(path, snapshot, metadata):
            continue

        if (
            metadata.nonce != own_nonce
            and metadata.hostname == _local_hostname()
            and not _pid_is_alive(metadata.pid)
        ):
            if _unlink_if_unchanged(path, snapshot):
                _fsync_directory(staging_dir)
            continue
        entries.append((path, metadata))
    return tuple(entries)


def _read_guard_snapshot_with_retry(path: Path) -> _LockSnapshot | None:
    delay = _GUARD_INITIAL_DELAY_SECONDS
    for attempt in range(_GUARD_ATTEMPTS):
        try:
            return _read_snapshot(path)
        except PermissionError as exc:
            if attempt + 1 >= _GUARD_ATTEMPTS:
                raise LockHeldError(
                    "A context lock mutation guard entry is temporarily unreadable"
                ) from exc
            _sleep(delay)
            delay = min(delay * 2, _GUARD_MAX_DELAY_SECONDS)
    raise AssertionError("Guard read retry loop must return or raise")  # pragma: no cover


def _guard_entry_is_cancelled(
    guard_path: Path,
    guard_snapshot: _LockSnapshot,
    metadata: _GuardMetadata,
) -> bool:
    path = _guard_cancellation_path(guard_path, metadata)
    snapshot = _read_guard_snapshot_with_retry(path)
    if snapshot is None:
        return False
    try:
        cancellation = _decode_guard_metadata(snapshot.payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        if _discard_stale_cancellation(path, snapshot):
            return False
        raise LockHeldError("A mutation guard cancellation record is malformed") from exc
    if cancellation != metadata:
        if _discard_stale_cancellation(path, snapshot):
            return False
        raise LockHeldError("A mutation guard cancellation record has mismatched ownership")
    if _unlink_guard_snapshot_with_retry(guard_path, guard_snapshot):
        _fsync_directory(guard_path.parent)
        if _unlink_guard_snapshot_with_retry(path, snapshot):
            _fsync_directory(path.parent)
    return True


def _guard_cancellation_path(guard_path: Path, metadata: _GuardMetadata) -> Path:
    if guard_path.name == f"{_GUARD_CHOOSING_PREFIX}{metadata.nonce}.json":
        kind = "choosing"
    elif guard_path.name == f"{_GUARD_TICKET_PREFIX}{metadata.nonce}.json":
        kind = "ticket"
    else:
        raise LockError("The mutation guard path does not match its ownership metadata")
    return guard_path.with_name(f"{_GUARD_CANCELLED_PREFIX}{kind}-{metadata.nonce}.json")


def _discard_stale_cancellation(path: Path, snapshot: _LockSnapshot) -> bool:
    modified_at = datetime.fromtimestamp(snapshot.mtime_ns / 1_000_000_000, tz=UTC)
    if _utc_now() - modified_at <= _GUARD_MALFORMED_STALE_AFTER:
        return False
    if not _unlink_if_unchanged(path, snapshot):
        return False
    _fsync_directory(path.parent)
    return True


def _unlink_guard_snapshot_with_retry(path: Path, expected: _LockSnapshot) -> bool:
    delay = _GUARD_INITIAL_DELAY_SECONDS
    for attempt in range(_GUARD_ATTEMPTS):
        current = _read_guard_snapshot_with_retry(path)
        if current is None:
            return True
        if current != expected:
            raise LockHeldError("A mutation guard entry changed during cancellation cleanup")
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt + 1 >= _GUARD_ATTEMPTS:
                return False
            _sleep(delay)
            delay = min(delay * 2, _GUARD_MAX_DELAY_SECONDS)
    raise AssertionError("Guard unlink retry loop must return")  # pragma: no cover


def _unlink_if_unchanged(path: Path, expected: _LockSnapshot) -> bool:
    current = _read_snapshot(path)
    if current is None or current != expected:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _read_snapshot(path: Path) -> _LockSnapshot | None:
    _reject_unsafe_file_leaf(path, allow_missing=True)
    try:
        with path.open("rb") as stream:
            payload = stream.read()
            file_stat = os.fstat(stream.fileno())
    except FileNotFoundError:
        return None
    return _LockSnapshot(
        payload=payload,
        mtime_ns=file_stat.st_mtime_ns,
        file_id=(file_stat.st_dev, file_stat.st_ino),
    )


def _write_exclusive(path: Path, payload: bytes) -> None:
    _reject_unsafe_file_leaf(path, allow_missing=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            path.unlink()
        raise


def _write_fsynced(path: Path, payload: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _encode_metadata(metadata: LockMetadata) -> bytes:
    return (json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _decode_metadata(payload: bytes) -> LockMetadata:
    loaded: Any = json.loads(payload.decode("utf-8"))
    return LockMetadata.from_dict(loaded)


def _encode_guard_metadata(metadata: _GuardMetadata) -> bytes:
    return (json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _decode_guard_metadata(payload: bytes) -> _GuardMetadata:
    loaded: Any = json.loads(payload.decode("utf-8"))
    return _GuardMetadata.from_dict(loaded)


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Lock {field_name} must be a non-empty string")
    return value


def _parse_utc(value: object, field_name: str) -> datetime:
    raw = _required_string(value, field_name)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Lock {field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Lock {field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reject_unsafe_file_leaf(path: Path, *, allow_missing: bool) -> None:
    # One lstat decides everything: guard election deletes transient entries
    # concurrently, so separate exists()/is_file() probes race with unlink.
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if not allow_missing:
            raise ContextBoundaryError(f"Runtime file is missing: {path.name}") from None
        return
    except OSError as exc:
        raise ContextBoundaryError(f"Unable to inspect runtime path {path.name}: {exc}") from exc
    if stat.S_ISLNK(mode):
        raise ContextBoundaryError(f"Runtime file must not be a symlink or junction: {path.name}")
    if not stat.S_ISREG(mode):
        raise ContextBoundaryError(f"Runtime path must be a regular file: {path.name}")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_nonce() -> str:
    return secrets.token_hex(16)


def _local_hostname() -> str:
    return socket.gethostname()


def _local_pid() -> int:
    return os.getpid()


def _pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    if sys.platform != "win32":  # pragma: no cover - windows-only helper
        raise RuntimeError("_windows_pid_is_alive is Windows-only")
    # Import lazily so non-Windows platforms never resolve Win32 symbols.
    import ctypes

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        # Access denied means the process exists; every other uncertainty also
        # fails closed as alive. A definitely absent PID returns invalid parameter.
        return ctypes.get_last_error() != 87
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == wait_object_0:
            return False
        if result == wait_timeout:
            return True
        return True
    finally:
        kernel32.CloseHandle(handle)


def _replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def _link_no_replace(source: Path, target: Path) -> None:
    os.link(source, target, follow_symlinks=False)


def _unlink_with_retry(path: Path) -> None:
    delay = _REPLACE_INITIAL_DELAY_SECONDS
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            path.unlink()
            return
        except PermissionError as exc:
            if attempt + 1 >= _REPLACE_ATTEMPTS:
                raise LockError(
                    f"Runtime file remained unavailable after {_REPLACE_ATTEMPTS} attempts"
                ) from exc
            _sleep(delay)
            delay *= 2
    raise AssertionError("Retry loop must return or raise")  # pragma: no cover


def _replace_heartbeat_with_retry(holder: ContextLock, source: Path, target: Path) -> None:
    delay = _REPLACE_INITIAL_DELAY_SECONDS
    for attempt in range(_REPLACE_ATTEMPTS):
        holder.verify_fence()
        try:
            _replace(source, target)
            return
        except PermissionError as exc:
            if attempt + 1 >= _REPLACE_ATTEMPTS:
                raise LockError(
                    f"Heartbeat replacement remained unavailable after {_REPLACE_ATTEMPTS} attempts"
                ) from exc
            _sleep(delay)
            delay *= 2
    raise AssertionError("Retry loop must return or raise")  # pragma: no cover


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


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
