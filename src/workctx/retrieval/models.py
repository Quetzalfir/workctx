"""Typed public contract for deterministic context packs."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from workctx.domain.references import validate_durable_reference, validate_workctx_entity_uri

_CONTEXT_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_SOURCE_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"
_RFC3339_DATE_TIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})$"
)


def _normalize_json_integer(value: object) -> int:
    """Apply JSON Schema's integer semantics without accepting strings or booleans."""

    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ValueError("Value must be a JSON integer")


def _normalize_schema_version(value: object) -> int:
    normalized = _normalize_json_integer(value)
    if normalized != 1:
        raise ValueError("schema_version must be 1")
    return normalized


def _parse_rfc3339_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Date-time values must include a UTC offset")
        return value
    if not isinstance(value, str) or _RFC3339_DATE_TIME.fullmatch(value) is None:
        raise ValueError("Date-time values must use an RFC 3339 string")
    normalized = f"{value[:-1]}+00:00" if value[-1] in {"Z", "z"} else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Date-time values must use an RFC 3339 string") from exc
    if parsed.utcoffset() is None:
        raise ValueError("Date-time values must include a UTC offset")
    return parsed


type NonNegativeJsonInteger = Annotated[
    int,
    BeforeValidator(_normalize_json_integer),
    Field(ge=0),
]
type RankingFactorJsonInteger = Annotated[
    int,
    BeforeValidator(_normalize_json_integer),
    Field(ge=0, le=100),
]
type RankTotalJsonInteger = Annotated[
    int,
    BeforeValidator(_normalize_json_integer),
    Field(ge=0, le=10_000),
]
type SchemaVersion = Annotated[
    Literal[1],
    BeforeValidator(_normalize_schema_version),
]
type ContractAwareDatetime = Annotated[
    AwareDatetime,
    BeforeValidator(_parse_rfc3339_datetime),
]


class _StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PackItemKind(StrEnum):
    """Controlled item vocabulary covering canonical entities and retrieval-only records."""

    EVIDENCE = "evidence"
    PERSON = "person"
    TEAM = "team"
    PROJECT = "project"
    SYSTEM = "system"
    SERVICE = "service"
    MODULE = "module"
    FLOW = "flow"
    INTEGRATION = "integration"
    DECISION = "decision"
    RISK = "risk"
    QUESTION = "question"
    TASK = "task"
    CLAIM = "claim"
    DRAFT = "draft"
    INVESTIGATION = "investigation"
    INCIDENT = "incident"
    OBSERVATION = "observation"
    ARTIFACT = "artifact"
    RELATIONSHIP = "relationship"
    DEPENDENCY = "dependency"
    INTERACTION = "interaction"
    STATUS_HISTORY = "status_history"


class RankMetadata(_StrictContractModel):
    """Deterministic integer contributions from every doc-03 ranking factor."""

    relation_semantics: RankingFactorJsonInteger
    recency: RankingFactorJsonInteger
    current_state: RankingFactorJsonInteger
    confidence: RankingFactorJsonInteger
    directness: RankingFactorJsonInteger
    query_match: RankingFactorJsonInteger
    entity_importance: RankingFactorJsonInteger
    source_quality: RankingFactorJsonInteger
    total: RankTotalJsonInteger


class PackItem(_StrictContractModel):
    id: str = Field(min_length=1)
    kind: PackItemKind
    uri: str | None
    title: str = Field(min_length=1)
    summary: str
    data: dict[str, JsonValue]
    rank: RankMetadata | None

    @field_validator("kind", mode="before")
    @classmethod
    def parse_kind(cls, value: object) -> object:
        if type(value) is str:
            return PackItemKind(value)
        return value

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_durable_reference(value)


class PackSection(_StrictContractModel):
    items: list[PackItem]
    omitted_count: NonNegativeJsonInteger


class PackSectionName(StrEnum):
    FOCAL_ENTITY = "focal_entity"
    CLAIMS_AND_STATUS_HISTORY = "claims_and_status_history"
    DIRECT_RELATIONSHIPS = "direct_relationships"
    SOURCE_OBSERVATIONS = "source_observations"
    RELATED_TASKS_AND_DEPENDENCIES = "related_tasks_and_dependencies"
    PEOPLE_AND_INTERACTIONS = "people_and_interactions"
    DECISIONS_RISKS_AND_QUESTIONS = "decisions_risks_and_questions"
    CONTRADICTORY_OR_SUPERSEDING_EVIDENCE = "contradictory_or_superseding_evidence"
    ARCHITECTURE_ENTITIES = "architecture_entities"


class OmittedItem(_StrictContractModel):
    section: PackSectionName
    item_id: str = Field(min_length=1)
    units: NonNegativeJsonInteger
    reason: str = Field(min_length=1)

    @field_validator("section", mode="before")
    @classmethod
    def parse_section(cls, value: object) -> object:
        if type(value) is str:
            return PackSectionName(value)
        return value


