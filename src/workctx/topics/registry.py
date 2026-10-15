"""Safe read-only access to the canonical topic registry and tagged documents."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from workctx.adapters.filesystem import (
    CanonicalStore,
    ContextZone,
    load_yaml_model,
)
from workctx.domain import EntityFrontmatter, Task, Topic, TopicRegistry, TopicStatus
from workctx.domain.frontmatter import parse_frontmatter
from workctx.domain.topics import TOPICS_REGISTRY_PATH
from workctx.topics.errors import TopicNotFoundError, TopicRegistryError


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    registry: TopicRegistry
    exists: bool
    content_hash: str | None


@dataclass(frozen=True, slots=True)
class TopicDocument:
    relative_path: str
    document: EntityFrontmatter | Task
    body: str
    content_hash: str


def load_registry(root: Path) -> RegistrySnapshot:
    """Load the optional registry without following links or leaving the context."""

    store = CanonicalStore(root)
    path = store.resolve_path(TOPICS_REGISTRY_PATH, zones=(ContextZone.META,))
    if not os.path.lexists(path):
        return RegistrySnapshot(
            registry=TopicRegistry(schema_version=1, topics=[]),
            exists=False,
            content_hash=None,
        )
    if _is_link_or_junction(path) or not path.is_file():
        raise TopicRegistryError("The topic registry must be a regular context-local file.")
    try:
        content = path.read_bytes()
        registry = load_yaml_model(content, TopicRegistry)
    except (OSError, UnicodeError, ValueError, ValidationError) as exc:
        raise TopicRegistryError("The canonical topic registry is invalid.") from exc
    return RegistrySnapshot(
        registry=registry,
        exists=True,
        content_hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )


def resolve_active_topic(registry: TopicRegistry, value: str) -> Topic:
    """Resolve a slug or alias to its active canonical topic."""

    resolution = registry.resolve(value)
    if resolution is None or resolution.topic.status is not TopicStatus.ACTIVE:
        raise TopicNotFoundError(
            "Topic did not resolve to an active slug or alias; run `workctx topic list`."
        )
    return resolution.topic


def require_active_slug(registry: TopicRegistry, slug: str) -> Topic:
    """Require an exact active canonical slug for a mutation target."""

    topic = registry.get(slug)
    if topic is None or topic.status is not TopicStatus.ACTIVE:
        raise TopicNotFoundError(f"Active topic slug {slug!r} was not found.")
    return topic


def iter_topic_documents(root: Path) -> tuple[TopicDocument, ...]:
    """Read validated topic-capable Markdown documents in deterministic path order."""

    store = CanonicalStore(root)
    zones = (
        (ContextZone.KNOWLEDGE, "02_knowledge"),
        (ContextZone.WORK, "03_work"),
        (ContextZone.OUTBOX, "05_outbox"),
    )
    records: list[TopicDocument] = []
    for zone, relative_zone in zones:
        zone_path = store.resolve_path(relative_zone, zones=(zone,))
        candidates: list[Path] = []
        pending = [zone_path]
        while pending:
            directory = pending.pop()
            try:
                children = tuple(directory.iterdir())
            except OSError as exc:
                raise TopicRegistryError(
                    "A topic-capable canonical directory could not be read."
                ) from exc
            for child in children:
                if _is_link_or_junction(child):
                    raise TopicRegistryError("A topic-capable canonical path is an unsafe link.")
                if child.is_dir():
                    pending.append(child)
                elif child.is_file() and child.suffix.casefold() == ".md":
                    candidates.append(child)
        for path in sorted(candidates, key=lambda item: item.as_posix().casefold()):
            if path.name.casefold() == "readme.md":
                continue
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise TopicRegistryError(
                    "A topic-capable canonical document could not be read."
                ) from exc
            if not content.startswith((b"---\n", b"---\r\n")):
                continue
            try:
                raw, _body = parse_frontmatter(content.decode("utf-8"))
            except (UnicodeError, ValueError, yaml.YAMLError) as exc:
                raise TopicRegistryError(
                    "A topic-capable canonical document could not be parsed."
                ) from exc
            entity_type = raw.get("entity_type")
            if entity_type is None:
                continue
            relative = path.relative_to(store.context_root).as_posix()
            try:
                loaded = (
                    store.read_task(relative)
                    if entity_type == "task"
                    else store.read_entity(relative)
                )
            except (OSError, ValueError, ValidationError) as exc:
                raise TopicRegistryError("A topic-capable canonical document is invalid.") from exc
            records.append(
                TopicDocument(
                    relative_path=relative,
                    document=loaded.frontmatter,
                    body=loaded.body,
                    content_hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
                )
            )
    return tuple(records)


def documents_by_topic(
    root: Path,
    registry: TopicRegistry,
) -> dict[str, frozenset[str]]:
    """Return canonical document paths grouped by resolved active topic."""

    grouped: dict[str, set[str]] = {topic.slug: set() for topic in registry.active_topics()}
    for record in iter_topic_documents(root):
        for value in record.document.topics:
            resolution = registry.resolve(value)
            if resolution is not None:
                grouped.setdefault(resolution.topic.slug, set()).add(record.relative_path)
    return {slug: frozenset(paths) for slug, paths in grouped.items()}


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


__all__ = [
    "RegistrySnapshot",
    "TopicDocument",
    "documents_by_topic",
    "iter_topic_documents",
    "load_registry",
    "require_active_slug",
    "resolve_active_topic",
]
