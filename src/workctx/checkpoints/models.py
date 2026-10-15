"""Typed, content-free checkpoint check results."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckpointStatus(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class CheckpointReason(StrEnum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    DELETED = "deleted"
    MOVED = "moved"
    SUPERSEDED = "superseded"


class CheckpointLocation(_ResultModel):
    uri: str
    source_path: str
    revision: AwareDatetime | None
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CheckpointItemResult(_ResultModel):
    uri: str
    status: CheckpointStatus
    reason: CheckpointReason
    saved_path: str
    current_path: str | None
    saved_revision: AwareDatetime | None
    current_revision: AwareDatetime | None
    saved_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    current_content_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    changes: tuple[Literal["content", "revision"], ...]
    successors: tuple[CheckpointLocation, ...]


class CheckpointNewItem(_ResultModel):
    uri: str
    source_path: str
    created_at: AwareDatetime


class CheckpointStatusCounts(_ResultModel):
    green: int = Field(ge=0)
    yellow: int = Field(ge=0)
    red: int = Field(ge=0)
    new: int = Field(ge=0)


class CheckpointCheckResult(_ResultModel):
    schema_version: Literal[1] = 1
    checkpoint_id: str
    label: str
    checkpoint_created_at: AwareDatetime
    checked_at: AwareDatetime
    topic: str | None
    items: tuple[CheckpointItemResult, ...]
    new_items: tuple[CheckpointNewItem, ...]
    counts: CheckpointStatusCounts


__all__ = [
    "CheckpointCheckResult",
    "CheckpointItemResult",
    "CheckpointLocation",
    "CheckpointNewItem",
    "CheckpointReason",
    "CheckpointStatus",
    "CheckpointStatusCounts",
]
