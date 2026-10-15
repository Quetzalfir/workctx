"""Structured results for generated graph vaults."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GraphMap(StrEnum):
    """The two isolated Obsidian graph projections."""

    TOPICS = "topics"
    REFERENCES = "references"

    @property
    def directory_name(self) -> str:
        return "topic-map" if self is GraphMap.TOPICS else "reference-map"

    @property
    def relative_path(self) -> str:
        return f"04_views/graph/{self.directory_name}"


class _GraphResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GraphVaultResult(_GraphResultModel):
    """Summary for one generated, self-contained vault."""

    map: GraphMap
    path: str
    topic_count: int = Field(ge=0)
    item_count: int = Field(ge=0)
    file_count: int = Field(ge=0)


class GraphExportResult(_GraphResultModel):
    """Stable command result for one complete graph export."""

    schema_version: Literal[1] = 1
    context_id: str
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_topic: str | None
    topics: tuple[str, ...]
    vaults: tuple[GraphVaultResult, GraphVaultResult]


__all__ = ["GraphExportResult", "GraphMap", "GraphVaultResult"]
