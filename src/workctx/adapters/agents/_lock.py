"""Nonce-fenced writer lock shared by adapter mutation transactions."""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Self

from workctx import __version__

from ._safe_fs import FileSnapshot, SafeRoot, UnsafePathError
from .errors import InvalidAdapterStateError
from .layout import InstallationLayout

DEFAULT_STALE_AFTER = timedelta(minutes=10)
_CTYPES: Any = ctypes
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87
_STILL_ACTIVE = 259


class AdapterLockError(InvalidAdapterStateError):
    """Base error for adapter lock state."""


class AdapterLockHeldError(AdapterLockError):
    """Raised when a non-stale writer lock already exists."""


class AdapterLockFenceError(AdapterLockError):
    """Raised after an acquired lock loses its identity or nonce."""


@dataclass(frozen=True, slots=True)
class LockMetadata:
    """ADR-0006-compatible lock owner metadata."""

    pid: int
    hostname: str
    session_id: str
    tool_version: str
    acquired_at: datetime
    heartbeat_at: datetime
    nonce: str

    def to_bytes(self) -> bytes:
        payload = {
            "pid": self.pid,
            "hostname": self.hostname,
            "session_id": self.session_id,
            "tool_version": self.tool_version,
            "acquired_at": _format_utc(self.acquired_at),
            "heartbeat_at": _format_utc(self.heartbeat_at),
            "nonce": self.nonce,
        }
        return (json.dumps(payload, indent=2) + "\n").encode("utf-8")

    @classmethod
    def from_bytes(cls, content: bytes) -> Self:
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Lock metadata is not valid UTF-8 JSON") from error
        fields = {
            "pid",
            "hostname",
            "session_id",
            "tool_version",
            "acquired_at",
            "heartbeat_at",
            "nonce",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Lock metadata has an invalid object shape")
        pid = value["pid"]
        strings = {field: value[field] for field in fields - {"pid"}}
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Lock pid must be a positive integer")
        if any(not isinstance(item, str) or not item for item in strings.values()):
            raise ValueError("Lock string fields must be nonempty")
        nonce = strings["nonce"]
        if len(nonce) != 32 or any(character not in "0123456789abcdef" for character in nonce):
            raise ValueError("Lock nonce must be lowercase 128-bit hexadecimal")
        return cls(
            pid=pid,
            hostname=strings["hostname"],
            session_id=strings["session_id"],
            tool_version=strings["tool_version"],
            acquired_at=_parse_utc(strings["acquired_at"]),
            heartbeat_at=_parse_utc(strings["heartbeat_at"]),
            nonce=nonce,
        )


@dataclass(frozen=True, slots=True)
class LockInspection:
    """Read-only lock assessment used by status precedence."""

    exists: bool
    live: bool
    stale: bool
    invalid: bool
    metadata: LockMetadata | None = None
    detail: str | None = None
    snapshot: FileSnapshot | None = None


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("Lock timestamp must use the UTC Z designator")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("Lock timestamp must be UTC")
    return parsed


def _process_exists(pid: int) -> bool:
    """Check a local PID without signaling or mutating the process."""

    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True

    kernel32 = _CTYPES.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    open_process.restype = ctypes.c_void_p
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    get_exit_code.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return int(_CTYPES.get_last_error()) != _ERROR_INVALID_PARAMETER
    try:
        exit_code = ctypes.c_ulong()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return True
        return int(exit_code.value) == _STILL_ACTIVE
    finally:
        close_handle(handle)


def _snapshot_is_expired(
    snapshot: FileSnapshot,
    *,
    now: datetime,
    stale_after: timedelta,
) -> bool:
    if snapshot.modified_ns is None:
        return False
    now_ns = int(now.timestamp() * 1_000_000_000)
    age_ns = now_ns - snapshot.modified_ns
    return age_ns > int(stale_after.total_seconds() * 1_000_000_000)


def inspect_adapter_lock(
    layout: InstallationLayout,
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> LockInspection:
    """Safely inspect a lock without taking it over or reading anything global."""

    safe = SafeRoot(layout.root)
    try:
        snapshot = safe.inspect_file(layout.lock_path)
    except UnsafePathError as error:
        return LockInspection(False, False, False, True, detail=str(error))
    if not snapshot.exists:
        return LockInspection(False, False, False, False)
    if snapshot.content is None:
        return LockInspection(
            True,
            False,
            False,
            True,
            detail="Lock content was unavailable",
            snapshot=snapshot,
        )
    current = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        metadata = LockMetadata.from_bytes(snapshot.content)
    except ValueError as error:
        stale = _snapshot_is_expired(snapshot, now=current, stale_after=stale_after)
        return LockInspection(
            True,
            False,
            stale,
            not stale,
            detail=str(error),
            snapshot=snapshot,
        )
    owner_dead = (
        metadata.hostname.casefold() == socket.gethostname().casefold()
        and not _process_exists(metadata.pid)
    )
    stale = owner_dead or current - metadata.heartbeat_at > stale_after
    return LockInspection(
        True,
        not stale,
        stale,
        False,
        metadata=metadata,
        snapshot=snapshot,
    )


class AdapterLock:
    """An acquired, fenced lease for one installation root."""

    def __init__(
        self,
        safe: SafeRoot,
        path: str,
        metadata: LockMetadata,
        snapshot: FileSnapshot,
    ) -> None:
        self._safe = safe
        self._path = path
        self._metadata = metadata
        self._snapshot = snapshot
        self._released = False

    @property
    def nonce(self) -> str:
        return self._metadata.nonce

    @classmethod
    def acquire(
        cls,
        layout: InstallationLayout,
        *,
        session_id: str,
        now: datetime | None = None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> Self:
        if not session_id:
            raise ValueError("session_id must not be empty")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        safe = SafeRoot(layout.root)
        parent = PurePosixPath(layout.lock_path).parent.as_posix()
        safe.ensure_directories(parent)
        for _attempt in range(8):
            metadata = LockMetadata(
                pid=os.getpid(),
                hostname=socket.gethostname(),
                session_id=session_id,
                tool_version=__version__,
                acquired_at=current,
                heartbeat_at=current,
                nonce=secrets.token_hex(16),
            )
            try:
                snapshot = safe.write_exclusive(layout.lock_path, metadata.to_bytes())
            except FileExistsError:
                inspection = inspect_adapter_lock(
                    layout,
                    now=current,
                    stale_after=stale_after,
                )
                if inspection.invalid:
                    raise AdapterLockError(inspection.detail or "Invalid adapter lock") from None
                if inspection.live:
                    raise AdapterLockHeldError(
                        "Another writer holds the project adapter lock"
                    ) from None
                if not inspection.stale or inspection.snapshot is None:
                    raise AdapterLockError("Adapter lock could not be assessed safely") from None
                stamp = _format_utc(current).replace("-", "").replace(":", "")
                evidence_id = (
                    inspection.metadata.nonce
                    if inspection.metadata is not None
                    else (inspection.snapshot.content_hash or "sha256:malformed").removeprefix(
                        "sha256:"
                    )[:32]
                )
                archive_base = layout.lock_path.removesuffix(".json")
                archive = f"{archive_base}.stale-{stamp}-{evidence_id}.json"
                try:
                    safe.move(
                        layout.lock_path,
                        archive,
                        expected_source=inspection.snapshot,
                    )
                except (FileExistsError, FileNotFoundError):
                    continue
                continue
            return cls(safe, layout.lock_path, metadata, snapshot)
        raise AdapterLockHeldError("Adapter lock acquisition did not converge")

    def verify(self) -> None:
        if self._released:
            raise AdapterLockFenceError("Adapter lock was already released")
        current = self._safe.inspect_file(self._path)
        if (
            not current.exists
            or current.identity != self._snapshot.identity
            or current.content_hash != self._snapshot.content_hash
            or current.content is None
        ):
            raise AdapterLockFenceError("Adapter lock identity or bytes changed")
        try:
            metadata = LockMetadata.from_bytes(current.content)
        except ValueError as error:
            raise AdapterLockFenceError("Adapter lock metadata became invalid") from error
        if metadata.nonce != self.nonce:
            raise AdapterLockFenceError("Adapter lock nonce changed")

    def heartbeat(self, *, now: datetime | None = None) -> None:
        """Refresh the heartbeat by atomically replacing the same nonce record."""

        self.verify()
        current = (now or datetime.now(UTC)).astimezone(UTC)
        updated = LockMetadata(
            pid=self._metadata.pid,
            hostname=self._metadata.hostname,
            session_id=self._metadata.session_id,
            tool_version=self._metadata.tool_version,
            acquired_at=self._metadata.acquired_at,
            heartbeat_at=current,
            nonce=self._metadata.nonce,
        )
        staged_path = f"{self._path}.heartbeat-{self.nonce}"
        staged_snapshot: FileSnapshot | None = None
        try:
            staged_snapshot = self._safe.write_exclusive(staged_path, updated.to_bytes())
            snapshot = self._safe.replace(
                staged_path,
                self._path,
                expected_source=staged_snapshot,
                expected_target=self._snapshot,
            )
        except BaseException:
            staged = self._safe.inspect_file(staged_path)
            if staged_snapshot is not None and staged == staged_snapshot:
                self._safe.unlink(staged_path, expected=staged_snapshot)
            raise
        self._metadata = updated
        self._snapshot = snapshot

    def release(self) -> None:
        if self._released:
            return
        self.verify()
        if not self._safe.unlink(self._path, expected=self._snapshot):
            raise AdapterLockFenceError("Adapter lock disappeared during release")
        self._released = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.release()
