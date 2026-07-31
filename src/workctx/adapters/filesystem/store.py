"""Typed, zone-aware access to canonical context documents."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from workctx.adapters.filesystem._paths import (
    ContextZone,
    canonical_context_root,
    resolve_context_path,
)
from workctx.adapters.filesystem.lock import ContextLock
from workctx.adapters.filesystem.serialization import (
    MarkdownDocument,
    dump_json_bytes,
    dump_yaml_bytes,
    has_hand_edits_json,
    has_hand_edits_markdown,
    has_hand_edits_yaml,
    load_json_model,
    load_markdown_model,
    load_yaml_model,
    render_markdown_bytes,
)
from workctx.adapters.filesystem.staging import StagedWrite, atomic_replace_bytes
from workctx.domain.artifacts import ArtifactManifest
from workctx.domain.entities import EntityFrontmatter
from workctx.domain.references import WorkctxUri
from workctx.domain.tasks import Task
from workctx.errors import ContextBoundaryError, ContextNotFoundError, InvalidContextError
from workctx.models.context import ContextConfig

_ENTITY_ZONES = (
    ContextZone.KNOWLEDGE.value,
    ContextZone.WORK.value,
    ContextZone.OUTBOX.value,
)
_TASK_ZONES = (ContextZone.WORK.value,)
_MANIFEST_ZONES = (f"{ContextZone.INBOX.value}/manifests",)
_ARTIFACT_CONTENT_ZONES = (ContextZone.INBOX.value, ContextZone.PROCESSED.value)


class CanonicalStore:
    """A canonical document store bound to exactly one resolved context root."""

    def __init__(self, context_root: Path) -> None:
        self._context_root = canonical_context_root(context_root)
        config_path = resolve_context_path(
            self._context_root,
            "context.yaml",
            allowed_root_files=("context.yaml",),
        )
        if not config_path.is_file():
            raise ContextNotFoundError(f"Missing context.yaml in {self._context_root}")
        try:
            self._config = load_yaml_model(_read_regular_bytes(config_path), ContextConfig)
        except (OSError, ValueError, ValidationError) as exc:
            raise InvalidContextError(f"Unable to load context.yaml: {exc}") from exc

    @property
    def context_root(self) -> Path:
        return self._context_root

    @property
    def context_id(self) -> str:
        return self._config.id

    def resolve_path(
        self,
        relative_path: str | Path,
        *,
        zones: Iterable[ContextZone],
    ) -> Path:
        """Resolve a path restricted to explicit canonical workspace zones."""

        prefixes = tuple(zone.value for zone in zones)
        if not prefixes:
            raise ContextBoundaryError("At least one canonical context zone is required")
        return resolve_context_path(self._context_root, relative_path, allowed_prefixes=prefixes)

    def read_context_config(self) -> ContextConfig:
        path = resolve_context_path(
            self._context_root,
            "context.yaml",
            allowed_root_files=("context.yaml",),
        )
        config = load_yaml_model(_read_regular_bytes(path), ContextConfig)
        self._require_context_id(config.id)
        return config

    def prepare_context_config(self, config: ContextConfig) -> StagedWrite:
        self._require_context_id(config.id)
        return StagedWrite("context.yaml", dump_yaml_bytes(config))

    def write_context_config(
        self,
        config: ContextConfig,
        *,
        lock: ContextLock | None = None,
    ) -> None:
        self._write_prepared(self.prepare_context_config(config), lock=lock)
        self._config = config

    def context_config_has_hand_edits(self) -> bool:
        path = resolve_context_path(
            self._context_root,
            "context.yaml",
            allowed_root_files=("context.yaml",),
        )
        return has_hand_edits_yaml(_read_regular_bytes(path), ContextConfig)

    def read_entity(self, relative_path: str | Path) -> MarkdownDocument[EntityFrontmatter]:
        path = self._document_path(relative_path, prefixes=_ENTITY_ZONES, suffixes=(".md",))
        document = load_markdown_model(_read_regular_bytes(path), EntityFrontmatter)
        self._require_non_task_entity(document.frontmatter)
        self._require_entity_context(document.frontmatter)
        return document

    def prepare_entity(
        self,
        relative_path: str | Path,
        frontmatter: EntityFrontmatter,
        body: str = "",
    ) -> StagedWrite:
        path = self._document_path(relative_path, prefixes=_ENTITY_ZONES, suffixes=(".md",))
        self._require_non_task_entity(frontmatter)
        self._require_entity_context(frontmatter)
        relative = path.relative_to(self._context_root).as_posix()
        return StagedWrite(relative, render_markdown_bytes(frontmatter, body))

    def write_entity(
        self,
        relative_path: str | Path,
        frontmatter: EntityFrontmatter,
        body: str = "",
        *,
        lock: ContextLock | None = None,
    ) -> None:
        self._write_prepared(self.prepare_entity(relative_path, frontmatter, body), lock=lock)

    def entity_has_hand_edits(self, relative_path: str | Path) -> bool:
        path = self._document_path(relative_path, prefixes=_ENTITY_ZONES, suffixes=(".md",))
        return has_hand_edits_markdown(_read_regular_bytes(path), EntityFrontmatter)

    def read_task(self, relative_path: str | Path) -> MarkdownDocument[Task]:
        path = self._document_path(relative_path, prefixes=_TASK_ZONES, suffixes=(".md",))
        document = load_markdown_model(_read_regular_bytes(path), Task)
        self._require_entity_context(document.frontmatter)
        return document

    def prepare_task(
        self,
        relative_path: str | Path,
        task: Task,
        body: str = "",
    ) -> StagedWrite:
        path = self._document_path(relative_path, prefixes=_TASK_ZONES, suffixes=(".md",))
        self._require_entity_context(task)
        relative = path.relative_to(self._context_root).as_posix()
        return StagedWrite(relative, render_markdown_bytes(task, body))

    def write_task(
        self,
        relative_path: str | Path,
        task: Task,
        body: str = "",
        *,
        lock: ContextLock | None = None,
    ) -> None:
        self._write_prepared(self.prepare_task(relative_path, task, body), lock=lock)

    def task_has_hand_edits(self, relative_path: str | Path) -> bool:
        path = self._document_path(relative_path, prefixes=_TASK_ZONES, suffixes=(".md",))
        return has_hand_edits_markdown(_read_regular_bytes(path), Task)

    def read_artifact_manifest(self, relative_path: str | Path) -> ArtifactManifest:
        path = self._document_path(
            relative_path,
            prefixes=_MANIFEST_ZONES,
            suffixes=(".yaml", ".yml", ".json"),
        )
        payload = _read_regular_bytes(path)
        manifest = (
            load_json_model(payload, ArtifactManifest)
            if path.suffix.lower() == ".json"
            else load_yaml_model(payload, ArtifactManifest)
        )
        self._validate_artifact_paths(manifest)
        return manifest

    def prepare_artifact_manifest(
        self,
        relative_path: str | Path,
        manifest: ArtifactManifest,
    ) -> StagedWrite:
        path = self._document_path(
            relative_path,
            prefixes=_MANIFEST_ZONES,
            suffixes=(".yaml", ".yml", ".json"),
        )
        self._validate_artifact_paths(manifest)
        content = (
            dump_json_bytes(manifest)
            if path.suffix.lower() == ".json"
            else dump_yaml_bytes(manifest)
        )
        return StagedWrite(path.relative_to(self._context_root).as_posix(), content)

    def write_artifact_manifest(
        self,
        relative_path: str | Path,
        manifest: ArtifactManifest,
        *,
        lock: ContextLock | None = None,
    ) -> None:
        self._write_prepared(
            self.prepare_artifact_manifest(relative_path, manifest),
            lock=lock,
        )

    def artifact_manifest_has_hand_edits(self, relative_path: str | Path) -> bool:
        path = self._document_path(
            relative_path,
            prefixes=_MANIFEST_ZONES,
            suffixes=(".yaml", ".yml", ".json"),
        )
        payload = _read_regular_bytes(path)
        if path.suffix.lower() == ".json":
            return has_hand_edits_json(payload, ArtifactManifest)
        return has_hand_edits_yaml(payload, ArtifactManifest)

    def _document_path(
        self,
        relative_path: str | Path,
        *,
        prefixes: tuple[str, ...],
        suffixes: tuple[str, ...],
    ) -> Path:
        path = resolve_context_path(
            self._context_root,
            relative_path,
            allowed_prefixes=prefixes,
        )
        if path.suffix.lower() not in suffixes:
            expected = ", ".join(suffixes)
            raise ContextBoundaryError(f"Canonical document must use one of: {expected}")
        return path

    def _require_context_id(self, context_id: str) -> None:
        if context_id != self.context_id:
            raise ContextBoundaryError(
                f"Document belongs to context '{context_id}', not '{self.context_id}'"
            )

    @staticmethod
    def _require_non_task_entity(frontmatter: EntityFrontmatter) -> None:
        if frontmatter.entity_type == "task":
            raise ContextBoundaryError("Task documents must use the dedicated task APIs")

    def _require_entity_context(self, frontmatter: EntityFrontmatter) -> None:
        try:
            WorkctxUri.parse(frontmatter.uri).require_context(self.context_id)
        except ValueError as exc:
            raise ContextBoundaryError(str(exc)) from exc

    def _validate_artifact_paths(self, manifest: ArtifactManifest) -> None:
        resolve_context_path(
            self._context_root,
            manifest.preserved_path,
            allowed_prefixes=_ARTIFACT_CONTENT_ZONES,
        )
        for sidecar in manifest.sidecars:
            resolve_context_path(
                self._context_root,
                sidecar,
                allowed_prefixes=_ARTIFACT_CONTENT_ZONES,
            )

    def _write_prepared(self, write: StagedWrite, *, lock: ContextLock | None) -> None:
        with self._write_lock(lock) as active_lock:
            atomic_replace_bytes(
                self._context_root,
                write.target,
                write.content,
                nonce=active_lock.nonce,
                lock=active_lock,
            )

    @contextmanager
    def _write_lock(self, lock: ContextLock | None) -> Iterator[ContextLock]:
        if lock is not None:
            if lock.context_root != self._context_root:
                raise ContextBoundaryError("The supplied lock belongs to another context")
            lock.verify_fence()
            yield lock
            return

        acquired = ContextLock.acquire(
            self._context_root,
            session_id=f"canonical-store-{uuid4().hex}",
        )
        try:
            yield acquired
        finally:
            acquired.release()


def _read_regular_bytes(path: Path) -> bytes:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ContextBoundaryError(f"Canonical document must be a regular file: {path.name}")
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as stream:
        return stream.read()
