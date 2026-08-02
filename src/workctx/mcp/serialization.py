"""Deterministic JSON conversion and redaction for the MCP boundary."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import cast

from pydantic import BaseModel, JsonValue

from workctx.domain import ArtifactReference, RepoReference, WorkctxUri
from workctx.presentation import sanitize_message

REDACTED = "[REDACTED]"

_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])"
    r"(?P<prefix>(?:\$env:)?(?P<key>[A-Za-z][A-Za-z0-9_-]*)"
    r"['\"]?\s*[:=]\s*)"
    r"(?P<value>(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+))"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KNOWN_TOKEN = re.compile(r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,})")
_SECRET_FIELD_SUFFIXES = (
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "password",
    "passwd",
    "private_key",
    "secret",
    "secret_access_key",
    "token",
)


def to_json_value(value: object) -> JsonValue:
    """Convert public engine records to JSON without introspecting private state."""

    if value is None or type(value) in {bool, int, str}:
        return cast(JsonValue, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise TypeError("MCP results cannot contain non-finite numbers")
        return value
    if isinstance(value, (WorkctxUri, ArtifactReference, RepoReference)):
        return str(value)
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("MCP result object keys must be strings")
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [to_json_value(item) for item in sorted(value, key=str)]
    raise TypeError("MCP result contains an unsupported value")


def sanitize_json_value(value: JsonValue) -> JsonValue:
    """Apply the repository's complete secret-pattern union recursively."""

    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key in sorted(value):
            item = value[key]
            result[key] = REDACTED if _is_secret_field(key) else sanitize_json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_json_value(item) for item in value]
    return value


def sanitize_text(value: str) -> str:
    """Redact secret-looking substrings without collapsing ordinary authored text."""

    if _PRIVATE_KEY.search(value):
        return REDACTED
    sanitized = _SECRET_ASSIGNMENT.sub(_redact_secret_assignment, value)
    sanitized = _BEARER_TOKEN.sub(f"Bearer {REDACTED}", sanitized)
    return _KNOWN_TOKEN.sub(REDACTED, sanitized)


def _is_secret_field(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    compact = normalized.replace("_", "")
    return any(
        normalized == suffix
        or normalized.endswith(f"_{suffix}")
        or compact == suffix.replace("_", "")
        or compact.endswith(suffix.replace("_", ""))
        for suffix in _SECRET_FIELD_SUFFIXES
    )


def _redact_secret_assignment(match: re.Match[str]) -> str:
    if not _is_secret_field(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{REDACTED}"


def safe_diagnostic_text(value: object, *, fallback: str = "Operation failed.") -> str:
    """Return a bounded single-line diagnostic using both sanitization layers."""

    return sanitize_message(sanitize_text(str(value)), fallback=fallback)


def safe_result_object(value: object) -> dict[str, JsonValue]:
    """Convert and sanitize a result that must be a JSON object."""

    converted = sanitize_json_value(to_json_value(value))
    if not isinstance(converted, dict):
        raise TypeError("MCP tool results must be JSON objects")
    return converted


__all__ = [
    "REDACTED",
    "safe_diagnostic_text",
    "safe_result_object",
    "sanitize_json_value",
    "sanitize_text",
    "to_json_value",
]
