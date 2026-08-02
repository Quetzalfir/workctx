"""SDK-neutral records for MCP tool results and resource failures."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from workctx.mcp.contracts import SCHEMA_VERSION


class ErrorCategory(StrEnum):
    USER_CORRECTABLE = "user_correctable"
    USAGE_CONFIGURATION = "usage_configuration"
    CONTEXT_BOUNDARY = "context_boundary"
    CONFLICT = "conflict"
    UNAVAILABLE_DEPENDENCY = "unavailable_dependency"
    PARTIAL_SUCCESS = "partial_success"
    INTERNAL_FAILURE = "internal_failure"


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    ADVISORY = "advisory"


class McpDiagnostic(BaseModel):
    """One stable, sanitized tool-boundary diagnostic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_-]*$")
    category: ErrorCategory
    severity: DiagnosticSeverity
    message: str = Field(min_length=1, max_length=500)
    path: str | None = Field(default=None, max_length=500)
    repair_action: str | None = Field(default=None, max_length=500)

    @field_validator("message", "path", "repair_action")
    @classmethod
    def require_single_line(cls, value: str | None) -> str | None:
        if value is not None and any(character in value for character in "\r\n"):
            raise ValueError("MCP diagnostics must be single-line")
        return value


class McpEnvelope(BaseModel):
    """Versioned structured content returned by every Work Context MCP tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    ok: bool
    context_id: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    result: dict[str, JsonValue]
    warnings: tuple[McpDiagnostic, ...] = ()
    errors: tuple[McpDiagnostic, ...] = ()

    @model_validator(mode="after")
    def require_consistent_error_state(self) -> Self:
        if self.ok and self.errors:
            raise ValueError("Successful MCP results cannot contain errors")
        if not self.ok and not self.errors:
            raise ValueError("Failed MCP results require at least one error")
        return self


class ToolResponse(BaseModel):
    """SDK-neutral response plus the short text summary MCP clients display."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope: McpEnvelope
    summary: str = Field(min_length=1, max_length=500)

    @property
    def is_error(self) -> bool:
        return not self.envelope.ok


class ResourceAccessError(Exception):
    """Safe resource error translated to an MCP INVALID_PARAMS response."""

    def __init__(self, diagnostic: McpDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


__all__ = [
    "DiagnosticSeverity",
    "ErrorCategory",
    "McpDiagnostic",
    "McpEnvelope",
    "ResourceAccessError",
    "ToolResponse",
]
