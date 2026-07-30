class WorkctxError(Exception):
    """Base exception for expected Work Context OS failures."""


class ContextAlreadyExistsError(WorkctxError):
    """Raised when a target directory is not safe for context initialization."""


class ContextNotFoundError(WorkctxError):
    """Raised when a context root cannot be resolved."""


class InvalidContextError(WorkctxError):
    """Raised when context configuration cannot be loaded."""
