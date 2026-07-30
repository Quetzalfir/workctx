from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from workctx.domain.references import validate_durable_reference


class RelationType(StrEnum):
    DERIVED_FROM = "derived_from"
    EVIDENCED_BY = "evidenced_by"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    PARENT_OF = "parent_of"
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    REQUESTED_BY = "requested_by"
    OWNED_BY = "owned_by"
    WAITING_ON = "waiting_on"
    PRODUCES = "produces"
    IMPLEMENTS = "implements"
    CALLS = "calls"
    PUBLISHES_TO = "publishes_to"
    CONSUMES_FROM = "consumes_from"
    STORES_IN = "stores_in"
    AUTHENTICATES_VIA = "authenticates_via"
    OPERATED_BY = "operated_by"
    AFFECTS = "affects"
    MENTIONS = "mentions"
    RELATED_TO = "related_to"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _validate_contract_datetime_input(value: object) -> object:
    if isinstance(value, (int, float)):
        raise ValueError("Date-time values must use an RFC 3339 string")
    return value


type ContractDateTime = Annotated[
    AwareDatetime,
    BeforeValidator(_validate_contract_datetime_input),
]


class TypedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation: RelationType
    target: str
    confidence: Confidence | None = Field(default=None, exclude_if=lambda value: value is None)
    source_observations: list[str] = Field(default_factory=list)
    valid_from: ContractDateTime | None = None
    valid_to: ContractDateTime | None = None
    note: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        return validate_durable_reference(value)

    @field_validator("confidence", "note", mode="before")
    @classmethod
    def reject_explicit_null_non_nullable_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("Optional non-null fields must be omitted rather than null")
        return value

    @field_validator("source_observations")
    @classmethod
    def validate_unique_source_observations(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_observations must contain unique values")
        return value
