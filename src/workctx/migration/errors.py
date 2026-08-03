"""Expected failures raised by the legacy migration engine."""

from __future__ import annotations

from workctx.errors import ConflictError, ContextBoundaryError, UserCorrectableError
from workctx.migration.models import MigrationReport


class MigrationError(UserCorrectableError):
    """Base class for a migration request the operator can correct."""


class MigrationBlockedError(MigrationError):
    """Raised when apply is refused after producing a sanitized report."""

    def __init__(self, report: MigrationReport) -> None:
        super().__init__("Migration apply is blocked by report findings.")
        self.report = report


class MigrationValidationError(MigrationError):
    """Raised when the staged destination fails canonical validation."""

    def __init__(self, report: MigrationReport) -> None:
        super().__init__("The staged migration context did not validate cleanly.")
        self.report = report


class MigrationBoundaryError(ContextBoundaryError):
    """Raised when source or target boundaries cannot be made safe."""


class MigrationSourceChangedError(ConflictError):
    """Raised when the source tree fingerprint changes during migration."""


__all__ = [
    "MigrationBlockedError",
    "MigrationBoundaryError",
    "MigrationError",
    "MigrationSourceChangedError",
    "MigrationValidationError",
]
