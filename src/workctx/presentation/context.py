from __future__ import annotations

from pathlib import Path

from workctx.errors import ContextNotFoundError
from workctx.services.contexts import resolve_context_root

_NOT_FOUND_MESSAGE = (
    "No context could be resolved. Pass --context PATH or run the command from inside "
    "a directory containing context.yaml."
)


def resolve_cli_context(
    *,
    explicit_path: Path | None,
    positional_path: Path | None = None,
    discovery_start: Path | None = None,
) -> Path:
    """Resolve option, positional path, or cwd without crossing context boundaries."""

    candidate = explicit_path or positional_path
    if candidate is not None:
        try:
            return resolve_context_root(candidate)
        except ContextNotFoundError as exc:
            raise ContextNotFoundError(_NOT_FOUND_MESSAGE) from exc

    try:
        return resolve_context_root(discovery_start or Path.cwd())
    except ContextNotFoundError as exc:
        active = _active_registry_context()
        if active is not None:
            return active
        raise ContextNotFoundError(_NOT_FOUND_MESSAGE) from exc


def _active_registry_context() -> Path | None:
    """Doc-04 resolution step 3: the active context from the user-level registry."""

    from workctx.adapters.filesystem.registry import ContextRegistry, RegistryError

    try:
        active = ContextRegistry().get_active()
        if active is None:
            return None
        return resolve_context_root(active)
    except (RegistryError, ContextNotFoundError):
        return None