class BudgetAndTruncation(_StrictContractModel):
    requested_units: NonNegativeJsonInteger
    used_units: NonNegativeJsonInteger
    minimum_units: NonNegativeJsonInteger
    unit: Literal["approx_tokens_chars_div_4"]
    truncated: bool
    within_budget: bool
    over_budget_by: NonNegativeJsonInteger
    omitted_count: NonNegativeJsonInteger
    omitted_items: list[OmittedItem]

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.minimum_units > self.used_units:
            raise ValueError("minimum_units cannot exceed used_units")

        expected_within_budget = self.used_units <= self.requested_units
        if self.within_budget is not expected_within_budget:
            raise ValueError("within_budget must reflect used_units and requested_units")

        expected_overage = max(0, self.used_units - self.requested_units)
        if self.over_budget_by != expected_overage:
            raise ValueError("over_budget_by must equal used_units minus requested_units")

        if self.omitted_count != len(self.omitted_items):
            raise ValueError("omitted_count must equal the number of omitted_items")

        expected_truncated = self.omitted_count > 0
        if self.truncated is not expected_truncated:
            raise ValueError("truncated must reflect whether any items were omitted")
        return self


class ContextPackSections(_StrictContractModel):
    focal_entity: PackSection
    claims_and_status_history: PackSection
    direct_relationships: PackSection
    source_observations: PackSection
    related_tasks_and_dependencies: PackSection
    people_and_interactions: PackSection
    decisions_risks_and_questions: PackSection
    contradictory_or_superseding_evidence: PackSection
    architecture_entities: PackSection
    budget_and_truncation: BudgetAndTruncation

    @model_validator(mode="after")
    def validate_section_counts(self) -> Self:
        if len(self.focal_entity.items) != 1:
            raise ValueError("focal_entity must contain exactly one item")

        sections = self._item_sections()
        omitted_by_section = Counter(
            item.section for item in self.budget_and_truncation.omitted_items
        )
        for name, section in sections.items():
            if section.omitted_count != omitted_by_section[name]:
                raise ValueError(f"{name.value}.omitted_count must match omitted_items metadata")

        if sum(section.omitted_count for section in sections.values()) != (
            self.budget_and_truncation.omitted_count
        ):
            raise ValueError("Section omitted counts must equal the total omitted_count")
        return self

    def _item_sections(self) -> dict[PackSectionName, PackSection]:
        return {
            PackSectionName.FOCAL_ENTITY: self.focal_entity,
            PackSectionName.CLAIMS_AND_STATUS_HISTORY: self.claims_and_status_history,
            PackSectionName.DIRECT_RELATIONSHIPS: self.direct_relationships,
            PackSectionName.SOURCE_OBSERVATIONS: self.source_observations,
            PackSectionName.RELATED_TASKS_AND_DEPENDENCIES: (self.related_tasks_and_dependencies),
            PackSectionName.PEOPLE_AND_INTERACTIONS: self.people_and_interactions,
            PackSectionName.DECISIONS_RISKS_AND_QUESTIONS: (self.decisions_risks_and_questions),
            PackSectionName.CONTRADICTORY_OR_SUPERSEDING_EVIDENCE: (
                self.contradictory_or_superseding_evidence
            ),
            PackSectionName.ARCHITECTURE_ENTITIES: self.architecture_entities,
        }


class ContextPack(_StrictContractModel):
    schema_version: SchemaVersion
    context_id: str = Field(
        min_length=2,
        max_length=64,
        pattern=_CONTEXT_ID_PATTERN,
    )
    focal_uri: str
    source_updated_at: ContractAwareDatetime
    source_fingerprint: str = Field(pattern=_SOURCE_FINGERPRINT_PATTERN)
    query: str | None
    include_history: bool
    include_architecture: bool
    sections: ContextPackSections

    @field_validator("focal_uri")
    @classmethod
    def validate_focal_uri(cls, value: str) -> str:
        return str(validate_workctx_entity_uri(value))

    @model_validator(mode="after")
    def validate_focal_context(self) -> Self:
        focal_uri = validate_workctx_entity_uri(self.focal_uri)
        try:
            focal_uri.require_context(self.context_id)
        except ValueError as exc:
            raise ValueError("focal_uri must belong to context_id") from exc
        if len(self.sections.focal_entity.items) != 1:
            raise ValueError("focal_entity must contain exactly one item")
        return self


__all__ = [
    "BudgetAndTruncation",
    "ContextPack",
    "ContextPackSections",
    "NonNegativeJsonInteger",
    "OmittedItem",
    "PackItem",
    "PackItemKind",
    "PackSection",
    "PackSectionName",
    "RankMetadata",
    "RankTotalJsonInteger",
    "RankingFactorJsonInteger",
]
