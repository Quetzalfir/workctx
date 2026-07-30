from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from workctx.domain.entities import CURRENT_SCHEMA_VERSION

ARTIFACT_ID_PATTERN = r"^ART-[0-9]{8}-[a-z0-9-]+-[0-9]{2}$"
CONTENT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

ArtifactId = Annotated[str, Field(pattern=ARTIFACT_ID_PATTERN)]
ContentHash = Annotated[str, Field(pattern=CONTENT_HASH_PATTERN)]
ArtifactClassification = Literal["public", "internal", "confidential", "restricted"]


class ArtifactSourceType(StrEnum):
    CHAT = "chat"
    MEETING = "meeting"
    TRANSCRIPT = "transcript"
    SCREENSHOT = "screenshot"
    IMAGE = "image"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    AUDIO = "audio"
    VIDEO = "video"
    EMAIL = "email"
    NOTE = "note"
    REPOSITORY = "repository"
    EXTERNAL_SNAPSHOT = "external_snapshot"
    OTHER = "other"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    QUARANTINED = "quarantined"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class ArtifactManifest(BaseModel):
    """Canonical metadata for one preserved evidence artifact."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(strict=True)
    id: ArtifactId
    content_hash: ContentHash
    original_name: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    source_type: ArtifactSourceType
    source_origin: str | None = None
    event_at: AwareDatetime | None = None
    event_at_inferred: bool = Field(default=False, strict=True)
    ingested_at: AwareDatetime
    language: str | None = None
    participants: list[str] = Field(default_factory=list)
    classification: ArtifactClassification | None = None
    status: ArtifactStatus
    preserved_path: str = Field(min_length=1)
    sidecars: list[str] = Field(default_factory=list)
    duplicate_of: ArtifactId | None = None
    notes: str | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _require_current_schema_version(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("schema_version must be an integer")
        if isinstance(value, int):
            normalized = value
        elif isinstance(value, float) and value.is_integer():
            normalized = int(value)
        else:
            raise ValueError("schema_version must be an integer")
        if normalized != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version {normalized}; migration to schema version "
                f"{CURRENT_SCHEMA_VERSION} is required"
            )
        return normalized

    @field_validator("participants")
    @classmethod
    def _require_unique_participants(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("participants must be unique")
        return value

    @field_validator("event_at", "ingested_at", mode="before")
    @classmethod
    def _reject_numeric_timestamps(cls, value: object) -> object:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("timestamps must be RFC 3339 strings or datetime values")
        return value
