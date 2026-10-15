"""Structured results for Obsidian installation and vault opening."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from workctx.graph import GraphMap


class _ObsidianResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObsidianInstallResult(_ObsidianResultModel):
    """Outcome or preview of an optional package-manager installation."""

    schema_version: Literal[1] = 1
    platform: Literal["windows", "macos", "linux"]
    installed: bool
    manager: str | None
    command: tuple[str, ...] | None
    command_text: str | None
    executed: bool


class ObsidianOpenResult(_ObsidianResultModel):
    """Outcome of opening one generated vault through the Obsidian URI scheme."""

    schema_version: Literal[1] = 1
    map: GraphMap
    vault_path: str
    uri: str
    exported: bool


__all__ = ["ObsidianInstallResult", "ObsidianOpenResult"]
