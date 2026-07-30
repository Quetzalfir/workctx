from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from workctx.domain.ids import ObservationId
from workctx.domain.locators import SourceLocator
from workctx.domain.references import ArtifactReference
from workctx.domain.relations import Confidence, ContractDateTime, TypedReference


class ObservationKind(StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    DECISION = "decision"
    COMMITMENT = "commitment"
    TASK = "task"
    RISK = "risk"
    BLOCKER = "blocker"
    DEPENDENCY = "dependency"
    QUESTION = "question"


class ObservationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    locator: SourceLocator

    @field_validator("ref")
    @classmethod
    def validate_artifact_reference(cls, value: str) -> str:
        return str(ArtifactReference.parse(value))


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: ObservationKind
    statement: str = Field(min_length=1)
    confidence: Confidence
    source: ObservationSource
    observed_at: ContractDateTime | None = None
    valid_from: ContractDateTime | None = None
    valid_to: ContractDateTime | None = None
    derived_from: list[str] = Field(default_factory=list)
    related: list[TypedReference] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_observation_id(cls, value: str) -> str:
        return str(ObservationId.parse(value))

    @field_validator("derived_from")
    @classmethod
    def validate_unique_derived_from(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("derived_from must contain unique values")
        return value
