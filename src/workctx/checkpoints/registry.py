"""Safe loading and listing of canonical checkpoint files."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import ValidationError

from workctx.adapters.filesystem import CanonicalStore, ContextZone, load_yaml_model
from workctx.checkpoints.errors import CheckpointNotFoundError, CheckpointValidationError
from workctx.domain import CHECKPOINT_ID_PATTERN, Checkpoint

_CHECKPOINT_ID = re.compile(CHECKPOINT_ID_PATTERN)


def load_checkpoint(root: Path, checkpoint_id: str) -> Checkpoint:
    """Load one canonical checkpoint without following links."""

    if _CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
        raise CheckpointNotFoundError("Checkpoint ID is invalid or was not found.")
    store = CanonicalStore(root)
    relative = f"99_meta/{checkpoint_id}.yaml"
    path = store.resolve_path(relative, zones=(ContextZone.META,))
    if not os.path.lexists(path) or _is_link_or_junction(path) or not path.is_file():
        raise CheckpointNotFoundError("Checkpoint ID is invalid or was not found.")
    try:
        checkpoint = load_yaml_model(path.read_bytes(), Checkpoint)
    except (OSError, UnicodeError, ValueError, ValidationError) as exc:
        raise CheckpointValidationError("The canonical checkpoint is invalid.") from exc
    if checkpoint.id != checkpoint_id or checkpoint.relative_path != relative:
        raise CheckpointValidationError("The checkpoint filename does not match its identity.")
    _require_local_items(checkpoint, store.context_id)
    return checkpoint


def list_checkpoints(root: Path) -> tuple[Checkpoint, ...]:
    """Return every canonical checkpoint in newest-first deterministic order."""

    store = CanonicalStore(root)
    meta = store.resolve_path("99_meta", zones=(ContextZone.META,))
    checkpoints: list[Checkpoint] = []
    try:
        candidates = tuple(meta.iterdir())
    except OSError as exc:
        raise CheckpointValidationError(
            "Canonical checkpoint metadata could not be listed."
        ) from exc
    for path in sorted(candidates, key=lambda item: (item.name.casefold(), item.name)):
        if path.suffix.casefold() != ".yaml":
            continue
        checkpoint_id = path.stem
        if _CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
            continue
        checkpoints.append(load_checkpoint(store.context_root, checkpoint_id))
    return tuple(
        sorted(
            checkpoints,
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )
    )


def _require_local_items(checkpoint: Checkpoint, context_id: str) -> None:
    try:
        for item in checkpoint.items:
            from workctx.domain import WorkctxUri

            WorkctxUri.parse(item.uri).require_context(context_id)
    except ValueError as exc:
        raise CheckpointValidationError(
            "The checkpoint contains an item outside the active context."
        ) from exc


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


__all__ = ["list_checkpoints", "load_checkpoint"]
