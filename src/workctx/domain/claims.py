from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from workctx.domain.ids import ClaimId, ObservationId
from workctx.domain.references import WorkctxUri, validate_workctx_entity_uri
from workctx.domain.relations import Confidence, ContractDateTime


class ClaimStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    UNCERTAIN = "uncertain"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    id: str
    subject: str
    predicate: str = Field(min_length=1)
    object: JsonValue
    observed_at: ContractDateTime
    valid_from: ContractDateTime | None = None
    valid_to: ContractDateTime | None = None
    status: ClaimStatus
    supersedes: str | None = None
    superseded_by: str | None = None
    confidence: Confidence
    source_observations: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def validate_claim_id(cls, value: str) -> str:
        return str(ClaimId.parse(value))

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        return str(validate_workctx_entity_uri(value))

    @field_validator("supersedes", "superseded_by")
    @classmethod
    def validate_related_claim_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(ClaimId.parse(value))

    @field_validator("source_observations")
    @classmethod
    def validate_source_observations(cls, value: list[str]) -> list[str]:
        canonical: list[str] = []
        for item in value:
            uri = WorkctxUri.parse(item)
            if uri.entity_type != "observation":
                raise ValueError("source_observations must reference observation entities")
            ObservationId.parse(uri.entity_id)
            canonical.append(str(uri))
        if len(canonical) != len(set(canonical)):
            raise ValueError("source_observations must contain unique canonical values")
        return canonical
