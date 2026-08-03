"""Expected failures raised by operational-view generation."""

from __future__ import annotations

from workctx.errors import ConflictError, WorkctxError


class ViewError(WorkctxError):
    """Base class for generated-view failures."""


class ViewSourceChangedError(ConflictError, ViewError):
    """Raised when canonical state advances during a read-only brief build."""

    def __init__(self) -> None:
        super().__init__("Canonical state changed while the operational brief was built.")


__all__ = ["ViewError", "ViewSourceChangedError"]
