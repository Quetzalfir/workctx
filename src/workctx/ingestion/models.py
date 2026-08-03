"""Typed application records for artifact registration and inbox lifecycle operations."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from workctx.domain.artifacts import (
    ArtifactClassification,
    ArtifactManifest,
    ArtifactSourceType,
)
from workctx.transactions import ApplyResult

DEFAULT_MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
DEFAULT_SCAN_CHUNK_BYTES = 1024 * 1024

_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:con|prn|aux|nul|clock\$|conin\$|conout\$|com[1-9]|lpt[1-9])(?:\..*)?$",
    re.IGNORECASE,
)


class _IngestionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DuplicatePolicy(StrEnum):
    """Policy for a new path whose primary bytes already have a manifest."""

    REFUSE = "refuse"
    LINK = "link"


class QuarantineReason(StrEnum):
    """Stable, content-free reasons for quarantining an artifact set."""

    EXECUTABLE_PAYLOAD = "executable_payload"
    OVERSIZED = "oversized"
    POSSIBLE_SECRET = "possible_secret"
    PROMPT_INJECTION = "prompt_injection"
    UNSUPPORTED_TYPE = "unsupported_type"


class RegistrationDisposition(StrEnum):
    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"
    DUPLICATE_LINKED = "duplicate_linked"
    QUARANTINED = "quarantined"


class ArchiveDisposition(StrEnum):
    ARCHIVED = "archived"
    ALREADY_ARCHIVED = "already_archived"
    RECOVERED = "recovered"


class IngestionPolicy(_IngestionRecord):
    """Bounded deterministic registration controls."""

    max_artifact_bytes: int = Field(default=DEFAULT_MAX_ARTIFACT_BYTES, ge=1)
    scan_chunk_bytes: int = Field(default=DEFAULT_SCAN_CHUNK_BYTES, ge=4096, le=8 * 1024 * 1024)
    scan_overlap_chars: int = Field(default=4096, ge=256, le=65536)


class RegisterRequest(_IngestionRecord):
    """Metadata for one file already present below ``00_inbox/raw``."""

    path: str
    source_type: ArtifactSourceType
    media_type: str | None = None
    source_origin: str | None = None
    event_at: AwareDatetime | None = None
    event_at_inferred: bool = False
    language: str | None = None
    participants: tuple[str, ...] = ()
    classification: ArtifactClassification | None = None
    sidecars: tuple[str, ...] = ()
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.REFUSE

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_raw_path(value)

    @field_validator("sidecars")
    @classmethod
    def validate_sidecars(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_validate_raw_path(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("sidecars must contain unique paths")
        return normalized

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("media_type must be a printable non-empty value")
        return normalized

    @field_validator("participants")
    @classmethod
    def validate_participants(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("participants must be unique")
        if any(not value for value in values):
            raise ValueError("participants must not contain empty values")
        return values

    @model_validator(mode="after")
    def validate_pairing_and_event_time(self) -> Self:
        if self.path in self.sidecars:
            raise ValueError("the primary artifact cannot also be a sidecar")
        if self.event_at_inferred and self.event_at is None:
            raise ValueError("event_at_inferred requires event_at")
        return self


class IngestionDiagnostic(_IngestionRecord):
    """A location-only ingestion diagnostic."""

    reason: QuarantineReason
    path: str


class ArtifactRecord(_IngestionRecord):
    manifest_path: str
    reference: str
    manifest: ArtifactManifest


class RegistrationResult(_IngestionRecord):
    disposition: RegistrationDisposition
    artifact: ArtifactRecord
    diagnostics: tuple[IngestionDiagnostic, ...] = ()
    receipts: tuple[ApplyResult, ...] = ()
    recovered_move: bool = False


class InboxListing(_IngestionRecord):
    artifacts: tuple[ArtifactRecord, ...]

    @property
    def count(self) -> int:
        return len(self.artifacts)


class QuarantineInfo(_IngestionRecord):
    artifact: ArtifactRecord
    diagnostics: tuple[IngestionDiagnostic, ...]
    recovery_pending: bool
    source_present: bool
    destination_present: bool


class ArchiveResult(_IngestionRecord):
    disposition: ArchiveDisposition
    artifact: ArtifactRecord
    source_path: str
    destination_path: str
    manifest_receipt: ApplyResult | None = None


class _SidecarMetadata(_IngestionRecord):
    source_path: str
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class _ManifestMetadata(_IngestionRecord):
    schema_version: Literal[1] = 1
    registered_path: str
    sidecars: tuple[_SidecarMetadata, ...] = ()
    quarantine_diagnostics: tuple[IngestionDiagnostic, ...] = ()
    quarantine_receipt: ApplyResult | None = None


def _validate_raw_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("artifact paths must be non-empty strings")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("artifact paths must use Unicode NFC")
    if "\\" in value or "\x00" in value or any(ord(character) < 32 for character in value):
        raise ValueError("artifact paths must be portable, printable POSIX paths")
    parts = value.split("/")
    if parts[:2] != ["00_inbox", "raw"] or len(parts) < 3:
        raise ValueError("artifact paths must name a file below 00_inbox/raw")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact paths cannot contain empty or traversal segments")
    if any(
        ":" in part or part.endswith((".", " ")) or _WINDOWS_DEVICE_NAME.fullmatch(part) is not None
        for part in parts
    ):
        raise ValueError("artifact paths must use Windows-portable path segments")
    return value


__all__ = [
    "ArchiveDisposition",
    "ArchiveResult",
    "ArtifactRecord",
    "DuplicatePolicy",
    "InboxListing",
    "IngestionDiagnostic",
    "IngestionPolicy",
    "QuarantineInfo",
    "QuarantineReason",
    "RegisterRequest",
    "RegistrationDisposition",
    "RegistrationResult",
]
