"""Typed user-correctable failures for checkpoint operations."""

from workctx.errors import UserCorrectableError


class CheckpointError(UserCorrectableError):
    """Base error for deterministic checkpoint operations."""


class CheckpointNotFoundError(CheckpointError):
    """Raised when a checkpoint ID does not resolve."""


class CheckpointValidationError(CheckpointError):
    """Raised when checkpoint input or canonical content is invalid."""


__all__ = ["CheckpointError", "CheckpointNotFoundError", "CheckpointValidationError"]
