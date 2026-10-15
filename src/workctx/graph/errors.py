"""Typed failures for deterministic graph exports."""

from workctx.errors import UserCorrectableError


class GraphExportError(UserCorrectableError):
    """Raised when a generated graph vault cannot be built safely."""


__all__ = ["GraphExportError"]
