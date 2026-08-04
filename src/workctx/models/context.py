from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

CURRENT_SCHEMA_VERSION = 1


class ContextKind(StrEnum):
    COMPANY = "company"
    PROJECT = "project"
    PRODUCT = "product"
    PERSONAL = "personal"
    LABORATORY = "laboratory"


class ContextProfile(StrEnum):
    LIGHT = "light"
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    HYBRID = "hybrid"


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class LocalMutationPolicy(StrEnum):
    REVIEW_REQUIRED = "review_required"
    ALLOWED_AFTER_VALIDATION = "allowed_after_validation"


class ExternalWritePolicy(StrEnum):
    NEVER = "never"
    APPROVAL_REQUIRED = "approval_required"


class EvidenceRetentionPolicy(StrEnum):
    PRESERVE = "preserve"
    PRESERVE_WITH_REDACTION = "preserve_with_redaction"


class ContextLanguages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(pattern="^en$")
    user_interaction: str = Field(min_length=2, max_length=16)


class ContextPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_mutations: LocalMutationPolicy
    external_writes: ExternalWritePolicy
    raw_evidence_retention: EvidenceRetentionPolicy
    federated_search: bool

    @field_validator("federated_search", mode="before")
    @classmethod
    def _require_isolated_search(cls, value: object) -> bool:
        if value is not False:
            raise ValueError("federated_search must remain false for an isolated context")
        return False


class TelemetryConfig(BaseModel):
    """Opt-in machine-local usage telemetry knobs (D-045: default off)."""

    model_config = ConfigDict(extra="forbid")

    usage: bool = False
    promotion_uses: int = Field(default=5, ge=1, le=1000)
    decay_days: int = Field(default=60, ge=1, le=3650)


class ContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(strict=True)
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: ContextKind
    profile: ContextProfile
    languages: ContextLanguages
    timezone: str = Field(min_length=1)
    classification: DataClassification
    security_boundary: str = Field(pattern="^isolated$")
    policies: ContextPolicies
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
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

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _reject_numeric_timestamps(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("timestamps must be RFC 3339 strings or datetime values")
        return value
