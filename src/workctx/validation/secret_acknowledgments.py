"""Typed canonical records for exact-content secret-scan acknowledgments."""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SECRET_SCAN_ACKNOWLEDGMENTS_PATH = "99_meta/secret-scan-acknowledgments.yaml"

_CONTENT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_WINDOWS_DEVICE_NAME = re.compile(
    r"(?i)(?:CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9]|LPT[1-9])"
    r"(?:\..*)?"
)


def _validate_context_relative_path(value: str) -> str:
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("acknowledgment paths must use Unicode NFC")
    if "\\" in value or "\x00" in value or any(ord(character) < 32 for character in value):
        raise ValueError("acknowledgment paths must be printable POSIX paths")
    if PurePosixPath(value).is_absolute():
        raise ValueError("acknowledgment paths must be context-relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("acknowledgment paths cannot contain empty or traversal segments")
    if any(
        ":" in part or part.endswith((".", " ")) or _WINDOWS_DEVICE_NAME.fullmatch(part)
        for part in parts
    ):
        raise ValueError("acknowledgment paths must use Windows-portable path segments")
    if value.casefold() == SECRET_SCAN_ACKNOWLEDGMENTS_PATH.casefold():
        raise ValueError("the acknowledgment file cannot acknowledge itself")
    return value


ContextRelativeAcknowledgmentPath = Annotated[
    str,
    Field(min_length=1, max_length=1000),
    AfterValidator(_validate_context_relative_path),
]
ContentHash = Annotated[str, Field(pattern=_CONTENT_HASH_PATTERN)]


class _AcknowledgmentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecretScanAcknowledgment(_AcknowledgmentRecord):
    path: ContextRelativeAcknowledgmentPath
    content_hash: ContentHash
    acknowledged_at: AwareDatetime
    note: str | None = Field(default=None, max_length=500)

    @field_validator("acknowledged_at")
    @classmethod
    def require_utc(cls, value: AwareDatetime) -> AwareDatetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("acknowledged_at must use UTC")
        return value


class SecretScanAcknowledgments(_AcknowledgmentRecord):
    schema_version: Literal[1] = 1
    acknowledgments: tuple[SecretScanAcknowledgment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_paths(self) -> Self:
        keys = [entry.path.casefold() for entry in self.acknowledgments]
        if len(keys) != len(set(keys)):
            raise ValueError("acknowledgment paths must be unique")
        return self

    def by_path(self) -> dict[str, SecretScanAcknowledgment]:
        return {entry.path: entry for entry in self.acknowledgments}


def load_secret_scan_acknowledgments(content: str) -> SecretScanAcknowledgments:
    """Parse the canonical YAML record; callers own sanitized error reporting."""

    loaded: Any = yaml.safe_load(content)
    return SecretScanAcknowledgments.model_validate(loaded)


def dump_secret_scan_acknowledgments(record: SecretScanAcknowledgments) -> bytes:
    """Render deterministic UTF-8 YAML suitable for one staged replacement."""

    payload = record.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")


__all__ = [
    "SECRET_SCAN_ACKNOWLEDGMENTS_PATH",
    "SecretScanAcknowledgment",
    "SecretScanAcknowledgments",
    "dump_secret_scan_acknowledgments",
    "load_secret_scan_acknowledgments",
]
