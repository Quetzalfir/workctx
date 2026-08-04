"""Atomic names-only index for OS credential-store entries."""

from __future__ import annotations

import errno
import json
import os
import secrets as stdlib_secrets
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from platformdirs import user_config_path

from workctx.secrets.errors import SecretIndexError
from workctx.secrets.models import SecretRef

INDEX_SCHEMA_VERSION = 1
INDEX_FILENAME = "secret-names.json"
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.01


class SecretNamesIndex:
    """Persist only validated names; secret values never enter this adapter."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else _default_index_path()

    def list(self) -> tuple[str, ...]:
        return self._load()

    def add(self, ref: SecretRef) -> None:
        with _mutation_guard(self.path):
            names = set(self._load())
            if ref.name in names:
                return
            names.add(ref.name)
            self._save(tuple(sorted(names)))

    def remove(self, ref: SecretRef) -> bool:
        with _mutation_guard(self.path):
            names = set(self._load())
            if ref.name not in names:
                return False
            names.remove(ref.name)
            self._save(tuple(sorted(names)))
            return True

    def _load(self) -> tuple[str, ...]:
        path = self.path
        if path.is_symlink():
            raise SecretIndexError
        if not path.exists():
            return ()
        if not _is_regular_file(path):
            raise SecretIndexError

        failed = False
        loaded: Any = None
        try:
            with path.open("r", encoding="utf-8") as stream:
                loaded = json.load(stream, object_pairs_hook=_reject_duplicate_keys)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            failed = True
        if failed:
            raise SecretIndexError
        return _parse_index(loaded)

    def _save(self, names: tuple[str, ...]) -> None:
        path = self.path
        failed = False
        temp: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or (path.exists() and not _is_regular_file(path)):
                raise OSError
            payload = (
                json.dumps(
                    {"schema_version": INDEX_SCHEMA_VERSION, "names": list(names)},
                    ensure_ascii=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
            temp = path.with_name(f".{path.name}.{stdlib_secrets.token_hex(8)}.tmp")
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            _fsync_directory(path.parent)
        except OSError:
            failed = True
        finally:
            if temp is not None:
                with suppress(OSError):
                    temp.unlink()
        if failed:
            raise SecretIndexError


def _default_index_path() -> Path:
    return user_config_path("workctx", appauthor=False) / INDEX_FILENAME


def _parse_index(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "names"}:
        raise SecretIndexError
    schema_version = value["schema_version"]
    raw_names = value["names"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != INDEX_SCHEMA_VERSION
        or not isinstance(raw_names, list)
    ):
        raise SecretIndexError

    names: list[str] = []
    for raw_name in raw_names:
        try:
            ref = SecretRef.parse(raw_name)
        except Exception:
            raise SecretIndexError from None
        names.append(ref.name)
    if len(names) != len(set(names)) or names != sorted(names):
        raise SecretIndexError
    return tuple(names)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    except OSError:
        return False


@contextmanager
def _mutation_guard(index_path: Path) -> Iterator[None]:
    failed = False
    descriptor = -1
    guard_path = index_path.with_name(f".{index_path.name}.lock")
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(guard_path, flags, 0o600)
        opened = os.fstat(descriptor)
        linked = guard_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(linked.st_mode)
            or not os.path.samestat(opened, linked)
        ):
            raise OSError
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
    except OSError:
        failed = True
    if failed:
        if descriptor >= 0:
            os.close(descriptor)
        raise SecretIndexError

    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    try:
        while not _try_lock(descriptor):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SecretIndexError
            time.sleep(min(_LOCK_POLL_SECONDS, remaining))
        yield
    finally:
        with suppress(OSError):
            _unlock(descriptor)
        os.close(descriptor)


def _try_lock(descriptor: int) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise SecretIndexError from None
    return True


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


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
