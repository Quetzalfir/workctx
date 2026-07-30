from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import IntEnum
from time import perf_counter_ns
from typing import Any

from pydantic import JsonValue
from typer.core import TyperGroup

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
    WorkctxError,
)
from workctx.presentation.envelope import CliDiagnostic, build_envelope
from workctx.presentation.streams import write_envelope, write_error

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|authorization)\b"
    r"\s*[:=]\s*)(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_MAX_DIAGNOSTIC_LENGTH = 500


class ExitCode(IntEnum):
    """Lead-decided process exit codes from doc-04."""

    SUCCESS = 0
    USER_CORRECTABLE = 1
    USAGE_CONFIGURATION = 2
    CONTEXT_BOUNDARY = 3
    CONFLICT = 4
    UNAVAILABLE_DEPENDENCY = 5
    PARTIAL_SUCCESS = 6
    INTERNAL_FAILURE = 10


@dataclass(slots=True)
class _InvocationState:
    started_ns: int
    command: str
    json_output: bool
    context_id: str | None = None
    result: dict[str, JsonValue] = field(default_factory=dict)
    warnings: list[CliDiagnostic] = field(default_factory=list)
    errors: list[CliDiagnostic] = field(default_factory=list)


_CURRENT_INVOCATION: ContextVar[_InvocationState | None] = ContextVar(
    "workctx_cli_invocation", default=None
)


def exit_code_for(error: BaseException | None) -> int:
    """Map success or one exception to the exact doc-04 exit-code table."""

    if error is None:
        return ExitCode.SUCCESS
    if isinstance(error, UsageConfigurationError) or getattr(error, "exit_code", None) == 2:
        return ExitCode.USAGE_CONFIGURATION
    if isinstance(error, (ContextBoundaryError, PermissionError)):
        return ExitCode.CONTEXT_BOUNDARY
    if isinstance(error, ConflictError):
        return ExitCode.CONFLICT
    if isinstance(error, UnavailableDependencyError):
        return ExitCode.UNAVAILABLE_DEPENDENCY
    if isinstance(error, StaleDerivedStateError):
        return ExitCode.PARTIAL_SUCCESS
    if isinstance(
        error,
        (
            ContextAlreadyExistsError,
            ContextNotFoundError,
            InvalidContextError,
            UserCorrectableError,
            WorkctxError,
        ),
    ):
        return ExitCode.USER_CORRECTABLE
    return ExitCode.INTERNAL_FAILURE


def sanitize_message(message: object, *, fallback: str = "Operation failed.") -> str:
    """Make expected diagnostic text single-line, bounded, and secret-safe."""

    text = _CONTROL_CHARACTERS.sub(" ", str(message))
    text = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = " ".join(text.split()).strip() or fallback
    if len(text) > _MAX_DIAGNOSTIC_LENGTH:
        text = f"{text[: _MAX_DIAGNOSTIC_LENGTH - 3]}..."
    return text


def begin_command(command: str, *, json_output: bool) -> None:
    """Attach parsed command metadata to the active top-level invocation."""

    state = _state()
    state.command = command
    state.json_output = json_output


def emit_success(
    *,
    result: Mapping[str, JsonValue],
    context_id: str | None = None,
    warnings: Sequence[CliDiagnostic] = (),
) -> None:
    """Emit a successful envelope when the parsed command requested JSON."""

    state = _state()
    state.context_id = context_id
    if not state.json_output:
        return
    envelope = build_envelope(
        ok=True,
        command=state.command,
        context_id=context_id,
        result=result,
        warnings=warnings,
        duration_ms=_duration_ms(state),
    )
    write_envelope(envelope)


def record_failure(
    *,
    result: Mapping[str, JsonValue],
    context_id: str | None = None,
    warnings: Sequence[CliDiagnostic] = (),
    errors: Sequence[CliDiagnostic] = (),
) -> None:
    """Record command details that the outer exception boundary will serialize."""

    state = _state()
    state.context_id = context_id
    state.result = dict(result)
    state.warnings = list(warnings)
    state.errors = list(errors)


class PresentationTyperGroup(TyperGroup):
    """Typer root group with the single CLI exception and exit-code boundary."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        raw_args = tuple(sys.argv[1:] if args is None else args)
        state = _InvocationState(
            started_ns=perf_counter_ns(),
            command=_infer_command(raw_args),
            json_output="--json" in raw_args,
        )
        token = _CURRENT_INVOCATION.set(state)
        try:
            return super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except Exception as exc:
            if not standalone_mode and getattr(exc, "exit_code", None) is not None:
                raise
            code = exit_code_for(exc)
            _emit_failure(state, exc, code)
            raise SystemExit(code) from None
        finally:
            _CURRENT_INVOCATION.reset(token)


def _state() -> _InvocationState:
    state = _CURRENT_INVOCATION.get()
    if state is None:
        state = _InvocationState(started_ns=perf_counter_ns(), command="unknown", json_output=False)
        _CURRENT_INVOCATION.set(state)
    return state


def _duration_ms(state: _InvocationState) -> int:
    return max(0, (perf_counter_ns() - state.started_ns) // 1_000_000)


def _emit_failure(state: _InvocationState, error: Exception, code: int) -> None:
    diagnostic = _diagnostic_for(error, code)
    errors = state.errors or [diagnostic]
    if state.json_output:
        envelope = build_envelope(
            ok=False,
            command=state.command,
            context_id=state.context_id,
            result=state.result,
            warnings=state.warnings,
            errors=errors,
            duration_ms=_duration_ms(state),
        )
        write_envelope(envelope)
    write_error(diagnostic.message)


def _diagnostic_for(error: Exception, code: int) -> CliDiagnostic:
    if code == ExitCode.INTERNAL_FAILURE:
        return CliDiagnostic(code="INTERNAL_ERROR", message="Unexpected internal failure.")
    if isinstance(error, InvalidContextError):
        return CliDiagnostic(code="INVALID_CONTEXT", message="Context configuration is invalid.")
    if isinstance(error, ContextNotFoundError):
        return CliDiagnostic(code="CONTEXT_NOT_FOUND", message=sanitize_message(error))
    if isinstance(error, ContextAlreadyExistsError):
        return CliDiagnostic(
            code="CONTEXT_ALREADY_EXISTS",
            message="Context target already exists or is not an empty directory.",
        )

    error_code = {
        ExitCode.USER_CORRECTABLE: "USER_CORRECTABLE",
        ExitCode.USAGE_CONFIGURATION: "USAGE_CONFIGURATION",
        ExitCode.CONTEXT_BOUNDARY: "CONTEXT_BOUNDARY",
        ExitCode.CONFLICT: "CONFLICT",
        ExitCode.UNAVAILABLE_DEPENDENCY: "DEPENDENCY_UNAVAILABLE",
        ExitCode.PARTIAL_SUCCESS: "STALE_DERIVED_STATE",
    }.get(ExitCode(code), "INTERNAL_ERROR")
    return CliDiagnostic(code=error_code, message=sanitize_message(error))


def _infer_command(args: Sequence[str]) -> str:
    if not args:
        return "unknown"
    if args[0] == "context" and len(args) > 1 and not args[1].startswith("-"):
        return f"context.{args[1]}"
    if not args[0].startswith("-"):
        return args[0]
    return "unknown"
