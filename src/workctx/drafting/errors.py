"""Expected, content-safe failures raised by the local drafting workflow."""

from __future__ import annotations

from workctx.errors import ConflictError, ContextBoundaryError, UserCorrectableError, WorkctxError


class DraftingError(WorkctxError):
    """Base class for deterministic drafting failures."""


class DraftInputError(UserCorrectableError, DraftingError):
    """Raised when a draft selector or payload cannot be used safely."""

    def __init__(self, message: str = "The draft input is invalid.") -> None:
        super().__init__(message)


class DraftNotFoundError(UserCorrectableError, DraftingError):
    """Raised when a requested person, task, or draft is absent."""

    def __init__(self, message: str = "The requested draft was not found.") -> None:
        super().__init__(message)


class DraftSecretError(UserCorrectableError, DraftingError):
    """Raised without retaining a payload that may contain a secret."""

    def __init__(self) -> None:
        super().__init__("A possible secret prevents this draft from being persisted.")


class DraftContextError(ContextBoundaryError, DraftingError):
    """Raised when a drafting reference crosses the active context boundary."""

    def __init__(self) -> None:
        super().__init__("A drafting reference belongs to another context.")


class DraftStateError(ConflictError, DraftingError):
    """Raised when canonical draft state cannot be read or changed safely."""

    def __init__(self, message: str = "Canonical draft state is inconsistent.") -> None:
        super().__init__(message)


class DraftContextChangedError(ConflictError, DraftingError):
    """Raised when deterministic context gathering cannot obtain one stable snapshot."""

    def __init__(self) -> None:
        super().__init__("The context changed repeatedly while reply context was gathered.")


__all__ = [
    "DraftContextChangedError",
    "DraftContextError",
    "DraftInputError",
    "DraftNotFoundError",
    "DraftSecretError",
    "DraftStateError",
    "DraftingError",
]
