"""Content-free failures for secret-reference operations."""

from __future__ import annotations

from workctx.errors import UnavailableDependencyError, UserCorrectableError


class SecretError(UserCorrectableError):
    """Base class for operator-correctable secret-reference failures."""


class InvalidSecretRefError(SecretError, ValueError):
    """Raised when a secret reference does not use the canonical name grammar."""

    def __init__(self) -> None:
        super().__init__("Secret names must be 1-64 characters of lowercase kebab-case.")


class InvalidSecretValueError(SecretError, ValueError):
    """Raised when a caller supplies a non-text secret value."""

    def __init__(self) -> None:
        super().__init__("Secret values must be text.")


class SecretNotFoundError(SecretError):
    """Raised when neither resolver layer contains a named secret."""

    def __init__(self, ref_name: str) -> None:
        self.ref_name = ref_name
        super().__init__(f"Secret reference '{ref_name}' was not found.")


class SecretBackendUnavailableError(UnavailableDependencyError):
    """Raised when an operation requires an unavailable OS credential store."""

    def __init__(self) -> None:
        super().__init__(
            "The OS credential store is unavailable; configure keyring or use a "
            "WORKCTX_SECRET_* environment variable."
        )


class SecretIndexError(SecretError):
    """Raised when the names-only user index cannot be read or updated safely."""

    def __init__(self) -> None:
        super().__init__("The names-only secret index is unavailable or malformed.")


class DotenvParseError(SecretError):
    """Raised for a malformed dotenv line without retaining its content."""

    def __init__(self, line_number: int) -> None:
        self.line_number = line_number
        super().__init__(f"The dotenv file is malformed at line {line_number}.")


class SecretImportError(SecretError):
    """Raised when a dotenv source cannot be read or securely removed."""

    def __init__(self, message: str = "The dotenv file could not be processed safely.") -> None:
        super().__init__(message)
