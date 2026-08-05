"""Expected, content-safe failures raised by the local drafting workflow."""

from __future__ import annotations

from workctx.errors import (
    ConflictError,
    ContextBoundaryError,
    UsageConfigurationError,
    UserCorrectableError,
    WorkctxError,
)


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


class SendError(WorkctxError):
    """Base class for content-free outbox delivery failures."""


class SendApprovalRequiredError(UsageConfigurationError, SendError):
    """Raised before any delivery work when approval is absent."""

    def __init__(self) -> None:
        super().__init__("Explicit approval is required for one outbox send operation.")


class SendInputError(UserCorrectableError, SendError):
    """Raised for an unsupported channel, target, or fingerprint shape."""

    def __init__(self, message: str = "The outbox send input is invalid.") -> None:
        super().__init__(message)


class SendFingerprintMismatchError(ConflictError, SendError):
    """Raised when approval does not pin the exact current draft and target."""

    def __init__(self) -> None:
        super().__init__("The approved send fingerprint does not match the current operation.")


class SendStateError(ConflictError, SendError):
    """Raised when a sent or inconsistent draft cannot enter delivery again."""

    def __init__(
        self,
        message: str = "The draft cannot enter the requested delivery state.",
    ) -> None:
        super().__init__(message)


class SendSecretError(UserCorrectableError, SendError):
    """Raised without retaining outgoing content that may contain a secret."""

    def __init__(self) -> None:
        super().__init__("A possible secret prevents this draft from being sent.")


class SendAuthenticationError(UserCorrectableError, SendError):
    """Raised when the fixed GitHub credential chain cannot resolve a token."""

    def __init__(self) -> None:
        super().__init__("GitHub authentication is unavailable for this send operation.")


class SendDeliveryError(UserCorrectableError, SendError):
    """Base class for remote failures whose diagnostics contain no response content."""


class SendConnectionError(SendDeliveryError):
    def __init__(self) -> None:
        super().__init__("GitHub delivery could not complete; verify the target before any retry.")


class SendTimeoutError(SendDeliveryError):
    def __init__(self) -> None:
        super().__init__(
            "GitHub delivery timed out with an unknown remote outcome; verify the target "
            "before any retry."
        )


class SendStatusError(SendDeliveryError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"GitHub refused the delivery request with HTTP {status_code}; no response "
            "content was retained."
        )


class SendResponseError(SendDeliveryError):
    def __init__(self) -> None:
        super().__init__(
            "GitHub returned an unusable delivery receipt; verify the target before any retry."
        )


class SendAuditCommitError(ConflictError, SendError):
    """Raised when GitHub succeeded but local sent-state commit did not."""

    def __init__(self, remote_comment_id: str, remote_comment_url: str) -> None:
        self.remote_comment_id = remote_comment_id
        self.remote_comment_url = remote_comment_url
        super().__init__(
            "The GitHub comment was created, but its local delivery record could not be "
            "committed; do not resend until the draft is reconciled."
        )


__all__ = [
    "DraftContextChangedError",
    "DraftContextError",
    "DraftInputError",
    "DraftNotFoundError",
    "DraftSecretError",
    "DraftStateError",
    "DraftingError",
    "SendApprovalRequiredError",
    "SendAuditCommitError",
    "SendAuthenticationError",
    "SendConnectionError",
    "SendDeliveryError",
    "SendError",
    "SendFingerprintMismatchError",
    "SendInputError",
    "SendResponseError",
    "SendSecretError",
    "SendStateError",
    "SendStatusError",
    "SendTimeoutError",
]
