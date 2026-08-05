"""User-level registry of context IDs, roots, and explicit active selection."""

from __future__ import annotations

import errno
import json
import os
import secrets
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from platformdirs import user_config_path

from workctx.adapters.filesystem._paths import canonical_context_root
from workctx.adapters.filesystem.serialization import load_json_model, load_yaml_model
from workctx.domain.artifacts import ArtifactManifest, ArtifactStatus
from workctx.errors import ConflictError, WorkctxError
from workctx.models.context import ContextConfig

REGISTRY_SCHEMA_VERSION = 1
REGISTRY_FILENAME = "contexts.json"
_MUTATION_GUARD_TIMEOUT_SECONDS = 5.0
_MUTATION_GUARD_POLL_SECONDS = 0.01


class RegistryError(WorkctxError):
    """Raised when the user-level context registry is malformed or unavailable."""


class RegistryConflictError(ConflictError):
    """Raised when a registry mutation conflicts with another writer or registration."""


@dataclass(frozen=True, slots=True)
class RegisteredContext:
    context_id: str
    root: Path
    active: bool = False


@dataclass(frozen=True, slots=True)
class ContextInventoryStats:
    """Cheap canonical-file and verified-ledger counts for one context."""

    tasks: int
    entities: int
    evidence_notes: int
    pending_inbox_artifacts: int
    ledger_events: int
    last_ledger_activity: datetime | None


@dataclass(frozen=True, slots=True)
class ContextInventoryEntry:
    """Advisory details for one registered context without projection access."""

    context_id: str
    root: Path
    active: bool
    configured_context_id: str | None
    name: str | None
    kind: str | None
    profile: str | None
    language: str | None
    missing: bool
    mismatched: bool
    stats: ContextInventoryStats | None
    error: str | None


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    schema_version: int = REGISTRY_SCHEMA_VERSION
    active_context_id: str | None = None
    contexts: tuple[RegisteredContext, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "active_context_id": self.active_context_id,
            "contexts": {
                item.context_id: str(item.root)
                for item in sorted(self.contexts, key=lambda context: context.context_id)
            },
        }


