"""Machine-local advisory state for connector synchronization."""

from __future__ import annotations

import json
import os
import re
import secrets as stdlib_secrets
import stat
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workctx.adapters.filesystem._paths import canonical_context_root, resolve_context_path
from workctx.connectors.models import SyncResult

LAST_SYNC_STATE_PATH = "98_state/connectors/last-sync.json"

_STATE_DIRECTORY = "98_state/connectors"
_STATE_SCHEMA_VERSION = 1
_MAX_STATE_BYTES = 1024 * 1024
_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

LastSyncValues = dict[tuple[str, str], datetime]
_replace = os.replace


def load_last_sync(root: Path) -> LastSyncValues:
    """Load advisory UTC timestamps; every unsafe or corrupt state reads as empty."""

    loaded: Any = None
    try:
        context_root = canonical_context_root(root)
        path = resolve_context_path(
            context_root,
            LAST_SYNC_STATE_PATH,
            allowed_prefixes=("98_state",),
        )
        if not path.exists():
            return {}
        if path.is_symlink() or _is_junction(path):
            return {}
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_STATE_BYTES:
            return {}
        content = path.read_bytes()
        if len(content) > _MAX_STATE_BYTES:
            return {}
        loaded = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
        return _parse_state(loaded)
    except Exception:
        return {}
    finally:
        loaded = None


def record_last_sync(root: Path, result: SyncResult) -> bool:
    """Best-effort atomic update after a successful connector synchronization."""

    if not result.snapshots:
        return True
    values = load_last_sync(root)
    for snapshot in result.snapshots:
        values[(result.connector_name, snapshot.snapshot_id)] = snapshot.retrieved_at.astimezone(
            UTC
        )
    return _save_last_sync(root, values)


def _parse_state(value: object) -> LastSyncValues:
    if not isinstance(value, dict) or set(value) != {"schema_version", "connectors"}:
        raise ValueError("invalid last-sync state")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid last-sync schema version")
    connectors = value["connectors"]
    if not isinstance(connectors, dict):
        raise ValueError("invalid last-sync connectors")

    parsed: LastSyncValues = {}
    for connector_name, raw_snapshots in connectors.items():
        if not _valid_name(connector_name) or not isinstance(raw_snapshots, dict):
            raise ValueError("invalid last-sync connector")
        for snapshot_id, raw_timestamp in raw_snapshots.items():
            if not _valid_name(snapshot_id) or not isinstance(raw_timestamp, str):
                raise ValueError("invalid last-sync snapshot")
            timestamp = _parse_timestamp(raw_timestamp)
            if timestamp is None:
                raise ValueError("invalid last-sync timestamp")
            parsed[(connector_name, snapshot_id)] = timestamp
    return parsed


def _save_last_sync(root: Path, values: LastSyncValues) -> bool:
    temp: Path | None = None
    try:
        context_root = canonical_context_root(root)
        state_directory = resolve_context_path(
            context_root,
            _STATE_DIRECTORY,
            allowed_prefixes=("98_state",),
        )
        state_parent = state_directory.parent
        if not state_parent.is_dir() or state_parent.is_symlink() or _is_junction(state_parent):
            return False
        state_directory.mkdir(exist_ok=True)
        if (
            not state_directory.is_dir()
            or state_directory.is_symlink()
            or _is_junction(state_directory)
        ):
            return False
        path = resolve_context_path(
            context_root,
            LAST_SYNC_STATE_PATH,
            allowed_prefixes=("98_state",),
        )
        payload = _serialize_state(values)
        temp = path.with_name(f".{path.name}.{stdlib_secrets.token_hex(8)}.tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _replace(temp, path)
        _fsync_directory(path.parent)
        return True
    except Exception:
        return False
    finally:
        if temp is not None:
            with suppress(OSError):
                temp.unlink()


def _serialize_state(values: LastSyncValues) -> bytes:
    connectors: dict[str, dict[str, str]] = {}
    for (connector_name, snapshot_id), timestamp in sorted(values.items()):
        connectors.setdefault(connector_name, {})[snapshot_id] = _format_timestamp(timestamp)
    return (
        json.dumps(
            {"schema_version": _STATE_SCHEMA_VERSION, "connectors": connectors},
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate last-sync key")
        result[key] = value
    return result


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _valid_name(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) <= 64 and _NAME_PATTERN.fullmatch(value) is not None
    )


def _is_junction(path: Path) -> bool:
    return hasattr(path, "is_junction") and path.is_junction()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


__all__ = ["LAST_SYNC_STATE_PATH", "LastSyncValues", "load_last_sync", "record_last_sync"]
