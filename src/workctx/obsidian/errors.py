"""Typed failures for optional Obsidian helpers."""

from workctx.errors import UserCorrectableError


class ObsidianError(UserCorrectableError):
    """Raised when an Obsidian operation fails after its prerequisites exist."""


__all__ = ["ObsidianError"]
