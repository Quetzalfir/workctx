"""Expected failures raised by the project-scoped agent adapters."""

from workctx.errors import (
    ConflictError,
    UnavailableDependencyError,
    UserCorrectableError,
    WorkctxError,
)


class AgentAdapterError(WorkctxError):
    """Base class for agent-adapter failures."""


class InvalidAdapterStateError(AgentAdapterError):
    """Raised when unsafe or malformed project-local adapter state is found."""


class AdapterConflictError(ConflictError, AgentAdapterError):
    """Raised when an unapproved file conflict prevents a mutation."""


class UnsupportedClientVersionError(UnavailableDependencyError, AgentAdapterError):
    """Raised when a detected client is outside the supported version range."""


class RecoveryRequiredError(ConflictError, AgentAdapterError):
    """Raised when a flushed transaction intent requires recovery first."""


class RecoveryConflictError(ConflictError, AgentAdapterError):
    """Raised when transaction recovery cannot choose a safe outcome."""


class InvalidApprovalError(UserCorrectableError, AgentAdapterError):
    """Raised when a destructive approval is missing or no longer exact."""


AgentUnavailableError = UnavailableDependencyError
AdapterInvalidError = InvalidAdapterStateError
