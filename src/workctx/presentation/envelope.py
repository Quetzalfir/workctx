from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

SCHEMA_VERSION: Literal[1] = 1


class CliDiagnostic(BaseModel):
    """A structured, sanitized warning or error for CLI consumers."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_-]*$")
    message: str = Field(min_length=1, max_length=500)
    path: str | None = Field(default=None, max_length=500)
    repair_action: str | None = Field(default=None, max_length=500)

    @field_validator("message")
    @classmethod
    def message_is_single_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("diagnostic messages must be single-line")
        return value

    @field_validator("path")
    @classmethod
    def path_is_single_line(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("diagnostic paths must be single-line")
        return value


class CliMeta(BaseModel):
    """Stable metadata included with every JSON envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    duration_ms: int = Field(ge=0)

    @field_validator("duration_ms", mode="before")
    @classmethod
    def normalize_json_integer(cls, value: object) -> object:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value


class CliEnvelope(BaseModel):
    """The public JSON boundary shared by all machine-readable commands."""

    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool
    command: str = Field(min_length=1, max_length=128)
    context_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    result: dict[str, JsonValue]
    warnings: list[CliDiagnostic]
    errors: list[CliDiagnostic]
    meta: CliMeta

    @model_validator(mode="after")
    def error_state_matches_ok(self) -> Self:
        if self.ok and self.errors:
            raise ValueError("successful envelopes cannot contain errors")
        if not self.ok and not self.errors:
            raise ValueError("failed envelopes must contain at least one error")
        return self


def build_envelope(
    *,
    ok: bool,
    command: str,
    context_id: str | None,
    result: Mapping[str, JsonValue],
    warnings: Sequence[CliDiagnostic] = (),
    errors: Sequence[CliDiagnostic] = (),
    duration_ms: int,
) -> CliEnvelope:
    """Build and validate one public CLI envelope."""

    return CliEnvelope(
        ok=ok,
        command=command,
        context_id=context_id,
        result=dict(result),
        warnings=list(warnings),
        errors=list(errors),
        meta=CliMeta(duration_ms=duration_ms),
    )
