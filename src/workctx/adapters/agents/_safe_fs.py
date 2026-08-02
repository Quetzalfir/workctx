"""No-follow filesystem primitives for project-local agent adapter transactions.

Every relative path handled here is untrusted manifest data.  Existing file bytes are
read only from a descriptor or Windows handle after the complete ancestor walk, leaf
type, containment, and identity have been verified.  Mutations repeat those checks and
never use recursive deletion.
"""

from __future__ import annotations

import ctypes
import hashlib
import ntpath
import os
import re
import stat
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CTYPES: Any = ctypes
_HASH_PREFIX = "sha256:"
_READ_CHUNK_SIZE = 64 * 1024
_WINDOWS_REPLACE_ATTEMPTS = 10
_WINDOWS_REPLACE_INITIAL_DELAY = 0.01

_CREDENTIAL_PATH_MARKER = re.compile(
    r"(?:^|[._-])(?:auth(?:entication)?|credentials?|oauth|tokens?|secrets?|"
    r"passw(?:or)?d|api[._-]?keys?|access[._-]?keys?|private[._-]?keys?)"
    r"(?:$|[._-])",
    re.IGNORECASE,
)
_CREDENTIAL_PATH_SHAPE = re.compile(
    r"^(?:\.env(?:\..*)?|\.netrc|\.npmrc|\.pypirc|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|"
    r".*\.(?:key|pem|p12|pfx|jks|keystore))$",
    re.IGNORECASE,
)
_CREDENTIAL_DIRECTORY_SHAPE = re.compile(
    r"^\.(?:codex|claude|gemini|ssh|aws|azure|kube|docker|gnupg)$",
    re.IGNORECASE,
)


def is_credential_capable_path(path: str) -> bool:
    """Return whether any path segment can conventionally carry credential material."""

    return any(
        _CREDENTIAL_PATH_MARKER.search(part)
        or _CREDENTIAL_PATH_SHAPE.fullmatch(part)
        or _CREDENTIAL_DIRECTORY_SHAPE.fullmatch(part)
        for part in path.replace("\\", "/").split("/")
    )


_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "CONIN$",
        "CONOUT$",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        "COM\N{SUPERSCRIPT ONE}",
        "COM\N{SUPERSCRIPT TWO}",
        "COM\N{SUPERSCRIPT THREE}",
        "LPT\N{SUPERSCRIPT ONE}",
        "LPT\N{SUPERSCRIPT TWO}",
        "LPT\N{SUPERSCRIPT THREE}",
    }
)

_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_CREATE_NEW = 1
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_ERROR_HANDLE_EOF = 38


class SafeFilesystemError(Exception):
    """Base class for invalid or concurrently changed adapter filesystem state."""


class UnsafePathError(SafeFilesystemError, ValueError):
    """Raised when a relative path or on-disk path component is unsafe."""


