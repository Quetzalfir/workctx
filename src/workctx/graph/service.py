"""Deterministic, content-minimized Obsidian graph projections."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from workctx.adapters.filesystem import CanonicalStore, ContextLock, ContextZone
from workctx.domain import EntityFrontmatter, Topic, TopicHeat
from workctx.graph.errors import GraphExportError
from workctx.graph.models import GraphExportResult, GraphMap, GraphVaultResult
from workctx.topics import compute_topic_heat
from workctx.topics.registry import iter_topic_documents, load_registry, resolve_active_topic
from workctx.transactions import audit_summary
from workctx.validation.engine import contains_possible_secret

_GRAPH_ROOT = "04_views/graph"
_COLOR_GROUPS: tuple[tuple[str, int], ...] = (
    ("hot", 0xFF0000),
    ("warm", 0xFFA500),
    ("cool", 0x0000FF),
    ("cold", 0x800080),
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class _GraphItem:
    """Small immutable item record kept private to avoid exporting document content."""

    __slots__ = ("entity_id", "entity_type", "relative_path", "title", "uri")

    def __init__(
        self,
        *,
        entity_id: str,
        entity_type: str,
        relative_path: str,
        title: str,
        uri: str,
    ) -> None:
        self.entity_id = entity_id
        self.entity_type = entity_type
        self.relative_path = relative_path
        self.title = title
        self.uri = uri


class GraphExporter:
    """Build and replace both generated graph vaults for one isolated context."""

    def __init__(
        self,
        context_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        store = CanonicalStore(context_root)
        self._store = store
        self._root = store.context_root
        self._context_id = store.context_id
        self._clock = clock

    @property
    def context_root(self) -> Path:
        return self._root

    def export(self, *, topic: str | None = None) -> GraphExportResult:
        """Fully replace both vaults from one deterministic metadata snapshot."""

        session_id = f"graph-export-{secrets.token_hex(8)}"
        with ContextLock.acquire(self._root, session_id=session_id):
            return self._export_locked(topic=topic)

    def _export_locked(self, *, topic: str | None) -> GraphExportResult:
        """Read and replace generated output while holding the context writer lock."""

        snapshot = load_registry(self._root)
        source_revision = audit_summary(self._root).head_hash
        active_topics = {
            item.slug: item
            for item in sorted(snapshot.registry.active_topics(), key=lambda x: x.slug)
        }
        selected_topic = None
        if topic is not None:
            selected_topic = resolve_active_topic(snapshot.registry, topic).slug

        heat = compute_topic_heat(self._root, clock=self._clock)
        heat_by_slug = {item.slug: item for item in heat}
        documents = iter_topic_documents(self._root)
        items_by_uri = {
            record.document.uri: _item_from_document(record.relative_path, record.document)
            for record in documents
        }
        supports: dict[str, set[str]] = {slug: set() for slug in active_topics}
        directly_tagged: dict[str, list[EntityFrontmatter]] = {slug: [] for slug in active_topics}
        for record in documents:
            resolved_slugs: list[str] = []
            for value in record.document.topics:
                resolution = snapshot.registry.resolve(value)
                if resolution is None or resolution.topic.slug not in active_topics:
                    continue
                if resolution.topic.slug not in resolved_slugs:
                    resolved_slugs.append(resolution.topic.slug)
            for slug in resolved_slugs:
                supports[slug].add(record.document.uri)
                directly_tagged[slug].append(record.document)

        for slug, tagged_documents in directly_tagged.items():
            for document in tagged_documents:
                for referenced_uri in _referenced_document_uris(document, items_by_uri):
                    supports[slug].add(referenced_uri)

        included_slugs = _included_topic_slugs(
            tuple(active_topics),
            supports,
            selected_topic=selected_topic,
        )
        included_item_uris = {uri for slug in included_slugs for uri in supports.get(slug, set())}
        _require_metadata_secret_free(
            tuple(
                value
                for slug in included_slugs
                for value in (
                    slug,
                    active_topics[slug].name,
                    active_topics[slug].scope,
                )
            )
            + tuple(
                value
                for uri in sorted(included_item_uris)
                for value in (
                    items_by_uri[uri].title,
                    items_by_uri[uri].entity_type,
                    items_by_uri[uri].uri,
                    items_by_uri[uri].relative_path,
                )
            )
        )
        topic_files = _topic_map_files(
            included_slugs,
            active_topics,
            supports,
            heat_by_slug,
            source_revision,
        )
        reference_files, reference_items = _reference_map_files(
            included_slugs,
            active_topics,
            supports,
            heat_by_slug,
            items_by_uri,
            source_revision,
        )
        _require_secret_free(tuple(topic_files.values()) + tuple(reference_files.values()))

        views_root = self._store.resolve_path("04_views", zones=(ContextZone.VIEWS,))
        try:
            views_root.mkdir(exist_ok=True)
        except OSError as exc:
            raise GraphExportError("The generated views directory could not be created.") from exc
        if not views_root.is_dir():
            raise GraphExportError("The generated views path must be a directory.")
        graph_root = self._store.resolve_path(_GRAPH_ROOT, zones=(ContextZone.VIEWS,))
        _replace_graph_tree(
            views_root,
            graph_root,
            {
                GraphMap.TOPICS.directory_name: topic_files,
                GraphMap.REFERENCES.directory_name: reference_files,
            },
        )

        topic_result = GraphVaultResult(
            map=GraphMap.TOPICS,
            path=GraphMap.TOPICS.relative_path,
            topic_count=len(included_slugs),
            item_count=0,
            file_count=len(topic_files),
        )
        reference_result = GraphVaultResult(
            map=GraphMap.REFERENCES,
            path=GraphMap.REFERENCES.relative_path,
            topic_count=len(included_slugs),
            item_count=len(reference_items),
            file_count=len(reference_files),
        )
        return GraphExportResult(
            context_id=self._context_id,
            source_revision=source_revision,
            selected_topic=selected_topic,
            topics=included_slugs,
            vaults=(topic_result, reference_result),
        )


def export_graph(
    root: Path,
    *,
    topic: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GraphExportResult:
    """Convenience entry point for the explicit graph export command."""

    return GraphExporter(root, clock=clock).export(topic=topic)


def graph_vault_path(root: Path, map_name: GraphMap) -> Path:
    """Resolve one generated vault path inside the context views zone."""

    return CanonicalStore(root).resolve_path(map_name.relative_path, zones=(ContextZone.VIEWS,))


def _item_from_document(relative_path: str, document: EntityFrontmatter) -> _GraphItem:
    return _GraphItem(
        entity_id=document.id,
        entity_type=str(document.entity_type),
        relative_path=relative_path,
        title=document.title,
        uri=document.uri,
    )


def _referenced_document_uris(
    document: EntityFrontmatter,
    items_by_uri: Mapping[str, _GraphItem],
) -> tuple[str, ...]:
    payload = document.model_dump(mode="json")
    references = {
        value for value in _iter_strings(payload) if value != document.uri and value in items_by_uri
    }
    return tuple(sorted(references))


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key in sorted(value, key=str):
            yield from _iter_strings(value[key])
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _iter_strings(item)


def _included_topic_slugs(
    active_slugs: tuple[str, ...],
    supports: Mapping[str, set[str]],
    *,
    selected_topic: str | None,
) -> tuple[str, ...]:
    if selected_topic is None:
        return active_slugs
    selected_support = supports.get(selected_topic, set())
    neighborhood = {
        slug
        for slug in active_slugs
        if slug == selected_topic or bool(selected_support & supports.get(slug, set()))
    }
    return tuple(sorted(neighborhood))


def _topic_map_files(
    slugs: tuple[str, ...],
    topics: Mapping[str, Topic],
    supports: Mapping[str, set[str]],
    heat: Mapping[str, TopicHeat],
    source_revision: str,
) -> dict[str, bytes]:
    files = _vault_configuration_files()
    note_names = _topic_note_names(slugs)
    for slug in slugs:
        shared = [
            (other, len(supports.get(slug, set()) & supports.get(other, set())))
            for other in slugs
            if other != slug
        ]
        shared = [(other, count) for other, count in shared if count > 0]
        files[f"{note_names[slug]}.md"] = _render_topic_note(
            topics[slug],
            heat[slug],
            source_revision=source_revision,
            heading="Connected topics",
            links=tuple(
                f"[[{note_names[other]}|{_wikilink_label(topics[other].name)}]] — "
                f"{count} shared "
                f"{'item' if count == 1 else 'items'}"
                for other, count in shared
            ),
        )
    return files


def _reference_map_files(
    slugs: tuple[str, ...],
    topics: Mapping[str, Topic],
    supports: Mapping[str, set[str]],
    heat: Mapping[str, TopicHeat],
    items_by_uri: Mapping[str, _GraphItem],
    source_revision: str,
) -> tuple[dict[str, bytes], tuple[str, ...]]:
    files = _vault_configuration_files()
    note_names = _topic_note_names(slugs)
    referenced_uris = tuple(sorted({uri for slug in slugs for uri in supports.get(slug, set())}))
    stub_names = {uri: _stub_name(items_by_uri[uri]) for uri in referenced_uris}
    if len(set(stub_names.values())) != len(stub_names):
        raise GraphExportError("Graph item filenames collided; no vaults were replaced.")
    for slug in slugs:
        links = tuple(
            f"[[items/{stub_names[uri]}|{_wikilink_label(items_by_uri[uri].title)}]] — "
            f"{items_by_uri[uri].entity_type}"
            for uri in sorted(
                supports.get(slug, set()),
                key=lambda value: (
                    items_by_uri[value].entity_type,
                    items_by_uri[value].title.casefold(),
                    value,
                ),
            )
        )
        files[f"{note_names[slug]}.md"] = _render_topic_note(
            topics[slug],
            heat[slug],
            source_revision=source_revision,
            heading="Supporting items",
            links=links,
        )
    for uri in referenced_uris:
        item = items_by_uri[uri]
        files[f"items/{stub_names[uri]}.md"] = _render_item_stub(
            item,
            source_revision=source_revision,
        )
    return files, referenced_uris


def _vault_configuration_files() -> dict[str, bytes]:
    graph = {
        "colorGroups": [
            {
                "query": f"tag:#heat/{bucket}",
                "color": {"a": 1, "rgb": rgb},
            }
            for bucket, rgb in _COLOR_GROUPS
        ]
    }
    app = {"alwaysUpdateLinks": False}
    return {
        ".obsidian/app.json": _json_bytes(app),
        ".obsidian/graph.json": _json_bytes(graph),
    }


def _render_topic_note(
    topic: Topic,
    heat: TopicHeat,
    *,
    source_revision: str,
    heading: str,
    links: tuple[str, ...],
) -> bytes:
    last_activity = (
        "null" if heat.last_activity is None else _yaml_string(_timestamp(heat.last_activity))
    )
    lines = [
        "---",
        "generator: workctx graph export",
        f"source_revision: {source_revision}",
        f"tags: [heat/{heat.heat.value}]",
        f"name: {_yaml_string(topic.name)}",
        f"scope: {_yaml_string(topic.scope)}",
        f"heat_score: {heat.score:.6f}",
        f"last_activity: {last_activity}",
        "---",
        f"# {_markdown_text(topic.name)}",
        "",
        f"## {heading}",
        "",
    ]
    lines.extend(f"- {link}" for link in links)
    if not links:
        lines.append("_None._")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _render_item_stub(item: _GraphItem, *, source_revision: str) -> bytes:
    lines = [
        "---",
        "generator: workctx graph export",
        f"source_revision: {source_revision}",
        f"title: {_yaml_string(item.title)}",
        f"type: {_yaml_string(item.entity_type)}",
        f"uri: {_yaml_string(item.uri)}",
        f"path: {_yaml_string(item.relative_path)}",
        "---",
        f"# {_markdown_text(item.title)}",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _stub_name(item: _GraphItem) -> str:
    component = _safe_filename_component(item.entity_id)
    digest = hashlib.sha256(item.uri.encode("utf-8")).hexdigest()[:20]
    return f"{item.entity_type}-{component}-{digest}"


def _topic_note_names(slugs: tuple[str, ...]) -> dict[str, str]:
    names = {
        slug: (
            slug
            if slug.casefold() not in _WINDOWS_RESERVED_NAMES
            else f"topic-{slug}-{hashlib.sha256(slug.encode('utf-8')).hexdigest()[:8]}"
        )
        for slug in slugs
    }
    collisions = {name for name, count in Counter(names.values()).items() if count > 1}
    for slug, name in tuple(names.items()):
        if name in collisions:
            digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
            names[slug] = f"{name}-{digest}"
    if len(set(names.values())) != len(names):  # pragma: no cover - SHA-256 collision guard
        raise GraphExportError("Graph topic filenames collided; no vaults were replaced.")
    return names


def _safe_filename_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in normalized if ord(character) < 128)
    rendered: list[str] = []
    previous_dash = False
    for character in ascii_value.casefold():
        if character.isalnum():
            rendered.append(character)
            previous_dash = False
        elif not previous_dash:
            rendered.append("-")
            previous_dash = True
    component = "".join(rendered).strip("-.")[:48] or "item"
    if component in _WINDOWS_RESERVED_NAMES:
        component = f"item-{component}"
    return component.rstrip(" .") or "item"


def _wikilink_label(value: str) -> str:
    return _single_line(value).replace("[", "(").replace("]", ")").replace("|", "-")


def _markdown_text(value: str) -> str:
    return _single_line(value).replace("\\", "\\\\").replace("#", "\\#")


def _single_line(value: str) -> str:
    return " ".join(value.replace("\x00", "").splitlines()).strip()


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _require_secret_free(contents: tuple[bytes, ...]) -> None:
    for content in contents:
        try:
            rendered = content.decode("utf-8")
        except UnicodeError as exc:  # pragma: no cover - renderer invariant
            raise GraphExportError("Graph export produced invalid UTF-8 metadata.") from exc
        if contains_possible_secret(rendered):
            raise GraphExportError(
                "Graph export metadata matched a possible secret; redact the canonical "
                "metadata before exporting."
            )


def _require_metadata_secret_free(values: tuple[str, ...]) -> None:
    if contains_possible_secret("\n".join(values)) or any(
        contains_possible_secret(value) for value in values
    ):
        raise GraphExportError(
            "Graph export metadata matched a possible secret; redact the canonical "
            "metadata before exporting."
        )


def _replace_graph_tree(
    views_root: Path,
    graph_root: Path,
    vault_files: Mapping[str, Mapping[str, bytes]],
) -> None:
    _require_safe_generated_target(views_root, graph_root)
    staging_container = Path(tempfile.mkdtemp(prefix=".graph-export-", dir=views_root))
    staged_graph = staging_container / "graph"
    previous_graph = staging_container / "previous"
    cleanup_staging = True
    try:
        staged_graph.mkdir()
        for vault_name in sorted(vault_files):
            vault_root = staged_graph / vault_name
            vault_root.mkdir()
            if vault_name == GraphMap.REFERENCES.directory_name:
                (vault_root / "items").mkdir()
            for relative_path, content in sorted(vault_files[vault_name].items()):
                target = _safe_staged_path(vault_root, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        if graph_root.exists():
            graph_root.rename(previous_graph)
        try:
            staged_graph.rename(graph_root)
        except Exception:
            if previous_graph.exists() and not graph_root.exists():
                try:
                    previous_graph.rename(graph_root)
                except Exception:
                    cleanup_staging = False
                    raise
            raise
    except OSError as exc:
        raise GraphExportError("The generated graph vaults could not be replaced safely.") from exc
    finally:
        pending_error = sys.exc_info()[0] is not None
        if cleanup_staging and staging_container.exists():
            try:
                shutil.rmtree(staging_container)
            except OSError as exc:
                if not pending_error:
                    raise GraphExportError(
                        "Graph export succeeded but its temporary backup could not be removed."
                    ) from exc


def _require_safe_generated_target(views_root: Path, graph_root: Path) -> None:
    if _is_link_or_junction(views_root):
        raise GraphExportError("The generated views directory cannot be a link or junction.")
    if os.path.lexists(graph_root):
        if _is_link_or_junction(graph_root) or not graph_root.is_dir():
            raise GraphExportError("The graph export target must be a regular directory.")
        for directory, child_directories, files in os.walk(graph_root, followlinks=False):
            base = Path(directory)
            for name in (*child_directories, *files):
                if _is_link_or_junction(base / name):
                    raise GraphExportError(
                        "The existing graph export contains an unsafe link or junction."
                    )


def _safe_staged_path(root: Path, relative_path: str) -> Path:
    portable = PurePosixPath(relative_path)
    if portable.is_absolute() or any(part in {"", ".", ".."} for part in portable.parts):
        raise GraphExportError("Graph export generated an unsafe relative path.")
    target = root.joinpath(*portable.parts)
    try:
        target.relative_to(root)
    except ValueError as exc:  # pragma: no cover - PurePosixPath guard
        raise GraphExportError("Graph export generated an escaping path.") from exc
    return target


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


__all__ = ["GraphExporter", "export_graph", "graph_vault_path"]
