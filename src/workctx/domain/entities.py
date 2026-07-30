from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from workctx.domain.references import WorkctxUri

EntityType = Literal[
    "evidence",
    "person",
    "team",
    "project",
    "system",
    "service",
    "module",
    "flow",
    "integration",
    "decision",
    "risk",
    "question",
    "task",
    "claim",
    "draft",
    "investigation",
    "incident",
    "observation",
    "artifact",
]
ConfidenceLevel = Literal["low", "medium", "high"]
RelationType = Literal[
    "derived_from",
    "evidenced_by",
    "supports",
    "contradicts",
    "supersedes",
    "parent_of",
    "depends_on",
    "blocks",
    "requested_by",
    "owned_by",
    "waiting_on",
    "produces",
    "implements",
    "calls",
    "publishes_to",
    "consumes_from",
    "stores_in",
    "authenticates_via",
    "operated_by",
    "affects",
    "mentions",
    "related_to",
]

CURRENT_SCHEMA_VERSION = 1


class _ReferencePayload(BaseModel):
    """WP-100-compatible validation for the frozen reference schema."""

    model_config = ConfigDict(extra="forbid")

    relation: RelationType
    target: str = Field(pattern=r"^[a-z][a-z0-9+.-]*://.+$")
    confidence: ConfidenceLevel | None = None
    source_observations: list[str] | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null_for_non_nullable_fields(cls, value: object) -> object:
        if isinstance(value, dict):
            for field_name in ("confidence", "source_observations", "note"):
                if field_name in value and value[field_name] is None:
                    raise ValueError(f"{field_name} cannot be null")
        return value

    @field_validator("source_observations")
    @classmethod
    def _require_unique_source_observations(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("source_observations must be unique")
        return value

    @field_validator("valid_from", "valid_to", mode="before")
    @classmethod
    def _reject_numeric_reference_timestamps(cls, value: object) -> object:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("timestamps must be RFC 3339 strings or datetime values")
        return value


class EntityFrontmatter(BaseModel):
    """Shared metadata for canonical Markdown entities."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = Field(strict=True)
    id: str = Field(min_length=1)
    entity_type: EntityType
    title: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    status: str | None = None
    confidence: ConfidenceLevel | None = None
    tags: list[str] = Field(default_factory=list)
    references: list[_ReferencePayload] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime

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

    @field_validator("uri")
    @classmethod
    def _require_workctx_uri(cls, value: str) -> str:
        try:
            return str(WorkctxUri.parse(value))
        except ValueError as exc:
            raise ValueError("uri must be a valid workctx:// URI") from exc

    @field_validator("aliases", "tags")
    @classmethod
    def _require_unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("list values must be unique")
        return value

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _reject_numeric_timestamps(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("timestamps must be RFC 3339 strings or datetime values")
        return value

    @field_serializer("references")
    def _serialize_references(
        self,
        value: list[_ReferencePayload],
    ) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for reference in value:
            payload: dict[str, object] = reference.model_dump(mode="json")
            serialized.append(
                {
                    field_name: field_value
                    for field_name, field_value in payload.items()
                    if field_value is not None or field_name in {"valid_from", "valid_to"}
                }
            )
        return serialized

    @model_validator(mode="after")
    def _require_uri_identity_match(self) -> Self:
        parsed = WorkctxUri.parse(self.uri)
        if parsed.entity_type != self.entity_type:
            raise ValueError("uri entity type must match entity_type")
        if parsed.entity_id != self.id:
            raise ValueError("uri entity ID must match id")
        return self
