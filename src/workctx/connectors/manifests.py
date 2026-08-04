"""Strict loading for operator-authored connector manifests."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from workctx.adapters.filesystem._paths import canonical_context_root, resolve_context_path
from workctx.connectors.errors import ConnectorManifestError, DuplicateConnectorNameError
from workctx.connectors.models import ConnectorManifest

CONNECTOR_DIRECTORY = "07_connectors"
MAX_MANIFEST_BYTES = 1024 * 1024


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ValueError("YAML mapping keys must be hashable") from error
        if duplicate:
            raise ValueError("YAML mappings must not contain duplicate keys")
        try:
            mapping[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as error:
            raise ValueError("YAML mapping keys must be hashable") from error
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


class _LoadFailureKind(StrEnum):
    INVALID = "invalid"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class _LoadFailure:
    kind: _LoadFailureKind
    path: str = CONNECTOR_DIRECTORY
    connector_name: str | None = None


@dataclass(frozen=True, slots=True)
class _LoadedManifest:
    path: Path
    relative_path: str
    manifest: ConnectorManifest


def load_manifests(root: Path) -> tuple[ConnectorManifest, ...]:
    """Load all strict ``07_connectors/*.yaml`` manifests in stable name order."""

    manifests, failure = _load_manifests(root)
    if failure is not None:
        _raise_load_failure(failure)
    return manifests


def _load_manifests(
    root: Path,
) -> tuple[tuple[ConnectorManifest, ...], _LoadFailure | None]:
    try:
        context_root = canonical_context_root(root)
        connector_dir = resolve_context_path(
            context_root,
            CONNECTOR_DIRECTORY,
            allowed_prefixes=(CONNECTOR_DIRECTORY,),
        )
    except Exception:
        return (), _LoadFailure(_LoadFailureKind.INVALID)

    if not connector_dir.exists():
        return (), None
    if not connector_dir.is_dir() or connector_dir.is_symlink() or _is_junction(connector_dir):
        return (), _LoadFailure(_LoadFailureKind.INVALID)

    try:
        candidates = tuple(
            path
            for path in sorted(connector_dir.iterdir(), key=lambda item: item.name)
            if path.suffix == ".yaml"
        )
    except OSError:
        return (), _LoadFailure(_LoadFailureKind.INVALID)

    loaded: list[_LoadedManifest] = []
    for candidate in candidates:
        relative_path = f"{CONNECTOR_DIRECTORY}/{candidate.name}"
        manifest = _load_one(context_root, candidate, relative_path)
        if manifest is None:
            return (), _LoadFailure(_LoadFailureKind.INVALID, relative_path)
        loaded.append(_LoadedManifest(candidate, relative_path, manifest))

    names: set[str] = set()
    for item in loaded:
        if item.manifest.name in names:
            return (), _LoadFailure(
                _LoadFailureKind.DUPLICATE,
                connector_name=item.manifest.name,
            )
        names.add(item.manifest.name)

    for item in loaded:
        if item.path.stem != item.manifest.name:
            return (), _LoadFailure(_LoadFailureKind.INVALID, item.relative_path)

    return tuple(sorted((item.manifest for item in loaded), key=lambda item: item.name)), None


def _load_one(root: Path, path: Path, relative_path: str) -> ConnectorManifest | None:
    content: bytes | None = None
    loaded: Any = None
    try:
        resolved = resolve_context_path(
            root,
            relative_path,
            allowed_prefixes=(CONNECTOR_DIRECTORY,),
        )
        if resolved != path or path.is_symlink() or _is_junction(path):
            return None
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_MANIFEST_BYTES:
            return None
        content = path.read_bytes()
        after = path.stat()
        if not _stable_file(before, after) or len(content) > MAX_MANIFEST_BYTES:
            return None
        loaded = yaml.load(content.decode("utf-8"), Loader=_UniqueKeyLoader)
        if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
            return None
        return ConnectorManifest.model_validate(loaded)
    except (
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
        yaml.YAMLError,
        ValidationError,
    ):
        return None
    finally:
        content = None
        loaded = None


def _stable_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )


def _is_junction(path: Path) -> bool:
    return hasattr(path, "is_junction") and path.is_junction()


def _raise_load_failure(failure: _LoadFailure) -> None:
    if failure.kind is _LoadFailureKind.DUPLICATE and failure.connector_name is not None:
        raise DuplicateConnectorNameError(failure.connector_name)
    raise ConnectorManifestError(failure.path)


__all__ = ["CONNECTOR_DIRECTORY", "load_manifests"]
