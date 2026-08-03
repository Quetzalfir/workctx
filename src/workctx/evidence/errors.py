"""Content-safe failures for the deterministic evidence-processing workflow."""

from __future__ import annotations

from workctx.errors import ConflictError, ContextBoundaryError, UserCorrectableError, WorkctxError


class EvidenceWorkflowError(WorkctxError):
    """Base class for expected evidence workflow failures."""


class EvidenceInputError(UserCorrectableError, EvidenceWorkflowError):
    """Raised when an agent-authored staging payload is invalid."""

    def __init__(self, code: str, message: str, *, path: str | None = None) -> None:
        self.code = code
        self.path = path
        super().__init__(message)


class EvidenceContextError(ContextBoundaryError, EvidenceWorkflowError):
    """Raised when a proposed reference crosses the active context boundary."""

    code = "EVIDENCE-CONTEXT-MISMATCH"

    def __init__(self, *, path: str | None = None) -> None:
        self.path = path
        super().__init__("An evidence reference belongs to another context.")


class EvidenceStateError(ConflictError, EvidenceWorkflowError):
    """Raised when an artifact or canonical document is not processable."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EvidenceArtifactNotFoundError(EvidenceInputError):
    """Raised when a requested artifact manifest does not exist."""

    def __init__(self) -> None:
        super().__init__(
            "EVIDENCE-ARTIFACT-NOT-FOUND",
            "The requested evidence artifact was not found.",
        )


class EvidenceArtifactQuarantinedError(EvidenceStateError):
    """Raised when processing is attempted for quarantined evidence."""

    def __init__(self) -> None:
        super().__init__(
            "EVIDENCE-ARTIFACT-QUARANTINED",
            "Quarantined evidence cannot enter the processing workflow.",
        )


__all__ = [
    "EvidenceArtifactNotFoundError",
    "EvidenceArtifactQuarantinedError",
    "EvidenceContextError",
    "EvidenceInputError",
    "EvidenceStateError",
    "EvidenceWorkflowError",
]
