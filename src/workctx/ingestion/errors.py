"""Expected, content-safe failures raised by the ingestion lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workctx.errors import ConflictError, UserCorrectableError, WorkctxError

if TYPE_CHECKING:
    from workctx.adapters.filesystem import RecoveryInspection
    from workctx.transactions import ApplyResult


class IngestionError(WorkctxError):
    """Base class for deterministic ingestion failures."""


class ArtifactNotFoundError(UserCorrectableError, IngestionError):
    """Raised when a requested artifact or manifest cannot be found safely."""

    def __init__(self, message: str = "The requested artifact was not found.") -> None:
        super().__init__(message)


class ArtifactReadError(UserCorrectableError, IngestionError):
    """Raised when an inbox file cannot be read as a stable regular file."""

    def __init__(self) -> None:
        super().__init__("An inbox artifact could not be read as a stable regular file.")


class ArtifactStateError(ConflictError, IngestionError):
    """Raised when manifest and preserved-file state disagree."""

    def __init__(self, message: str = "Artifact lifecycle state is inconsistent.") -> None:
        super().__init__(message)


class DuplicateArtifactError(ConflictError, IngestionError):
    """Raised when duplicate policy refuses a second copy."""

    def __init__(self, *, duplicate_of: str) -> None:
        self.duplicate_of = duplicate_of
        super().__init__(f"Artifact content is already registered as {duplicate_of}.")


class ArtifactReceiptError(UserCorrectableError, IngestionError):
    """Raised when an authenticated event does not authorize this artifact."""

    def __init__(self) -> None:
        super().__init__("The committed transaction does not reference this artifact.")


class IngestionRecoveryPendingError(ConflictError, IngestionError):
    """Raised when a staged evidence move remains recoverable and must be retried."""

    def __init__(
        self,
        *,
        inspection: RecoveryInspection,
        receipt: ApplyResult,
    ) -> None:
        self.inspection = inspection
        self.receipt = receipt
        super().__init__(
            "An evidence move remains recoverable; retry the same lifecycle operation."
        )


__all__ = [
    "ArtifactNotFoundError",
    "ArtifactReadError",
    "ArtifactReceiptError",
    "ArtifactStateError",
    "DuplicateArtifactError",
    "IngestionError",
    "IngestionRecoveryPendingError",
]
