class WorkctxError(Exception):
    """Base exception for expected Work Context OS failures."""


class ContextAlreadyExistsError(WorkctxError):
    """Raised when a target directory is not safe for context initialization."""


class ContextNotFoundError(WorkctxError):
    """Raised when a context root cannot be resolved."""


class InvalidContextError(WorkctxError):
    """Raised when context configuration cannot be loaded."""


class UserCorrectableError(WorkctxError):
    """Raised when the operator can correct the request or local state."""


class UsageConfigurationError(WorkctxError):
    """Raised when runtime configuration is invalid after CLI parsing."""


class ContextBoundaryError(WorkctxError):
    """Raised when an operation would cross an isolated context boundary."""


class ConflictError(WorkctxError):
    """Raised when a conflict or stale precondition prevents an operation."""


class UnavailableDependencyError(WorkctxError):
    """Raised when a required local dependency or plugin is unavailable."""


class StaleDerivedStateError(WorkctxError):
    """Raised after partial success leaves rebuildable derived state stale."""
