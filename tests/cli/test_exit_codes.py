import pytest
import typer

from workctx.errors import (
    ConflictError,
    ContextAlreadyExistsError,
    ContextBoundaryError,
    ContextNotFoundError,
    InvalidContextError,
    StaleDerivedStateError,
    UnavailableDependencyError,
    UsageConfigurationError,
    UserCorrectableError,
)
from workctx.presentation import ExitCode, exit_code_for, sanitize_message


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, ExitCode.SUCCESS),
        (UserCorrectableError("fix me"), ExitCode.USER_CORRECTABLE),
        (UsageConfigurationError("bad config"), ExitCode.USAGE_CONFIGURATION),
        (ContextBoundaryError("wrong context"), ExitCode.CONTEXT_BOUNDARY),
        (ConflictError("stale"), ExitCode.CONFLICT),
        (UnavailableDependencyError("missing"), ExitCode.UNAVAILABLE_DEPENDENCY),
        (StaleDerivedStateError("rebuild"), ExitCode.PARTIAL_SUCCESS),
        (RuntimeError("boom"), ExitCode.INTERNAL_FAILURE),
    ],
)
def test_exit_code_mapper_covers_every_band(
    error: BaseException | None, expected: ExitCode
) -> None:
    assert exit_code_for(error) == expected


@pytest.mark.parametrize(
    "error",
    [
        ContextAlreadyExistsError("exists"),
        ContextNotFoundError("missing"),
        InvalidContextError("invalid"),
    ],
)
def test_existing_context_errors_remain_user_correctable(error: BaseException) -> None:
    assert exit_code_for(error) == ExitCode.USER_CORRECTABLE


def test_permission_error_maps_to_context_boundary() -> None:
    assert exit_code_for(PermissionError("denied")) == ExitCode.CONTEXT_BOUNDARY


def test_typer_usage_error_maps_to_usage_band() -> None:
    assert exit_code_for(typer.BadParameter("bad value")) == ExitCode.USAGE_CONFIGURATION


def test_sanitize_message_removes_controls_and_secret_values() -> None:
    value = sanitize_message(
        'bad\napi_key=abcdefghijklmnopqrstuv\tBearer opaque-token password="two words"'
    )

    assert "\n" not in value
    assert "\t" not in value
    assert "abcdefghijklmnopqrstuv" not in value
    assert "opaque-token" not in value
    assert "two words" not in value
    assert value.count("[REDACTED]") == 3
