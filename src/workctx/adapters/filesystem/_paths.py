"""Context-boundary path helpers shared by filesystem adapters."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath

from workctx.errors import ContextBoundaryError


class ContextZone(StrEnum):
    """Stable top-level zones in a Work Context workspace."""

    INBOX = "00_inbox"
    PROCESSED = "01_processed"
    KNOWLEDGE = "02_knowledge"
    WORK = "03_work"
    VIEWS = "04_views"
    OUTBOX = "05_outbox"
    INTEGRATIONS = "90_integrations"
    STATE = "98_state"
    META = "99_meta"


def canonical_context_root(root: Path) -> Path:
    """Resolve an existing context boundary to its canonical filesystem path."""

    try:
        resolved = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ContextBoundaryError(f"Unable to resolve context root {root}: {exc}") from exc
    if not resolved.is_dir():
        raise ContextBoundaryError(f"Context root is not a directory: {root}")
    return resolved


def resolve_context_path(
    root: Path,
    relative_path: str | Path,
    *,
    allowed_prefixes: tuple[str, ...] = (),
    allowed_root_files: tuple[str, ...] = (),
) -> Path:
    """Resolve and validate a relative path without crossing the context boundary.

    The returned path retains its lexical leaf so an atomic replace replaces a
    symlink leaf rather than unexpectedly following it. Containment is checked
    against the fully resolved path, including existing symlink and junction
    components.
    """

    canonical_root = canonical_context_root(root)
    portable = _portable_relative_path(relative_path)
    _require_allowed_location(portable, allowed_prefixes, allowed_root_files)

    candidate = canonical_root.joinpath(*portable.parts)
    try:
        resolved_candidate = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ContextBoundaryError(f"Unable to resolve context path {portable}: {exc}") from exc
    if not _is_within(resolved_candidate, canonical_root):
        raise ContextBoundaryError(f"Context path escapes the isolated root: {portable}")
    _require_resolved_allowed_location(
        canonical_root,
        portable,
        resolved_candidate,
        allowed_prefixes,
        allowed_root_files,
    )

    _reject_nested_context(candidate, canonical_root)
    return candidate


def relative_context_path(root: Path, path: Path) -> str:
    """Return a validated context-relative path with forward slashes."""

    canonical_root = canonical_context_root(root)
    try:
        relative = path.absolute().relative_to(canonical_root)
    except ValueError as exc:
        raise ContextBoundaryError(f"Path is outside the context root: {path}") from exc
    validated = resolve_context_path(canonical_root, relative)
    return validated.relative_to(canonical_root).as_posix()


def _portable_relative_path(path: str | Path) -> PurePosixPath:
    raw = os.fspath(path)
    if not raw or "\x00" in raw:
        raise ContextBoundaryError("Context-relative path must not be empty")
    windows_path = PureWindowsPath(raw)
    if windows_path.drive or windows_path.root or PurePosixPath(raw).is_absolute():
        raise ContextBoundaryError(f"Absolute paths are not allowed inside a context: {raw}")

    normalized = raw.replace("\\", "/")
    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ContextBoundaryError(f"Path traversal is not allowed inside a context: {raw}")
    portable = PurePosixPath(normalized)
    return portable


def _require_allowed_location(
    path: PurePosixPath,
    prefixes: tuple[str, ...],
    root_files: tuple[str, ...],
) -> None:
    if not prefixes and not root_files:
        return
    text = path.as_posix()
    if text in root_files:
        return
    for prefix in prefixes:
        normalized = PurePosixPath(prefix).as_posix().rstrip("/")
        if text == normalized or text.startswith(f"{normalized}/"):
            return
    raise ContextBoundaryError(f"Path is outside the allowed context zone: {text}")


def _require_resolved_allowed_location(
    root: Path,
    portable: PurePosixPath,
    resolved_candidate: Path,
    prefixes: tuple[str, ...],
    root_files: tuple[str, ...],
) -> None:
    if not prefixes and not root_files:
        return
    text = portable.as_posix()
    if text in root_files:
        lexical_file = root.joinpath(*portable.parts)
        if _same_path(resolved_candidate, lexical_file):
            return
        raise ContextBoundaryError(f"Root document resolves through an unsafe link: {text}")

    for prefix in prefixes:
        normalized = PurePosixPath(prefix).as_posix().rstrip("/")
        if text != normalized and not text.startswith(f"{normalized}/"):
            continue
        lexical_base = root.joinpath(*PurePosixPath(normalized).parts)
        resolved_base = lexical_base.resolve(strict=False)
        if not _same_path(resolved_base, lexical_base):
            raise ContextBoundaryError(
                f"Allowed context zone resolves through an unsafe link: {normalized}"
            )
        if _is_within(resolved_candidate, resolved_base):
            return
    raise ContextBoundaryError(f"Context path resolves outside its allowed zone: {text}")


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.path.normcase(candidate), os.path.normcase(root)))
    except ValueError:
        return False
    return common == os.path.normcase(root)


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(first) == os.path.normcase(second)


def _reject_nested_context(candidate: Path, root: Path) -> None:
    current = candidate if candidate.is_dir() else candidate.parent
    while _is_within(current.resolve(strict=False), root) and current != root:
        if (current / "context.yaml").is_file():
            relative = current.relative_to(root).as_posix()
            raise ContextBoundaryError(f"Path crosses nested context boundary: {relative}")
        current = current.parent
