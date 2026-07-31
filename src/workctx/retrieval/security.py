from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import JsonValue

REDACTED = "[REDACTED]"

_SECRET_FIELD = re.compile(
    r"(?i)^(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|authorization|"
    r"private[_-]?key)$"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|authorization|"
    r"private[_-]?key)\b\s*[:=]\s*)(?:bearer\s+)?"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def sanitize_text(value: str) -> str:
    """Redact secret-looking values without otherwise rewriting source text."""

    if _PRIVATE_KEY_HEADER.search(value):
        return REDACTED
    sanitized = _SECRET_ASSIGNMENT.sub(rf"\1{REDACTED}", value)
    return _BEARER_TOKEN.sub(f"Bearer {REDACTED}", sanitized)


def sanitize_json(value: JsonValue) -> JsonValue:
    """Recursively redact secret-looking JSON values with deterministic key order."""

    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        sanitized: dict[str, JsonValue] = {}
        for key in sorted(value):
            item = value[key]
            sanitized[key] = REDACTED if _SECRET_FIELD.fullmatch(key) else sanitize_json(item)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_json(item) for item in value]
    return value


def sanitize_named_value(name: str, value: JsonValue) -> JsonValue:
    """Redact a value when its separate field name is secret-signaling."""

    if _SECRET_FIELD.fullmatch(name):
        return REDACTED
    return sanitize_json(value)
