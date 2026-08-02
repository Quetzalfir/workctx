"""Derived project-local paths for adapter ownership and transactions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workctx.adapters.filesystem.serialization import load_yaml_model
from workctx.models.context import ContextConfig

from ._safe_fs import SafeFilesystemError, SafeRoot
from .errors import InvalidAdapterStateError
from .models import AgentClient


@dataclass(frozen=True, slots=True)
class InstallationLayout:
    """All internal locations for one selected project and client."""

    root: Path
    client: AgentClient
    is_context: bool
    manifest_path: str
    lock_path: str
    staging_path: str
    backup_root: str = ".workctx/backups"


def _is_valid_context(root: Path) -> bool:
    safe = SafeRoot(root)
    try:
        snapshot = safe.inspect_file("context.yaml")
    except SafeFilesystemError as error:
        raise InvalidAdapterStateError("Unsafe context.yaml marker") from error
    if not snapshot.exists:
        return False
    if snapshot.content is None:
        raise InvalidAdapterStateError("context.yaml content is unavailable")
    try:
        load_yaml_model(snapshot.content, ContextConfig)
    except ValueError as error:
        raise InvalidAdapterStateError(
            "context.yaml is not a valid context configuration"
        ) from error
    return True


def derive_layout(root: Path, client: AgentClient) -> InstallationLayout:
    """Resolve the root once and select context precedence over repository layout."""

    physical_root = root.resolve(strict=True)
    if not physical_root.is_dir():
        raise InvalidAdapterStateError("Installation root must be a directory")
    is_context = _is_valid_context(physical_root)
    if is_context:
        base = "98_state/agent-adapters"
        lock = "98_state/lock.json"
        staging = "98_state/staging/agent-adapters"
    else:
        base = ".workctx/agent-adapters"
        lock = ".workctx/agent-adapters/lock.json"
        staging = ".workctx/agent-adapters/staging"
    return InstallationLayout(
        root=physical_root,
        client=client,
        is_context=is_context,
        manifest_path=f"{base}/{client.value}/skill-manifest.json",
        lock_path=lock,
        staging_path=staging,
    )
