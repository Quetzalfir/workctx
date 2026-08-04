"""Typed contracts for machine-local usage telemetry and advisory candidates."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from workctx.domain import validate_durable_reference


class _UsageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UsageEvent(_UsageModel):
    """One compact append-only telemetry event."""

    timestamp: AwareDatetime
    api: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    target_uri: str | None = Field(default=None, min_length=1, max_length=4096)
    query_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("target_uri")
    @classmethod
    def validate_target_uri(cls, value: str | None) -> str | None:
        return None if value is None else validate_durable_reference(value)

    @model_validator(mode="after")
    def require_exactly_one_target(self) -> Self:
        if (self.target_uri is None) == (self.query_sha256 is None):
            raise ValueError("usage events require exactly one URI or query hash")
        return self


class UsageWindowTotals(_UsageModel):
    uses_7d: int = Field(ge=0)
    uses_30d: int = Field(ge=0)
    uses_90d: int = Field(ge=0)


class UsageTargetSummary(UsageWindowTotals):
    target_uri: str
    last_used_at: AwareDatetime

    @field_validator("target_uri")
    @classmethod
    def validate_target_uri(cls, value: str) -> str:
        return validate_durable_reference(value)


class UsageSummary(_UsageModel):
    generated_at: AwareDatetime
    event_count: int = Field(ge=0)
    uri_event_count: int = Field(ge=0)
    query_event_count: int = Field(ge=0)
    corrupt_line_count: int = Field(ge=0)
    totals: UsageWindowTotals
    targets: tuple[UsageTargetSummary, ...] = ()


class UsageStatus(_UsageModel):
    enabled: bool
    path: Literal["98_state/usage/usage.jsonl"] = "98_state/usage/usage.jsonl"
    file_size_bytes: int = Field(ge=0)
    rotated_file_count: int = Field(ge=0)
    rotated_size_bytes: int = Field(ge=0)
    summary: UsageSummary


class UsageCandidateKind(StrEnum):
    PROMOTION = "promotion"
    DECAY = "decay"


class PromotionCandidate(_UsageModel):
    kind: Literal[UsageCandidateKind.PROMOTION] = UsageCandidateKind.PROMOTION
    target_uri: str
    uses_7d: int = Field(ge=0)
    uses_30d: int = Field(ge=0)
    uses_90d: int = Field(ge=0)
    threshold_uses: int = Field(ge=1)
    last_used_at: AwareDatetime

    @field_validator("target_uri")
    @classmethod
    def validate_target_uri(cls, value: str) -> str:
        return validate_durable_reference(value)


class DecayCandidate(_UsageModel):
    kind: Literal[UsageCandidateKind.DECAY] = UsageCandidateKind.DECAY
    target_uri: str
    entity_type: Literal["task", "claim"]
    last_activity_at: AwareDatetime
    inactive_days: int = Field(ge=0)
    threshold_days: int = Field(ge=1)

    @field_validator("target_uri")
    @classmethod
    def validate_target_uri(cls, value: str) -> str:
        return validate_durable_reference(value)


type UsageCandidate = Annotated[
    PromotionCandidate | DecayCandidate,
    Field(discriminator="kind"),
]


__all__ = [
    "DecayCandidate",
    "PromotionCandidate",
    "UsageCandidate",
    "UsageCandidateKind",
    "UsageEvent",
    "UsageStatus",
    "UsageSummary",
    "UsageTargetSummary",
    "UsageWindowTotals",
]