class UnsafeFilesystemError(UnsafePathError):
    """Raised when physical filesystem state violates the no-follow boundary."""


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Stable identity from an opened object (device/volume plus inode/file index)."""

    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """File state read from a verified descriptor or handle."""

    exists: bool
    identity: FileIdentity | None
    size: int | None
    content_hash: str | None
    content: bytes | None
    modified_ns: int | None = None

    def matches(
        self,
        identity: FileIdentity | None,
        content_hash: str | None,
    ) -> bool:
        """Return whether this is the exact expected absent or present preimage."""

        return snapshot_matches(self, identity, content_hash)


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """No-follow metadata for one safe regular file or directory."""

    name: str
    path: str
    identity: FileIdentity
    is_directory: bool
    size: int

    @property
    def is_file(self) -> bool:
        return not self.is_directory


@dataclass(frozen=True, slots=True)
class _DirectoryProof:
    identities: tuple[FileIdentity, ...]

    @property
    def identity(self) -> FileIdentity:
        return self.identities[-1]


class _MissingPath(Exception):
    def __init__(self, component_index: int, identities: tuple[FileIdentity, ...]) -> None:
        super().__init__(component_index)
        self.component_index = component_index
        self.identities = identities


@dataclass(frozen=True, slots=True)
class _WindowsHandleInfo:
    identity: FileIdentity
    attributes: int
    size: int
    creation_time: int
    write_time: int

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & _FILE_ATTRIBUTE_DIRECTORY)

    @property
    def is_reparse_point(self) -> bool:
        return bool(self.attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_ulong),
        ("ftCreationTimeLow", ctypes.c_ulong),
        ("ftCreationTimeHigh", ctypes.c_ulong),
        ("ftLastAccessTimeLow", ctypes.c_ulong),
        ("ftLastAccessTimeHigh", ctypes.c_ulong),
        ("ftLastWriteTimeLow", ctypes.c_ulong),
        ("ftLastWriteTimeHigh", ctypes.c_ulong),
        ("dwVolumeSerialNumber", ctypes.c_ulong),
        ("nFileSizeHigh", ctypes.c_ulong),
        ("nFileSizeLow", ctypes.c_ulong),
        ("nNumberOfLinks", ctypes.c_ulong),
        ("nFileIndexHigh", ctypes.c_ulong),
        ("nFileIndexLow", ctypes.c_ulong),
    ]


def validate_relative_path(path: str) -> str:
    """Validate one portable, forward-slash, non-root relative file path."""

    if not isinstance(path, str):
        raise UnsafePathError("Adapter paths must be strings")
    if not path:
        raise UnsafePathError("Adapter paths must not be empty")
    if "\\" in path:
        raise UnsafePathError("Adapter paths must use forward slashes")
    if path.startswith("/") or ntpath.isabs(path) or ntpath.splitdrive(path)[0]:
        raise UnsafePathError("Adapter paths must be relative")
    if "\0" in path:
        raise UnsafePathError("Adapter paths must not contain NUL")

    for segment in path.split("/"):
        if segment in {"", ".", ".."}:
            raise UnsafePathError("Adapter paths contain an empty or traversal segment")
        if segment.endswith((".", " ")):
            raise UnsafePathError("Adapter path segments must not end in a dot or space")
        if any(ord(character) < 32 for character in segment):
            raise UnsafePathError("Adapter path segments must not contain control characters")
        if any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in segment):
            raise UnsafePathError("Adapter path segments contain a Windows-forbidden character")
        device_stem = segment.split(".", maxsplit=1)[0].rstrip(" .").upper()
        if device_stem in _WINDOWS_RESERVED_NAMES:
            raise UnsafePathError("Adapter path segments must not use Windows device names")
    return path


def collision_key(path: str) -> str:
    """Return the platform-independent NFC plus full-casefold path collision key."""

    return unicodedata.normalize("NFC", validate_relative_path(path)).casefold()


def snapshot_matches(
    snapshot: FileSnapshot,
    expected_identity: FileIdentity | None,
    expected_content_hash: str | None,
) -> bool:
    """Compare a snapshot to one exact present preimage or the absent state."""

    if expected_identity is None or expected_content_hash is None:
        return (
            expected_identity is None
            and expected_content_hash is None
            and not snapshot.exists
            and snapshot.identity is None
            and snapshot.content_hash is None
        )
    return (
        snapshot.exists
        and snapshot.identity == expected_identity
        and snapshot.content_hash == expected_content_hash
    )


class SafeRoot:
    """A physical installation root with descriptor/handle-relative safe operations."""

    def __init__(self, root: Path) -> None:
        try:
            physical = Path(root).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise UnsafeFilesystemError("Safe root is unavailable") from error

        if os.name == "nt":
            handle = _windows_open_handle(physical, read=False)
            try:
                info = _windows_handle_info(handle)
                if info.is_reparse_point or not info.is_directory:
                    raise UnsafeFilesystemError("Safe root must be a physical directory")
                final_path = Path(_windows_final_path(handle))
                _windows_compare_lstat(physical, info, expect_directory=True)
            finally:
                _windows_close_handle(handle)
            self.path = final_path
            self._root_final = _windows_normal_path(final_path)
            self._root_identity = info.identity
        else:
            try:
                descriptor = _posix_open_absolute_directory(physical)
            except OSError as error:
                raise UnsafeFilesystemError("Safe root must be a physical directory") from error
            try:
                opened = os.fstat(descriptor)
                linked = os.lstat(physical)
                _require_posix_directory(opened, "Safe root")
                _require_posix_directory(linked, "Safe root")
                if not os.path.samestat(opened, linked):
                    raise UnsafeFilesystemError("Safe root identity changed during validation")
                self._root_identity = _identity_from_stat(opened)
            finally:
                os.close(descriptor)
            self.path = physical
            self._root_final = ""

    def inspect_file(self, relative_path: str) -> FileSnapshot:
        """Read one regular file from a verified descriptor/handle, or report absence."""

        parts = _file_parts(relative_path)
        if os.name == "nt":
            return self._inspect_file_windows(parts)
        return self._inspect_file_posix(parts)

    def inspect_entry(self, relative_path: str) -> DirectoryEntry | None:
        """Inspect one file or directory without following it or reading file bytes."""

        parts = _file_parts(relative_path)
        if os.name == "nt":
            return self._inspect_entry_windows(parts)
        return self._inspect_entry_posix(parts)

    def require_directory(self, relative_path: str = ".") -> FileIdentity:
        """Require a safe directory and return its verified identity."""

        parts = _directory_parts(relative_path)
        proof = self._verified_directory(parts, allow_missing=False)
        if proof is None:  # pragma: no cover - allow_missing=False is definitive
            raise FileNotFoundError(relative_path)
        return proof.identity

    def list_directory(self, relative_path: str = ".") -> tuple[DirectoryEntry, ...]:
        """List safe regular-file/directory metadata without following any entry."""

        parts = _directory_parts(relative_path)
        if os.name == "nt":
            return self._list_directory_windows(parts)
        return self._list_directory_posix(parts)

    def list_directory_names(self, relative_path: str = ".") -> tuple[str, ...]:
        """List validated child names without opening or following any child entry."""

        parts = _directory_parts(relative_path)
        if os.name == "nt":
            directory = self.path.joinpath(*parts)
            with self._hold_windows_directory_chain(parts):
                names = os.listdir(directory)
                repeated = os.listdir(directory)
        else:
            descriptor = self._open_verified_posix_directory(parts)
            try:
                names = os.listdir(descriptor)
                repeated = os.listdir(descriptor)
            finally:
                os.close(descriptor)
        for name in (*names, *repeated):
            _validate_single_component(name)
        first = {unicodedata.normalize("NFC", name).casefold(): name for name in names}
        second = {unicodedata.normalize("NFC", name).casefold(): name for name in repeated}
        if len(first) != len(names) or len(second) != len(repeated):
            raise UnsafeFilesystemError("Directory contains colliding entry names")
        if first != second:
            raise UnsafeFilesystemError("Directory entries changed while listed")
        return tuple(
            sorted(
                names,
                key=lambda name: (unicodedata.normalize("NFC", name).casefold(), name),
            )
        )

    def ensure_directories(self, relative_path: str) -> FileIdentity:
        """Create missing directory components under revalidated safe parents."""

        parts = _directory_parts(relative_path)
        if not parts:
            return self.require_directory(".")
        if os.name == "nt":
            self._ensure_directories_windows(parts)
        else:
            self._ensure_directories_posix(parts)
        return self.require_directory(relative_path)

    def write_exclusive(
        self,
        relative_path: str,
        content: bytes,
        *,
        mode: int = 0o600,
    ) -> FileSnapshot:
        """Create and fsync one regular file with O_CREAT | O_EXCL semantics."""

        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        parts = _file_parts(relative_path)
        if os.name == "nt":
            self._write_exclusive_windows(parts, content, mode)
        else:
            self._write_exclusive_posix(parts, content, mode)
        self.fsync_directory(_relative_parent(parts))
        snapshot = self.inspect_file(relative_path)
        expected_hash = _content_hash(content)
        if not snapshot.exists or snapshot.content_hash != expected_hash:
            raise UnsafeFilesystemError("Exclusive write postcondition did not hold")
        return snapshot

    def reserve_empty(self, relative_path: str, *, mode: int = 0o600) -> FileSnapshot:
        """Exclusively reserve an absent destination as a flushed empty regular file."""

        return self.write_exclusive(relative_path, b"", mode=mode)

    def replace(
        self,
        source_relative_path: str,
        target_relative_path: str,
        *,
        expected_source: FileSnapshot | None = None,
        expected_target: FileSnapshot | None = None,
    ) -> FileSnapshot:
        """Atomically replace a target, optionally bound to exact caller snapshots."""

        source_parts = _file_parts(source_relative_path)
        target_parts = _file_parts(target_relative_path)
        _require_distinct_paths(source_relative_path, target_relative_path)
        source = self.inspect_file(source_relative_path)
        if not source.exists:
            raise FileNotFoundError(source_relative_path)
        target = self.inspect_file(target_relative_path)
        if expected_source is not None and not _same_snapshot(source, expected_source):
            raise UnsafeFilesystemError("Replacement source precondition changed")
        if expected_target is not None and not _same_snapshot(target, expected_target):
            raise UnsafeFilesystemError("Replacement target precondition changed")
        return self._replace_bound(
            source_parts,
            target_parts,
            source_relative_path,
            target_relative_path,
            expected_source or source,
            expected_target or target,
        )

    def move(
        self,
        source_relative_path: str,
        target_relative_path: str,
        *,
        expected_source: FileSnapshot | None = None,
    ) -> FileSnapshot:
        """Move a safe file to an absent target via an exclusive empty reservation."""

        _require_distinct_paths(source_relative_path, target_relative_path)
        source = self.inspect_file(source_relative_path)
        if not source.exists:
            raise FileNotFoundError(source_relative_path)
        if expected_source is not None and not _same_snapshot(source, expected_source):
            raise UnsafeFilesystemError("Move source precondition changed")
        if self.inspect_file(target_relative_path).exists:
            raise FileExistsError(target_relative_path)
        reservation = self.reserve_empty(target_relative_path)
        try:
            return self._replace_bound(
                _file_parts(source_relative_path),
                _file_parts(target_relative_path),
                source_relative_path,
                target_relative_path,
                source,
                reservation,
            )
        except BaseException:
            current = self.inspect_file(target_relative_path)
            if current.matches(reservation.identity, reservation.content_hash):
                self.unlink(target_relative_path, expected=reservation)
            raise

    def unlink(
        self,
        relative_path: str,
        *,
        expected: FileSnapshot | None = None,
    ) -> bool:
        """Remove one regular file, optionally bound to an exact caller snapshot."""

        parts = _file_parts(relative_path)
        observed = self.inspect_file(relative_path)
        if expected is not None and not _same_snapshot(observed, expected):
            raise UnsafeFilesystemError("Unlink precondition changed")
        bound_expected = expected or observed
        if not bound_expected.exists:
            return False
        parent = self._verified_directory(parts[:-1], allow_missing=False)
        if parent is None:  # pragma: no cover - allow_missing=False is definitive
            raise FileNotFoundError(relative_path)
        current = self.inspect_file(relative_path)
        if not _same_snapshot(current, bound_expected):
            raise UnsafeFilesystemError("File changed immediately before unlink")
        if os.name == "nt":
            with self._hold_windows_directory_chain(parts[:-1]):
                bound = self.inspect_file(relative_path)
                if not _same_snapshot(bound, bound_expected):
                    raise UnsafeFilesystemError("File changed during bound unlink")
                os.unlink(self.path.joinpath(*parts))
        else:
            parent_descriptor = self._open_verified_posix_directory(parts[:-1])
            try:
                os.unlink(parts[-1], dir_fd=parent_descriptor)
            finally:
                os.close(parent_descriptor)
        self.fsync_directory(_relative_parent(parts))
        return True

    def remove_empty_directory(self, relative_path: str) -> bool:
        """Remove one verified empty directory without recursive deletion."""

        parts = _file_parts(relative_path)
        try:
            expected = self.require_directory(relative_path)
        except FileNotFoundError:
            return False
        parent = self._verified_directory(parts[:-1], allow_missing=False)
        if parent is None:  # pragma: no cover - allow_missing=False is definitive
            raise FileNotFoundError(relative_path)
        if self.require_directory(relative_path) != expected:
            raise UnsafeFilesystemError("Directory changed immediately before removal")
        if os.name == "nt":
            with self._hold_windows_directory_chain(parts[:-1]):
                if self.require_directory(relative_path) != expected:
                    raise UnsafeFilesystemError("Directory changed during bound removal")
                os.rmdir(self.path.joinpath(*parts))
        else:
            parent_descriptor = self._open_verified_posix_directory(parts[:-1])
            try:
                os.rmdir(parts[-1], dir_fd=parent_descriptor)
            finally:
                os.close(parent_descriptor)
        self.fsync_directory(_relative_parent(parts))
        return True

    def fsync_directory(self, relative_path: str = ".") -> None:
        """Best-effort directory fsync on POSIX; verified no-op on Windows."""

        parts = _directory_parts(relative_path)
        if os.name == "nt":
            self.require_directory(relative_path)
            return
        descriptor = self._open_verified_posix_directory(parts)
        try:
            with suppress(OSError):
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _verified_directory(
        self,
        parts: tuple[str, ...],
        *,
        allow_missing: bool,
    ) -> _DirectoryProof | None:
        if os.name == "nt":
            return self._verified_directory_windows(parts, allow_missing=allow_missing)
        return self._verified_directory_posix(parts, allow_missing=allow_missing)

    def _verified_directory_posix(
        self,
        parts: tuple[str, ...],
        *,
        allow_missing: bool,
    ) -> _DirectoryProof | None:
        descriptor = self._try_open_verified_posix_directory(parts)
        if descriptor is None:
            if allow_missing:
                return None
            raise FileNotFoundError("/".join(parts))
        try:
            return _DirectoryProof((_identity_from_stat(os.fstat(descriptor)),))
        finally:
            os.close(descriptor)

    def _open_verified_posix_directory(self, parts: tuple[str, ...]) -> int:
        descriptor = self._try_open_verified_posix_directory(parts)
        if descriptor is None:
            raise FileNotFoundError("/".join(parts))
        return descriptor

    def _open_posix_directory_once(
        self,
        parts: tuple[str, ...],
    ) -> tuple[int, _DirectoryProof]:
        descriptor = self._open_posix_root()
        identities = [self._root_identity]
        try:
            for index, component in enumerate(parts):
                try:
                    linked = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError as error:
                    raise _MissingPath(index, tuple(identities)) from error
                _require_posix_directory(linked, component)
                child = os.open(component, _posix_directory_flags(), dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    linked_after = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                    _require_posix_directory(opened, component)
                    _require_posix_directory(linked_after, component)
                    if not (
                        os.path.samestat(linked, opened) and os.path.samestat(opened, linked_after)
                    ):
                        raise UnsafeFilesystemError(
                            f"Directory identity changed during validation: {component}"
                        )
                except BaseException:
                    os.close(child)
                    raise
                os.close(descriptor)
                descriptor = child
                identities.append(_identity_from_stat(opened))
            return descriptor, _DirectoryProof(tuple(identities))
        except BaseException:
            os.close(descriptor)
            raise

    def _open_posix_root(self) -> int:
        descriptor = _posix_open_absolute_directory(self.path)
        try:
            opened = os.fstat(descriptor)
            linked = os.lstat(self.path)
            _require_posix_directory(opened, "Safe root")
            _require_posix_directory(linked, "Safe root")
            if _identity_from_stat(opened) != self._root_identity or not os.path.samestat(
                opened, linked
            ):
                raise UnsafeFilesystemError("Safe root identity changed")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _inspect_file_posix(self, parts: tuple[str, ...]) -> FileSnapshot:
        parent_descriptor = self._try_open_verified_posix_directory(parts[:-1])
        if parent_descriptor is None:
            return _missing_snapshot()
        leaf_descriptor: int | None = None
        try:
            try:
                linked = os.stat(parts[-1], dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return self._confirm_missing_file_posix(
                    parts,
                    _identity_from_stat(os.fstat(parent_descriptor)),
                )
            _require_posix_regular(linked, parts[-1])
            leaf_descriptor = os.open(parts[-1], _posix_file_read_flags(), dir_fd=parent_descriptor)
            opened = os.fstat(leaf_descriptor)
            _require_posix_regular(opened, parts[-1])
            if not os.path.samestat(linked, opened):
                raise UnsafeFilesystemError("File identity changed while it was opened")

            repeated_parent = self._open_verified_posix_directory(parts[:-1])
            try:
                linked_after = os.stat(
                    parts[-1],
                    dir_fd=repeated_parent,
                    follow_symlinks=False,
                )
                _require_posix_regular(linked_after, parts[-1])
                if not os.path.samestat(opened, linked_after):
                    raise UnsafeFilesystemError("File identity changed during ancestor recheck")
            finally:
                os.close(repeated_parent)

            content = _read_descriptor(leaf_descriptor)
            final = os.fstat(leaf_descriptor)
            _require_posix_regular(final, parts[-1])
            if not _stable_posix_file(opened, final, len(content)):
                raise UnsafeFilesystemError("File changed while its bytes were read")
            return _snapshot(
                _identity_from_stat(final),
                content,
                modified_ns=final.st_mtime_ns,
            )
        finally:
            if leaf_descriptor is not None:
                os.close(leaf_descriptor)
            os.close(parent_descriptor)

    def _inspect_entry_posix(self, parts: tuple[str, ...]) -> DirectoryEntry | None:
        parent_descriptor = self._try_open_verified_posix_directory(parts[:-1])
        if parent_descriptor is None:
            return None
        leaf_descriptor: int | None = None
        expected_parent = _identity_from_stat(os.fstat(parent_descriptor))
        try:
            try:
                linked = os.stat(parts[-1], dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                self._confirm_missing_file_posix(parts, expected_parent)
                return None
            is_directory = _posix_entry_is_directory(linked, parts[-1])
            leaf_descriptor = os.open(
                parts[-1],
                _posix_entry_metadata_flags(is_directory=is_directory),
                dir_fd=parent_descriptor,
            )
            opened = os.fstat(leaf_descriptor)
            if _posix_entry_is_directory(opened, parts[-1]) is not is_directory:
                raise UnsafeFilesystemError("Entry type changed while it was opened")
            if not os.path.samestat(linked, opened):
                raise UnsafeFilesystemError("Entry identity changed while it was opened")

            repeated_parent = self._open_verified_posix_directory(parts[:-1])
            try:
                if _identity_from_stat(os.fstat(repeated_parent)) != expected_parent:
                    raise UnsafeFilesystemError("Entry ancestry changed during validation")
                try:
                    linked_after = os.stat(
                        parts[-1],
                        dir_fd=repeated_parent,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as error:
                    raise UnsafeFilesystemError(
                        "Entry disappeared during ancestor recheck"
                    ) from error
                if _posix_entry_is_directory(linked_after, parts[-1]) is not is_directory:
                    raise UnsafeFilesystemError("Entry type changed during ancestor recheck")
                if not os.path.samestat(opened, linked_after):
                    raise UnsafeFilesystemError("Entry identity changed during ancestor recheck")
            finally:
                os.close(repeated_parent)

            final = os.fstat(leaf_descriptor)
            if _posix_entry_is_directory(final, parts[-1]) is not is_directory:
                raise UnsafeFilesystemError("Entry type changed during metadata inspection")
            if not os.path.samestat(opened, final):
                raise UnsafeFilesystemError("Entry identity changed during metadata inspection")
            relative = "/".join(parts)
            return DirectoryEntry(
                name=parts[-1],
                path=relative,
                identity=_identity_from_stat(final),
                is_directory=is_directory,
                size=int(final.st_size),
            )
        finally:
            if leaf_descriptor is not None:
                os.close(leaf_descriptor)
            os.close(parent_descriptor)

    def _confirm_missing_file_posix(
        self,
        parts: tuple[str, ...],
        expected_parent: FileIdentity,
    ) -> FileSnapshot:
        repeated_parent = self._try_open_verified_posix_directory(parts[:-1])
        if repeated_parent is None:
            raise UnsafeFilesystemError("File ancestry disappeared while absence was checked")
        try:
            if _identity_from_stat(os.fstat(repeated_parent)) != expected_parent:
                raise UnsafeFilesystemError("File ancestry changed while absence was checked")
            try:
                os.stat(parts[-1], dir_fd=repeated_parent, follow_symlinks=False)
            except FileNotFoundError:
                return _missing_snapshot()
            raise UnsafeFilesystemError("File appeared while absence was checked")
        finally:
            os.close(repeated_parent)

    def _try_open_verified_posix_directory(self, parts: tuple[str, ...]) -> int | None:
        try:
            first_descriptor, first = self._open_posix_directory_once(parts)
        except _MissingPath as first_missing:
            try:
                second_descriptor, _second = self._open_posix_directory_once(parts)
            except _MissingPath as second_missing:
                if (
                    first_missing.component_index == second_missing.component_index
                    and first_missing.identities == second_missing.identities
                ):
                    return None
                raise UnsafeFilesystemError(
                    "Directory ancestry changed while absence was checked"
                ) from second_missing
            os.close(second_descriptor)
            raise UnsafeFilesystemError("Directory appeared while absence was checked") from None
        try:
            try:
                second_descriptor, second = self._open_posix_directory_once(parts)
            except _MissingPath as error:
                raise UnsafeFilesystemError("Directory disappeared during validation") from error
        finally:
            os.close(first_descriptor)
        if first.identities != second.identities:
            os.close(second_descriptor)
            raise UnsafeFilesystemError("Directory ancestry changed during validation")
        return second_descriptor

    def _list_directory_posix(self, parts: tuple[str, ...]) -> tuple[DirectoryEntry, ...]:
        descriptor = self._open_verified_posix_directory(parts)
        try:
            names = os.listdir(descriptor)
            entries: list[DirectoryEntry] = []
            seen: set[str] = set()
            for name in names:
                _validate_single_component(name)
                key = unicodedata.normalize("NFC", name).casefold()
                if key in seen:
                    raise UnsafeFilesystemError("Directory contains colliding entry names")
                seen.add(key)
                linked = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(linked.st_mode):
                    flags = _posix_directory_flags()
                    is_directory = True
                elif stat.S_ISREG(linked.st_mode):
                    flags = _posix_file_metadata_flags()
                    is_directory = False
                else:
                    raise UnsafeFilesystemError(f"Directory entry is not regular: {name}")
                child = os.open(name, flags, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    linked_after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if not (
                        os.path.samestat(linked, opened) and os.path.samestat(opened, linked_after)
                    ):
                        raise UnsafeFilesystemError(f"Directory entry identity changed: {name}")
                    if is_directory:
                        _require_posix_directory(opened, name)
                    else:
                        _require_posix_regular(opened, name)
                finally:
                    os.close(child)
                relative = "/".join((*parts, name))
                entries.append(
                    DirectoryEntry(
                        name=name,
                        path=relative,
                        identity=_identity_from_stat(opened),
                        is_directory=is_directory,
                        size=opened.st_size,
                    )
                )
            return tuple(sorted(entries, key=lambda entry: (collision_key(entry.path), entry.path)))
        finally:
            os.close(descriptor)

    def _ensure_directories_posix(self, parts: tuple[str, ...]) -> None:
        descriptor = self._open_verified_posix_directory(())
        try:
            for component in parts:
                try:
                    linked = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    with suppress(FileExistsError):
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                        with suppress(OSError):
                            os.fsync(descriptor)
                    linked = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                _require_posix_directory(linked, component)
                child = os.open(component, _posix_directory_flags(), dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    linked_after = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                    _require_posix_directory(opened, component)
                    if not (
                        os.path.samestat(linked, opened) and os.path.samestat(opened, linked_after)
                    ):
                        raise UnsafeFilesystemError(
                            f"Directory identity changed during creation: {component}"
                        )
                except BaseException:
                    os.close(child)
                    raise
                os.close(descriptor)
                descriptor = child
        finally:
            os.close(descriptor)

    def _write_exclusive_posix(
        self,
        parts: tuple[str, ...],
        content: bytes,
        mode: int,
    ) -> None:
        parent = self._open_verified_posix_directory(parts[:-1])
        descriptor: int | None = None
        try:
            _require_absent_or_safe_regular_at(parent, parts[-1])
            descriptor = os.open(
                parts[-1],
                _posix_file_create_flags(),
                mode,
                dir_fd=parent,
            )
            opened = os.fstat(descriptor)
            linked = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            _require_posix_regular(opened, parts[-1])
            _require_posix_regular(linked, parts[-1])
            if not os.path.samestat(opened, linked):
                raise UnsafeFilesystemError("Exclusive file identity changed after creation")
            _write_descriptor(descriptor, content)
            os.fsync(descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def _replace_bound(
        self,
        source_parts: tuple[str, ...],
        target_parts: tuple[str, ...],
        source_relative_path: str,
        target_relative_path: str,
        expected_source: FileSnapshot,
        expected_target: FileSnapshot,
    ) -> FileSnapshot:
        attempts = _WINDOWS_REPLACE_ATTEMPTS if os.name == "nt" else 1
        delay = _WINDOWS_REPLACE_INITIAL_DELAY
        for attempt in range(attempts):
            current_source = self.inspect_file(source_relative_path)
            current_target = self.inspect_file(target_relative_path)
            if not _same_snapshot(current_source, expected_source):
                raise UnsafeFilesystemError("Replacement source precondition changed")
            if not _same_snapshot(current_target, expected_target):
                raise UnsafeFilesystemError("Replacement target precondition changed")
            self._verified_directory(source_parts[:-1], allow_missing=False)
            self._verified_directory(target_parts[:-1], allow_missing=False)
            try:
                if os.name == "nt":
                    with (
                        self._hold_windows_directory_chain(source_parts[:-1]),
                        self._hold_windows_directory_chain(target_parts[:-1]),
                    ):
                        bound_source = self.inspect_file(source_relative_path)
                        bound_target = self.inspect_file(target_relative_path)
                        if not _same_snapshot(bound_source, expected_source):
                            raise UnsafeFilesystemError(
                                "Replacement source changed during bound operation"
                            )
                        if not _same_snapshot(bound_target, expected_target):
                            raise UnsafeFilesystemError(
                                "Replacement target changed during bound operation"
                            )
                        os.replace(
                            self.path.joinpath(*source_parts),
                            self.path.joinpath(*target_parts),
                        )
                else:
                    source_parent = self._open_verified_posix_directory(source_parts[:-1])
                    try:
                        target_parent = self._open_verified_posix_directory(target_parts[:-1])
                        try:
                            os.replace(
                                source_parts[-1],
                                target_parts[-1],
                                src_dir_fd=source_parent,
                                dst_dir_fd=target_parent,
                            )
                        finally:
                            os.close(target_parent)
                    finally:
                        os.close(source_parent)
                break
            except PermissionError:
                if os.name != "nt" or attempt + 1 == attempts:
                    raise
                time.sleep(delay)
                delay *= 2
        result = self.inspect_file(target_relative_path)
        if (
            not result.exists
            or result.identity != expected_source.identity
            or result.content_hash != expected_source.content_hash
        ):
            raise UnsafeFilesystemError("Atomic replacement postcondition did not hold")
        self.fsync_directory(_relative_parent(target_parts))
        if source_parts[:-1] != target_parts[:-1]:
            self.fsync_directory(_relative_parent(source_parts))
        return result

    def _verified_directory_windows(
        self,
        parts: tuple[str, ...],
        *,
        allow_missing: bool,
    ) -> _DirectoryProof | None:
        try:
            first = self._walk_windows_directory_once(parts)
        except _MissingPath as first_missing:
            try:
                self._walk_windows_directory_once(parts)
            except _MissingPath as second_missing:
                if (
                    allow_missing
                    and first_missing.component_index == second_missing.component_index
                    and first_missing.identities == second_missing.identities
                ):
                    return None
                if not allow_missing:
                    raise FileNotFoundError("/".join(parts)) from second_missing
                raise UnsafeFilesystemError(
                    "Directory ancestry changed while absence was checked"
                ) from second_missing
            raise UnsafeFilesystemError("Directory appeared while absence was checked") from None
        try:
            second = self._walk_windows_directory_once(parts)
        except _MissingPath as error:
            raise UnsafeFilesystemError("Directory disappeared during validation") from error
        if first.identities != second.identities:
            raise UnsafeFilesystemError("Directory ancestry changed during validation")
        return second

    def _walk_windows_directory_once(self, parts: tuple[str, ...]) -> _DirectoryProof:
        identities: list[FileIdentity] = []
        for index in range(len(parts) + 1):
            path = self.path.joinpath(*parts[:index])
            try:
                handle = _windows_open_handle(path, read=False)
            except FileNotFoundError as error:
                raise _MissingPath(index, tuple(identities)) from error
            try:
                info = _windows_handle_info(handle)
                if info.is_reparse_point or not info.is_directory:
                    raise UnsafeFilesystemError(f"Directory component is unsafe: {path.name}")
                self._require_windows_containment(_windows_final_path(handle))
                _windows_compare_lstat(path, info, expect_directory=True)
                if index == 0 and info.identity != self._root_identity:
                    raise UnsafeFilesystemError("Safe root identity changed")
                identities.append(info.identity)
            finally:
                _windows_close_handle(handle)
        return _DirectoryProof(tuple(identities))

    def _inspect_file_windows(self, parts: tuple[str, ...]) -> FileSnapshot:
        parent = self._verified_directory_windows(parts[:-1], allow_missing=True)
        if parent is None:
            return _missing_snapshot()
        path = self.path.joinpath(*parts)
        try:
            handle = _windows_open_handle(path, read=True)
        except FileNotFoundError:
            return self._confirm_missing_file_windows(parts, parent)
        try:
            opened = _windows_handle_info(handle)
            if opened.is_reparse_point or opened.is_directory:
                raise UnsafeFilesystemError("File leaf must be a non-reparse regular file")
            self._require_windows_containment(_windows_final_path(handle))
            _windows_compare_lstat(path, opened, expect_directory=False)
            repeated_parent = self._verified_directory_windows(parts[:-1], allow_missing=False)
            if repeated_parent is None or repeated_parent.identities != parent.identities:
                raise UnsafeFilesystemError("File ancestry changed during validation")
            _windows_compare_lstat(path, opened, expect_directory=False)
            content = _windows_read_handle(handle)
            final = _windows_handle_info(handle)
            if final != opened or len(content) != final.size:
                raise UnsafeFilesystemError("File changed while its bytes were read")
            return _snapshot(
                final.identity,
                content,
                modified_ns=_windows_filetime_to_unix_ns(final.write_time),
            )
        finally:
            _windows_close_handle(handle)

    def _inspect_entry_windows(self, parts: tuple[str, ...]) -> DirectoryEntry | None:
        parent = self._verified_directory_windows(parts[:-1], allow_missing=True)
        if parent is None:
            return None
        path = self.path.joinpath(*parts)
        with self._hold_windows_directory_chain(parts[:-1]) as held_parent:
            if held_parent.identities != parent.identities:
                raise UnsafeFilesystemError("Entry ancestry changed during validation")
            try:
                handle = _windows_open_handle(path, read=False, share_delete=False)
            except FileNotFoundError:
                try:
                    repeated = _windows_open_handle(path, read=False, share_delete=False)
                except FileNotFoundError:
                    return None
                _windows_close_handle(repeated)
                raise UnsafeFilesystemError("Entry appeared while absence was checked") from None
            try:
                opened = _windows_handle_info(handle)
                if opened.is_reparse_point:
                    raise UnsafeFilesystemError("Entry leaf must not be a reparse point")
                self._require_windows_containment(_windows_final_path(handle))
                _windows_compare_lstat(path, opened, expect_directory=opened.is_directory)

                repeated = _windows_open_handle(path, read=False, share_delete=False)
                try:
                    repeated_info = _windows_handle_info(repeated)
                    if repeated_info.is_reparse_point:
                        raise UnsafeFilesystemError("Entry leaf became a reparse point")
                    self._require_windows_containment(_windows_final_path(repeated))
                    _windows_compare_lstat(
                        path,
                        repeated_info,
                        expect_directory=opened.is_directory,
                    )
                    if repeated_info.identity != opened.identity:
                        raise UnsafeFilesystemError("Entry identity changed during validation")
                finally:
                    _windows_close_handle(repeated)

                final = _windows_handle_info(handle)
                if (
                    final.identity != opened.identity
                    or final.is_directory is not opened.is_directory
                    or final.is_reparse_point
                ):
                    raise UnsafeFilesystemError("Entry changed during metadata inspection")
                relative = "/".join(parts)
                return DirectoryEntry(
                    name=parts[-1],
                    path=relative,
                    identity=final.identity,
                    is_directory=final.is_directory,
                    size=final.size,
                )
            finally:
                _windows_close_handle(handle)

    def _confirm_missing_file_windows(
        self,
        parts: tuple[str, ...],
        parent: _DirectoryProof,
    ) -> FileSnapshot:
        repeated_parent = self._verified_directory_windows(parts[:-1], allow_missing=True)
        if repeated_parent is None:
            raise UnsafeFilesystemError("File ancestry disappeared while absence was checked")
        if repeated_parent.identities != parent.identities:
            raise UnsafeFilesystemError("File ancestry changed while absence was checked")
        try:
            os.lstat(self.path.joinpath(*parts))
        except FileNotFoundError:
            return _missing_snapshot()
        raise UnsafeFilesystemError("File appeared while absence was checked")

    def _list_directory_windows(self, parts: tuple[str, ...]) -> tuple[DirectoryEntry, ...]:
        directory = self.path.joinpath(*parts)
        held_entries: list[int] = []
        with self._hold_windows_directory_chain(parts):
            names = os.listdir(directory)
            entries: list[DirectoryEntry] = []
            seen: set[str] = set()
            try:
                for name in names:
                    _validate_single_component(name)
                    key = unicodedata.normalize("NFC", name).casefold()
                    if key in seen:
                        raise UnsafeFilesystemError("Directory contains colliding entry names")
                    seen.add(key)
                    path = directory / name
                    handle = _windows_open_handle(path, read=False, share_delete=False)
                    held_entries.append(handle)
                    info = _windows_handle_info(handle)
                    if info.is_reparse_point:
                        raise UnsafeFilesystemError(f"Directory entry is a reparse point: {name}")
                    _windows_compare_lstat(path, info, expect_directory=info.is_directory)
                    self._require_windows_containment(_windows_final_path(handle))
                    relative = "/".join((*parts, name))
                    entries.append(
                        DirectoryEntry(
                            name=name,
                            path=relative,
                            identity=info.identity,
                            is_directory=info.is_directory,
                            size=info.size,
                        )
                    )
                repeated_names = os.listdir(directory)
                if {
                    unicodedata.normalize("NFC", name).casefold() for name in repeated_names
                } != seen:
                    raise UnsafeFilesystemError("Directory entries changed while listed")
            finally:
                for handle in reversed(held_entries):
                    _windows_close_handle(handle)
        return tuple(sorted(entries, key=lambda entry: (collision_key(entry.path), entry.path)))

    def _ensure_directories_windows(self, parts: tuple[str, ...]) -> None:
        for index, _component in enumerate(parts, start=1):
            parent_parts = parts[: index - 1]
            child_parts = parts[:index]
            child = self.path.joinpath(*child_parts)
            with self._hold_windows_directory_chain(parent_parts):
                try:
                    handle = _windows_open_handle(child, read=False, share_delete=False)
                except FileNotFoundError:
                    with suppress(FileExistsError):
                        os.mkdir(child, mode=0o700)
                    handle = _windows_open_handle(child, read=False, share_delete=False)
                try:
                    info = _windows_handle_info(handle)
                    if info.is_reparse_point or not info.is_directory:
                        raise UnsafeFilesystemError(f"Directory component is unsafe: {child.name}")
                    self._require_windows_containment(_windows_final_path(handle))
                    _windows_compare_lstat(child, info, expect_directory=True)
                finally:
                    _windows_close_handle(handle)

    def _write_exclusive_windows(
        self,
        parts: tuple[str, ...],
        content: bytes,
        mode: int,
    ) -> None:
        path = self.path.joinpath(*parts)
        with self._hold_windows_directory_chain(parts[:-1]):
            try:
                existing = os.lstat(path)
            except FileNotFoundError:
                pass
            else:
                _reject_windows_reparse_stat(existing, parts[-1])
                if not stat.S_ISREG(existing.st_mode):
                    raise UnsafeFilesystemError("Exclusive target is not a regular file")
                raise FileExistsError(path)
            handle = _windows_create_exclusive_handle(path)
            try:
                opened = _windows_handle_info(handle)
                if opened.is_reparse_point or opened.is_directory:
                    raise UnsafeFilesystemError("Exclusive target is not a regular file")
                self._require_windows_containment(_windows_final_path(handle))
                _windows_compare_lstat(path, opened, expect_directory=False)
                _windows_write_handle(handle, content)
                final = _windows_handle_info(handle)
                if final.identity != opened.identity or final.size != len(content):
                    raise UnsafeFilesystemError("Exclusive file changed while it was written")
            finally:
                _windows_close_handle(handle)

    @contextmanager
    def _hold_windows_directory_chain(
        self,
        parts: tuple[str, ...],
    ) -> Iterator[_DirectoryProof]:
        """Hold every ancestor without delete sharing to prevent path substitution."""

        handles: list[int] = []
        identities: list[FileIdentity] = []
        try:
            for index in range(len(parts) + 1):
                path = self.path.joinpath(*parts[:index])
                handle = _windows_open_handle(path, read=False, share_delete=False)
                handles.append(handle)
                info = _windows_handle_info(handle)
                if info.is_reparse_point or not info.is_directory:
                    raise UnsafeFilesystemError(f"Directory component is unsafe: {path.name}")
                self._require_windows_containment(_windows_final_path(handle))
                _windows_compare_lstat(path, info, expect_directory=True)
                if index == 0 and info.identity != self._root_identity:
                    raise UnsafeFilesystemError("Safe root identity changed")
                identities.append(info.identity)
            yield _DirectoryProof(tuple(identities))
        finally:
            for handle in reversed(handles):
                _windows_close_handle(handle)

    def _require_windows_containment(self, final_path: str) -> None:
        candidate = _windows_normal_path(Path(final_path))
        try:
            common = ntpath.commonpath((self._root_final, candidate))
        except ValueError as error:
            raise UnsafeFilesystemError("Opened path is outside the safe root") from error
        if ntpath.normcase(common) != ntpath.normcase(self._root_final):
            raise UnsafeFilesystemError("Opened path is outside the safe root")


def _file_parts(path: str) -> tuple[str, ...]:
    return tuple(validate_relative_path(path).split("/"))


def _directory_parts(path: str) -> tuple[str, ...]:
    if path == ".":
        return ()
    return _file_parts(path)


def _relative_parent(parts: tuple[str, ...]) -> str:
    return "/".join(parts[:-1]) or "."


def _validate_single_component(name: str) -> None:
    if "/" in name or "\\" in name:
        raise UnsafeFilesystemError("Directory entry name contains a separator")
    try:
        validate_relative_path(name)
    except UnsafePathError as error:
        raise UnsafeFilesystemError(f"Directory contains an unsafe entry name: {name}") from error


def _require_distinct_paths(source: str, target: str) -> None:
    if collision_key(source) == collision_key(target):
        raise UnsafePathError("Source and target paths collide")


def _identity_from_stat(value: os.stat_result) -> FileIdentity:
    return FileIdentity(device=int(value.st_dev), inode=int(value.st_ino))


def _snapshot(
    identity: FileIdentity,
    content: bytes,
    *,
    modified_ns: int,
) -> FileSnapshot:
    return FileSnapshot(
        exists=True,
        identity=identity,
        size=len(content),
        content_hash=_content_hash(content),
        content=content,
        modified_ns=modified_ns,
    )


def _missing_snapshot() -> FileSnapshot:
    return FileSnapshot(False, None, None, None, None, None)


def _windows_filetime_to_unix_ns(filetime: int) -> int:
    windows_epoch_offset = 116_444_736_000_000_000
    return max(0, filetime - windows_epoch_offset) * 100


def _content_hash(content: bytes) -> str:
    return f"{_HASH_PREFIX}{hashlib.sha256(content).hexdigest()}"


def _same_snapshot(left: FileSnapshot, right: FileSnapshot) -> bool:
    return (
        left.exists == right.exists
        and left.identity == right.identity
        and left.size == right.size
        and left.content_hash == right.content_hash
    )


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, _READ_CHUNK_SIZE)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_descriptor(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("Unable to write complete file content")
        written += count


def _stable_posix_file(
    before: os.stat_result,
    after: os.stat_result,
    content_size: int,
) -> bool:
    return (
        os.path.samestat(before, after)
        and before.st_size == after.st_size == content_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _posix_directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise UnsafeFilesystemError("This POSIX platform lacks required no-follow flags")
    return int(os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0))


def _posix_file_read_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise UnsafeFilesystemError("This POSIX platform lacks O_NOFOLLOW")
    return int(os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0))


def _posix_file_metadata_flags() -> int:
    return _posix_file_read_flags()


def _posix_entry_metadata_flags(*, is_directory: bool) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise UnsafeFilesystemError("This POSIX platform lacks O_NOFOLLOW")
    path_only = getattr(os, "O_PATH", None)
    event_only = getattr(os, "O_EVTONLY", None)
    access = path_only if path_only is not None else event_only
    if access is None:
        access = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    directory = getattr(os, "O_DIRECTORY", 0) if is_directory else 0
    return int(access | nofollow | directory | getattr(os, "O_CLOEXEC", 0))


def _posix_entry_is_directory(value: os.stat_result, label: str) -> bool:
    if stat.S_ISDIR(value.st_mode):
        return True
    if stat.S_ISREG(value.st_mode):
        return False
    raise UnsafeFilesystemError(f"Entry leaf is not a regular file or directory: {label}")


def _posix_file_create_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise UnsafeFilesystemError("This POSIX platform lacks O_NOFOLLOW")
    return int(os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | getattr(os, "O_CLOEXEC", 0))


def _posix_open_absolute_directory(path: Path) -> int:
    return os.open(path, _posix_directory_flags())


def _require_posix_directory(value: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(value.st_mode):
        raise UnsafeFilesystemError(f"Directory component is unsafe: {label}")


def _require_posix_regular(value: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise UnsafeFilesystemError(f"File leaf is not a regular file: {label}")


def _require_absent_or_safe_regular_at(parent_descriptor: int, name: str) -> None:
    try:
        linked = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    _require_posix_regular(linked, name)
    raise FileExistsError(name)


def _windows_api() -> Any:
    if os.name != "nt":
        raise UnsafeFilesystemError("Windows filesystem API requested on a non-Windows host")
    return _CTYPES.WinDLL("kernel32", use_last_error=True)


def _windows_open_handle(path: Path, *, read: bool, share_delete: bool = True) -> int:
    kernel32 = _windows_api()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    access = _FILE_READ_ATTRIBUTES | (_GENERIC_READ if read else 0)
    share_mode = _FILE_SHARE_READ | _FILE_SHARE_WRITE
    if share_delete:
        share_mode |= _FILE_SHARE_DELETE
    handle_value = create_file(
        str(path),
        access,
        share_mode,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle_value in {None, invalid_handle}:
        error_code = int(_CTYPES.get_last_error())
        if error_code in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            raise FileNotFoundError(error_code, "Path does not exist", str(path))
        raise _windows_os_error(error_code, path)
    return int(handle_value)


def _windows_create_exclusive_handle(path: Path) -> int:
    kernel32 = _windows_api()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle_value = create_file(
        str(path),
        _FILE_READ_ATTRIBUTES | _GENERIC_WRITE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _CREATE_NEW,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle_value in {None, invalid_handle}:
        error_code = int(_CTYPES.get_last_error())
        if error_code in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
            raise FileExistsError(error_code, "Path already exists", str(path))
        if error_code in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            raise FileNotFoundError(error_code, "Parent path does not exist", str(path))
        raise _windows_os_error(error_code, path)
    return int(handle_value)


def _windows_close_handle(handle: int) -> None:
    kernel32 = _windows_api()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    close_handle(ctypes.c_void_p(handle))


def _windows_handle_info(handle: int) -> _WindowsHandleInfo:
    kernel32 = _windows_api()
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation)]
    get_information.restype = ctypes.c_int
    raw = _ByHandleFileInformation()
    if not get_information(ctypes.c_void_p(handle), ctypes.byref(raw)):
        error_code = int(_CTYPES.get_last_error())
        raise _windows_os_error(error_code)
    return _WindowsHandleInfo(
        identity=FileIdentity(
            device=int(raw.dwVolumeSerialNumber),
            inode=(int(raw.nFileIndexHigh) << 32) | int(raw.nFileIndexLow),
        ),
        attributes=int(raw.dwFileAttributes),
        size=(int(raw.nFileSizeHigh) << 32) | int(raw.nFileSizeLow),
        creation_time=(int(raw.ftCreationTimeHigh) << 32) | int(raw.ftCreationTimeLow),
        write_time=(int(raw.ftLastWriteTimeHigh) << 32) | int(raw.ftLastWriteTimeLow),
    )


def _windows_final_path(handle: int) -> str:
    kernel32 = _windows_api()
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
    get_final_path.restype = ctypes.c_ulong
    capacity = 512
    while True:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = int(
            get_final_path(
                ctypes.c_void_p(handle),
                buffer,
                capacity,
                0,
            )
        )
        if length == 0:
            error_code = int(_CTYPES.get_last_error())
            raise _windows_os_error(error_code)
        if length < capacity:
            return _strip_windows_extended_prefix(buffer.value)
        capacity = length + 1


def _windows_read_handle(handle: int) -> bytes:
    kernel32 = _windows_api()
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    ]
    read_file.restype = ctypes.c_int
    chunks: list[bytes] = []
    while True:
        buffer = ctypes.create_string_buffer(_READ_CHUNK_SIZE)
        count = ctypes.c_ulong(0)
        success = read_file(
            ctypes.c_void_p(handle),
            buffer,
            _READ_CHUNK_SIZE,
            ctypes.byref(count),
            None,
        )
        if not success:
            error_code = int(_CTYPES.get_last_error())
            if error_code == _ERROR_HANDLE_EOF:
                return b"".join(chunks)
            raise _windows_os_error(error_code)
        if count.value == 0:
            return b"".join(chunks)
        chunks.append(buffer.raw[: count.value])


def _windows_write_handle(handle: int, content: bytes) -> None:
    kernel32 = _windows_api()
    write_file = kernel32.WriteFile
    write_file.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    ]
    write_file.restype = ctypes.c_int
    offset = 0
    while offset < len(content):
        chunk = content[offset : offset + _READ_CHUNK_SIZE]
        buffer = ctypes.create_string_buffer(chunk)
        written = ctypes.c_ulong()
        if not write_file(
            ctypes.c_void_p(handle),
            buffer,
            len(chunk),
            ctypes.byref(written),
            None,
        ):
            raise _windows_os_error(int(_CTYPES.get_last_error()))
        if written.value <= 0:
            raise UnsafeFilesystemError("Windows file write made no progress")
        offset += int(written.value)
    flush = kernel32.FlushFileBuffers
    flush.argtypes = [ctypes.c_void_p]
    flush.restype = ctypes.c_int
    if not flush(ctypes.c_void_p(handle)):
        raise _windows_os_error(int(_CTYPES.get_last_error()))


def _windows_compare_lstat(
    path: Path,
    info: _WindowsHandleInfo,
    *,
    expect_directory: bool,
) -> None:
    try:
        linked = os.lstat(path)
    except FileNotFoundError as error:
        raise UnsafeFilesystemError("Opened object disappeared during lstat comparison") from error
    _reject_windows_reparse_stat(linked, path.name)
    if expect_directory:
        if not stat.S_ISDIR(linked.st_mode) or not info.is_directory:
            raise UnsafeFilesystemError("Opened directory has an unsafe type")
    elif not stat.S_ISREG(linked.st_mode) or info.is_directory:
        raise UnsafeFilesystemError("Opened file leaf is not regular")
    if int(linked.st_ino) != info.identity.inode:
        raise UnsafeFilesystemError("lstat and handle identities do not match")
    if not expect_directory and int(linked.st_size) != info.size:
        raise UnsafeFilesystemError("lstat and handle sizes do not match")


def _reject_windows_reparse_stat(value: os.stat_result, label: str) -> None:
    attributes = int(getattr(value, "st_file_attributes", 0))
    if stat.S_ISLNK(value.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafeFilesystemError(f"Path component is a reparse point: {label}")


def _strip_windows_extended_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return f"\\\\{path[8:]}"
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _windows_normal_path(path: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path)))


def _windows_os_error(error_code: int, path: Path | None = None) -> OSError:
    message = str(_CTYPES.FormatError(error_code))
    if path is None:
        return OSError(error_code, message)
    return OSError(error_code, message, str(path))
