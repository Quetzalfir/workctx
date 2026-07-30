"""Shared CLI presentation primitives."""

from workctx.presentation.boundary import (
    ExitCode,
    PresentationTyperGroup,
    begin_command,
    emit_success,
    exit_code_for,
    record_failure,
    sanitize_message,
)
from workctx.presentation.context import resolve_cli_context
from workctx.presentation.envelope import CliDiagnostic, CliEnvelope, CliMeta, build_envelope
from workctx.presentation.streams import error_console, output_console

__all__ = [
    "CliDiagnostic",
    "CliEnvelope",
    "CliMeta",
    "ExitCode",
    "PresentationTyperGroup",
    "begin_command",
    "build_envelope",
    "emit_success",
    "error_console",
    "exit_code_for",
    "output_console",
    "record_failure",
    "resolve_cli_context",
    "sanitize_message",
]
