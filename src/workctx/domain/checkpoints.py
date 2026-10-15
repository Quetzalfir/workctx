"""Canonical, content-free session checkpoint models."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from workctx.domain.references import WorkctxUri
from workctx.domain.topics import TopicSlug

CHECKPOINT_ID_PATTERN = r"^CHK-[0-9]{8}T[0-9]{6}Z-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{8}$"
# Checkpoint files live directly under 99_meta so transaction previews and applies never need
# an unaudited parent-directory creation in older contexts.
CHECKPOINTS_DIRECTORY = "99_meta"
_CONTENT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_ALLOWED_SOURCE_ZONES = frozenset({"02_knowledge", "03_work", "05_outbox"})
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:con|prn|aux|nul|clock\$|conin\$|conout\$|com[1-9]|lpt[1-9])(?:\..*)?$",
    re.IGNORECASE,
)

CheckpointId = Annotated[str, Field(pattern=CHECKPOINT_ID_PATTERN)]
CheckpointContentHash = Annotated[str, Field(pattern=_CONTENT_HASH_PATTERN)]


class CheckpointItem(BaseModel):
    """One canonical item identity and its content-free save-time revision markers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str
    source_path: str
    content_hash: CheckpointContentHash
    revision: AwareDatetime | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("uri")
    @classmethod
    def require_workctx_uri(cls, value: str) -> str:
        return str(WorkctxUri.parse(value))

    @field_validator("source_path")
    @classmethod
    def require_canonical_source_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or not path.parts
            or path.parts[0] not in _ALLOWED_SOURCE_ZONES
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(
                ":" in part
                or part.endswith((".", " "))
                or _WINDOWS_DEVICE_NAME.fullmatch(part) is not None
                for part in path.parts
            )
        ):
            raise ValueError("source_path must be a portable canonical document path")
        return value

    @field_validator("revision", mode="before")
    @classmethod
    def reject_numeric_revision(cls, value: object) -> object:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("revision must be an RFC 3339 string or datetime value")
        return value


class Checkpoint(BaseModel):
    """One canonical checkpoint saved through the audited transaction engine."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: CheckpointId
    created_at: AwareDatetime
    label: str = Field(min_length=1, max_length=200)
    topic: TopicSlug | None = Field(default=None, exclude_if=lambda value: value is None)
    items: list[CheckpointItem] = Field(min_length=1)

    @property
    def relative_path(self) -> str:
        return f"{CHECKPOINTS_DIRECTORY}/{self.id}.yaml"

    @field_validator("created_at", mode="before")
    @classmethod
    def reject_numeric_timestamp(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("created_at must be an RFC 3339 string or datetime value")
        return value

    @field_validator("label")
    @classmethod
    def require_safe_label_shape(cls, value: str) -> str:
        if value != value.strip() or any(character in value for character in "\r\n"):
            raise ValueError("checkpoint labels must be trimmed single-line text")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("checkpoint labels must be printable single-line text")
        return value

    @model_validator(mode="after")
    def require_unique_items(self) -> Self:
        uris = [item.uri for item in self.items]
        if len(uris) != len(set(uris)):
            raise ValueError("checkpoint item URIs must be unique")
        return self


__all__ = [
    "CHECKPOINTS_DIRECTORY",
    "CHECKPOINT_ID_PATTERN",
    "Checkpoint",
    "CheckpointContentHash",
    "CheckpointId",
    "CheckpointItem",
]
