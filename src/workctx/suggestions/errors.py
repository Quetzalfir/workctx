"""Expected, content-safe failures raised by suggestion operations."""

from __future__ import annotations

from workctx.errors import ConflictError, ContextBoundaryError, UserCorrectableError, WorkctxError


class SuggestionOperationError(WorkctxError):
    """Base class for deterministic suggestion-operation failures."""


class SuggestionApprovalRequiredError(UserCorrectableError, SuggestionOperationError):
    """Raised when a suggestion mutation lacks explicit runtime approval."""

    def __init__(self) -> None:
        super().__init__("Explicit approval is required for suggestion mutations.")


class SuggestionNotFoundError(UserCorrectableError, SuggestionOperationError):
    """Raised when a requested suggestion record is absent."""

    def __init__(self) -> None:
        super().__init__("The requested suggestion record was not found.")


class SuggestionContextError(ContextBoundaryError, SuggestionOperationError):
    """Raised when a suggestion URI belongs to another context."""

    def __init__(self) -> None:
        super().__init__("The suggestion reference belongs to another context.")


class SuggestionProposalError(UserCorrectableError, SuggestionOperationError):
    """Raised when an embedded data-fix proposal is unsafe or invalid."""

    def __init__(self, message: str = "The embedded data-fix proposal is not valid.") -> None:
        super().__init__(message)


class SuggestionStateError(ConflictError, SuggestionOperationError):
    """Raised when canonical suggestion state cannot be changed safely."""

    def __init__(
        self,
        message: str = "Suggestion state is inconsistent with the requested change.",
    ) -> None:
        super().__init__(message)


class SuggestionSequenceExhaustedError(ConflictError, SuggestionOperationError):
    """Raised when one suggestion slug exhausts its daily identifier range."""

    def __init__(self, day: str, slug: str) -> None:
        self.day = day
        self.slug = slug
        super().__init__("The daily suggestion identifier sequence is exhausted.")


__all__ = [
    "SuggestionApprovalRequiredError",
    "SuggestionContextError",
    "SuggestionNotFoundError",
    "SuggestionOperationError",
    "SuggestionProposalError",
    "SuggestionSequenceExhaustedError",
    "SuggestionStateError",
]
