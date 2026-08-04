"""Best-effort append-only recording for opt-in machine-local usage state."""

from __future__ import annotations

import hashlib
import json
import threading
import warnings
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from workctx.domain import validate_durable_reference
from workctx.services.contexts import load_context_config
from workctx.usage.models import UsageEvent

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_ROTATED_FILES = 2
USAGE_RELATIVE_PATH = Path("98_state/usage/usage.jsonl")

_WRITE_LOCK = threading.Lock()
_WARNING_LOCK = threading.Lock()
_WARNED_ROOTS: set[str] = set()


def is_enabled(root: Path) -> bool:
    """Return the typed per-context opt-in flag without creating state."""

    try:
        return load_context_config(root).telemetry.usage
    except Exception:
        _warn_once(root)
        return False


def record(
    root: Path,
    api: str,
    target: str,
    *,
    now: datetime | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep: int = DEFAULT_ROTATED_FILES,
) -> None:
    """Append one event when enabled; every failure is reduced to one warning.

    This function is deliberately a ``None``-returning best-effort seam. Callers
    must never branch on recording success, and no recording failure may cross a
    read API boundary.
    """

    if not is_enabled(root):
        return
    try:
        timestamp = _normalize_time(datetime.now(UTC) if now is None else now)
        event = _event(api, target, timestamp)
        payload = (
            json.dumps(
                event.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if type(keep) is not int or keep < 0:
            raise ValueError("keep must be a non-negative integer")
        _append(root, payload, max_bytes=max_bytes, keep=keep)
    except Exception:
        _warn_once(root)


def _event(api: str, target: str, timestamp: datetime) -> UsageEvent:
    if not isinstance(api, str) or not isinstance(target, str):
        raise TypeError("usage api and target must be strings")
    target_uri = None if _must_hash(api, target) else _safe_uri(target)
    if target_uri is None:
        return UsageEvent(
            timestamp=timestamp,
            api=api,
            query_sha256=hashlib.sha256(target.encode("utf-8")).hexdigest(),
        )
    return UsageEvent(timestamp=timestamp, api=api, target_uri=target_uri)


def _must_hash(api: str, target: str) -> bool:
    del target
    return api.casefold().rsplit(".", maxsplit=1)[-1] == "search"


def _safe_uri(target: str) -> str | None:
    if len(target) > 4096:
        return None
    try:
        canonical = validate_durable_reference(target)
    except ValueError:
        return None
    parsed = urlsplit(canonical)
    if parsed.scheme not in {"artifact", "repo", "workctx"} and (
        parsed.username is not None or parsed.password is not None or parsed.query
    ):
        return None
    return canonical


def _append(root: Path, payload: bytes, *, max_bytes: int, keep: int) -> None:
    resolved_root = root.expanduser().resolve(strict=True)
    state = resolved_root / "98_state"
    if _is_link(state) or not state.is_dir():
        raise OSError("usage state parent is unsafe")
    directory = state / "usage"
    with _WRITE_LOCK:
        if directory.exists() and (_is_link(directory) or not directory.is_dir()):
            raise OSError("usage directory is unsafe")
        directory.mkdir(exist_ok=True)
        if _is_link(directory) or not directory.is_dir():
            raise OSError("usage directory is unsafe")
        path = resolved_root / USAGE_RELATIVE_PATH
        if path.exists() and (_is_link(path) or not path.is_file()):
            raise OSError("usage file is unsafe")
        current_size = path.stat().st_size if path.exists() else 0
        if current_size and current_size + len(payload) > max_bytes:
            _rotate(path, keep=keep)
        with path.open("ab") as stream:
            stream.write(payload)


def _rotate(path: Path, *, keep: int) -> None:
    if keep == 0:
        path.unlink(missing_ok=True)
        return
    oldest = _rotated_path(path, keep)
    if oldest.exists():
        if _is_link(oldest) or not oldest.is_file():
            raise OSError("rotated usage file is unsafe")
        oldest.unlink()
    for index in range(keep - 1, 0, -1):
        source = _rotated_path(path, index)
        if not source.exists():
            continue
        if _is_link(source) or not source.is_file():
            raise OSError("rotated usage file is unsafe")
        source.replace(_rotated_path(path, index + 1))
    path.replace(_rotated_path(path, 1))


def _rotated_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("usage clocks must be timezone-aware")
    return value.astimezone(UTC)


def _warn_once(root: Path) -> None:
    try:
        key = str(root.expanduser().resolve(strict=False))
    except Exception:
        key = "<unresolved-context>"
    with _WARNING_LOCK:
        if key in _WARNED_ROOTS:
            return
        _WARNED_ROOTS.add(key)
    with suppress(Exception):
        warnings.warn(
            "Usage telemetry could not be recorded; the read operation continued.",
            RuntimeWarning,
            stacklevel=3,
        )


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_ROTATED_FILES",
    "USAGE_RELATIVE_PATH",
    "is_enabled",
    "record",
]