class ContextRegistry:
    """Atomic API over the platform-appropriate per-user context registry."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self.path = (
            registry_file.expanduser().absolute()
            if registry_file is not None
            else user_config_path("workctx", appauthor=False) / REGISTRY_FILENAME
        )
        _reject_registry_inside_context(self.path)

    def list(self) -> tuple[RegisteredContext, ...]:
        """List registrations in stable context-ID order."""

        return self._load().contexts

    def register(
        self,
        context_id: str,
        context_root: Path,
        *,
        make_active: bool = False,
        replace: bool = False,
    ) -> RegisteredContext:
        """Register a validated root without silently rebinding an existing ID."""

        root = canonical_context_root(context_root)
        _require_registry_outside_roots(self.path, (root,))
        actual_id = _load_registered_context_id(root)
        if context_id != actual_id:
            raise RegistryError(
                f"Registry ID '{context_id}' does not match context.yaml ID '{actual_id}'"
            )

        with _registry_mutation_guard(self.path):
            snapshot = self._load()
            contexts = {item.context_id: item.root for item in snapshot.contexts}
            existing = contexts.get(context_id)
            if existing is not None and existing != root and not replace:
                raise RegistryConflictError(
                    f"Context '{context_id}' is already registered to another root"
                )
            contexts[context_id] = root
            active_id = context_id if make_active else snapshot.active_context_id
            updated = _snapshot(active_id, contexts)
            if updated != snapshot:
                self._save(updated)
            return RegisteredContext(context_id, root, active_id == context_id)

    def unregister(self, context_id: str) -> bool:
        """Idempotently remove a registration and clear it if active."""

        with _registry_mutation_guard(self.path):
            snapshot = self._load()
            contexts = {item.context_id: item.root for item in snapshot.contexts}
            if context_id not in contexts:
                return False
            del contexts[context_id]
            active_id = (
                None if snapshot.active_context_id == context_id else snapshot.active_context_id
            )
            self._save(_snapshot(active_id, contexts))
            return True

    def set_active(self, context_id: str | None) -> None:
        """Explicitly select or clear the active user-level context."""

        with _registry_mutation_guard(self.path):
            snapshot = self._load()
            contexts = {item.context_id: item.root for item in snapshot.contexts}
            if context_id is not None and context_id not in contexts:
                raise RegistryError(f"Context '{context_id}' is not registered")
            updated = _snapshot(context_id, contexts)
            if updated != snapshot:
                self._save(updated)

    def get_active(self) -> Path | None:
        """Return the active valid root, failing closed for stale registrations."""

        snapshot = self._load()
        if snapshot.active_context_id is None:
            return None
        return self._validated_root(snapshot.active_context_id, snapshot)

    def get(self, context_id: str) -> Path | None:
        """Return one valid registered root, or ``None`` when it is unregistered."""

        snapshot = self._load()
        if not any(item.context_id == context_id for item in snapshot.contexts):
            return None
        return self._validated_root(context_id, snapshot)

    def _validated_root(self, context_id: str, snapshot: RegistrySnapshot) -> Path:
        roots = {item.context_id: item.root for item in snapshot.contexts}
        root = roots[context_id]
        try:
            canonical = canonical_context_root(root)
            actual_id = _load_registered_context_id(canonical)
        except (OSError, ValueError, WorkctxError) as exc:
            raise RegistryError(f"Registered context '{context_id}' is unavailable") from exc
        if actual_id != context_id:
            raise RegistryError(f"Registered context '{context_id}' no longer matches context.yaml")
        return canonical

    def _load(self) -> RegistrySnapshot:
        _reject_registry_inside_context(self.path)
        if self.path.is_symlink():
            raise RegistryError("The user-level registry must be a regular file")
        if not self.path.exists():
            return RegistrySnapshot()
        _require_regular_registry_file(self.path)
        try:
            with self.path.open("rb") as stream:
                loaded: Any = json.load(stream, object_pairs_hook=_reject_duplicate_object_keys)
            snapshot = _parse_snapshot(loaded)
            _require_registry_outside_roots(
                self.path,
                (context.root for context in snapshot.contexts),
            )
            return snapshot
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RegistryError("The user-level context registry is malformed") from exc

    def _save(self, snapshot: RegistrySnapshot) -> None:
        _reject_registry_inside_context(self.path)
        _require_registry_outside_roots(
            self.path,
            (context.root for context in snapshot.contexts),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.exists():
            _require_regular_registry_file(self.path)
        payload = (json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        temp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            _fsync_directory(self.path.parent)
        finally:
            with suppress(FileNotFoundError):
                temp.unlink()


def list_contexts(*, registry_file: Path | None = None) -> tuple[RegisteredContext, ...]:
    return ContextRegistry(registry_file).list()


def list_context_inventory(
    *,
    registry_file: Path | None = None,
) -> tuple[ContextInventoryEntry, ...]:
    """Inspect registered contexts with read-only, projection-free filesystem reads.

    A malformed or unavailable registry remains an error for this low-level API so callers can
    distinguish it from a valid empty registry. Individual stale or unreadable registrations are
    isolated into their own inventory rows.
    """

    return tuple(
        _inspect_registered_context(registered)
        for registered in ContextRegistry(registry_file).list()
    )


def register_context(
    context_id: str,
    context_root: Path,
    *,
    make_active: bool = False,
    replace: bool = False,
    registry_file: Path | None = None,
) -> RegisteredContext:
    return ContextRegistry(registry_file).register(
        context_id,
        context_root,
        make_active=make_active,
        replace=replace,
    )


def unregister_context(context_id: str, *, registry_file: Path | None = None) -> bool:
    return ContextRegistry(registry_file).unregister(context_id)


def set_active_context(
    context_id: str | None,
    *,
    registry_file: Path | None = None,
) -> None:
    ContextRegistry(registry_file).set_active(context_id)


def get_active_context(*, registry_file: Path | None = None) -> Path | None:
    return ContextRegistry(registry_file).get_active()


def _inspect_registered_context(registered: RegisteredContext) -> ContextInventoryEntry:
    config_path = registered.root / "context.yaml"
    try:
        metadata = config_path.lstat()
    except FileNotFoundError:
        return _inventory_entry(registered, missing=True)
    except OSError:
        return _inventory_entry(
            registered,
            error="Unable to read context configuration.",
        )

    if not stat.S_ISREG(metadata.st_mode):
        return _inventory_entry(
            registered,
            error="Context configuration is not a regular file.",
        )

    try:
        with config_path.open("rb") as stream:
            config = load_yaml_model(stream.read(), ContextConfig)
    except Exception:
        return _inventory_entry(
            registered,
            error="Unable to read context configuration.",
        )

    mismatched = config.id != registered.context_id
    try:
        from workctx.transactions import audit_summary

        summary = audit_summary(registered.root)
        stats = ContextInventoryStats(
            tasks=_count_markdown_documents(registered.root / "03_work" / "tasks"),
            entities=_count_markdown_documents(registered.root / "02_knowledge"),
            evidence_notes=_count_markdown_documents(registered.root / "02_knowledge" / "evidence"),
            pending_inbox_artifacts=_count_pending_inbox_artifacts(
                registered.root / "00_inbox" / "manifests"
            ),
            ledger_events=summary.event_count,
            last_ledger_activity=summary.last_timestamp,
        )
    except Exception:
        return _inventory_entry(
            registered,
            config=config,
            mismatched=mismatched,
            error="Unable to read context inventory statistics.",
        )

    return _inventory_entry(
        registered,
        config=config,
        mismatched=mismatched,
        stats=stats,
    )


def _inventory_entry(
    registered: RegisteredContext,
    *,
    config: ContextConfig | None = None,
    missing: bool = False,
    mismatched: bool = False,
    stats: ContextInventoryStats | None = None,
    error: str | None = None,
) -> ContextInventoryEntry:
    return ContextInventoryEntry(
        context_id=registered.context_id,
        root=registered.root,
        active=registered.active,
        configured_context_id=config.id if config is not None else None,
        name=config.name if config is not None else None,
        kind=config.kind.value if config is not None else None,
        profile=config.profile.value if config is not None else None,
        language=config.languages.user_interaction if config is not None else None,
        missing=missing,
        mismatched=mismatched,
        stats=stats,
        error=error,
    )


def _count_markdown_documents(directory: Path) -> int:
    if not _inventory_directory_exists(directory):
        return 0

    count = 0
    for current, directories, filenames in os.walk(
        directory,
        topdown=True,
        onerror=_raise_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if not name.startswith(".") and not _is_link(current_path / name)
        ]
        for name in filenames:
            path = current_path / name
            if (
                name.startswith(".")
                or name.casefold() == "readme.md"
                or path.suffix.casefold() != ".md"
                or _is_link(path)
            ):
                continue
            if path.is_file():
                count += 1
    return count


def _count_pending_inbox_artifacts(directory: Path) -> int:
    if not _inventory_directory_exists(directory):
        return 0

    pending = 0
    for path in sorted(directory.iterdir(), key=lambda candidate: candidate.name.casefold()):
        if path.name.startswith("."):
            continue
        suffix = path.suffix.casefold()
        if suffix not in {".json", ".yaml", ".yml"}:
            continue
        if _is_link(path) or not path.is_file():
            raise RegistryError("Inbox manifests must be regular files")
        payload = path.read_bytes()
        manifest = (
            load_json_model(payload, ArtifactManifest)
            if suffix == ".json"
            else load_yaml_model(payload, ArtifactManifest)
        )
        if manifest.status is ArtifactStatus.PENDING:
            pending += 1
    return pending


def _inventory_directory_exists(directory: Path) -> bool:
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(metadata.st_mode):
        raise RegistryError("Context inventory path is not a regular directory")
    return True


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return is_junction is not None and bool(is_junction())


def _raise_walk_error(error: OSError) -> None:
    raise error


@contextmanager
def _registry_mutation_guard(registry_path: Path) -> Iterator[None]:
    """Serialize mutations with a crash-released OS lock on a stable sentinel."""

    _reject_registry_inside_context(registry_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    guard_path = registry_path.with_name(f".{registry_path.name}.lock")
    descriptor = _open_registry_mutation_guard(guard_path)
    deadline = time.monotonic() + _MUTATION_GUARD_TIMEOUT_SECONDS

    try:
        while not _try_lock_registry_guard(descriptor):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RegistryConflictError(
                    "The user-level registry is currently being modified by another process"
                ) from None
            time.sleep(min(_MUTATION_GUARD_POLL_SECONDS, remaining))
        yield
    finally:
        with suppress(OSError):
            _unlock_registry_guard(descriptor)
        os.close(descriptor)


def _open_registry_mutation_guard(path: Path) -> int:
    """Open one stable regular sentinel without following a symlink."""

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RegistryError("Unable to open the user-level registry mutation guard") from exc

    try:
        opened = os.fstat(descriptor)
        linked = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(linked.st_mode)
            or not os.path.samestat(opened, linked)
        ):
            raise RegistryError("The user-level registry mutation guard must be a regular file")
        if opened.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
            _fsync_directory(path.parent)
        return descriptor
    except RegistryError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise RegistryError("Unable to inspect the user-level registry mutation guard") from exc
    except BaseException:
        os.close(descriptor)
        raise


def _try_lock_registry_guard(descriptor: int) -> bool:
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
        raise RegistryError("Unable to lock the user-level registry mutation guard") from exc
    return True


def _unlock_registry_guard(descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Registry JSON contains a duplicate object key: {key}")
        result[key] = value
    return result


def _load_registered_context_id(root: Path) -> str:
    config_path = root / "context.yaml"
    if not config_path.is_file() or config_path.is_symlink():
        raise RegistryError(f"Missing regular context.yaml in {root}")
    with config_path.open("rb") as stream:
        return load_yaml_model(stream.read(), ContextConfig).id


def _parse_snapshot(value: object) -> RegistrySnapshot:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "active_context_id",
        "contexts",
    }:
        raise ValueError("Registry has an invalid object shape")
    schema_version = value["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != REGISTRY_SCHEMA_VERSION
    ):
        raise ValueError("Registry schema_version is unsupported")
    active = value["active_context_id"]
    if active is not None and (not isinstance(active, str) or not active):
        raise ValueError("Registry active_context_id is invalid")
    raw_contexts = value["contexts"]
    if not isinstance(raw_contexts, dict):
        raise ValueError("Registry contexts must be an object")

    contexts: dict[str, Path] = {}
    for context_id, raw_root in raw_contexts.items():
        if not isinstance(context_id, str) or not context_id:
            raise ValueError("Registry context IDs must be non-empty strings")
        if not isinstance(raw_root, str) or not raw_root:
            raise ValueError("Registry roots must be non-empty strings")
        root = Path(raw_root)
        if not root.is_absolute():
            raise ValueError("Registry roots must be machine-absolute paths")
        contexts[context_id] = root
    if active is not None and active not in contexts:
        raise ValueError("Registry active_context_id is not registered")
    return _snapshot(active, contexts)


def _snapshot(active_id: str | None, contexts: dict[str, Path]) -> RegistrySnapshot:
    return RegistrySnapshot(
        active_context_id=active_id,
        contexts=tuple(
            RegisteredContext(context_id, root, context_id == active_id)
            for context_id, root in sorted(contexts.items())
        ),
    )


def _require_regular_registry_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RegistryError("The user-level registry must be a regular file")


def _reject_registry_inside_context(path: Path) -> None:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RegistryError("Unable to resolve the user-level registry path") from exc
    for parent in resolved.parents:
        if (parent / "context.yaml").is_file():
            raise RegistryError("The user-level registry must be outside every context root")


def _require_registry_outside_roots(path: Path, roots: Iterator[Path] | tuple[Path, ...]) -> None:
    try:
        registry_path = path.resolve(strict=False)
        for root in roots:
            resolved_root = root.expanduser().resolve(strict=False)
            if _path_is_within(registry_path, resolved_root):
                raise RegistryError("The user-level registry must be outside every context root")
    except RegistryError:
        raise
    except (OSError, RuntimeError) as exc:
        raise RegistryError("Unable to verify the user-level registry boundary") from exc


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(path), os.path.normcase(root)))
    except ValueError:
        return False
    return common == os.path.normcase(root)


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
