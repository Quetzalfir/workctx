from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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

    repository: str = Field(default="en", pattern="^en$")
    user_interaction: str = Field(default="en", min_length=2, max_length=16)


class ContextPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_mutations: LocalMutationPolicy = LocalMutationPolicy.REVIEW_REQUIRED
    external_writes: ExternalWritePolicy = ExternalWritePolicy.APPROVAL_REQUIRED
    raw_evidence_retention: EvidenceRetentionPolicy = EvidenceRetentionPolicy.PRESERVE
    federated_search: bool = False


class ContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: ContextKind = ContextKind.PROJECT
    profile: ContextProfile = ContextProfile.HYBRID
    languages: ContextLanguages = Field(default_factory=ContextLanguages)
    timezone: str = Field(default="UTC", min_length=1)
    classification: DataClassification = DataClassification.CONFIDENTIAL
    security_boundary: str = Field(default="isolated", pattern="^isolated$")
    policies: ContextPolicies = Field(default_factory=ContextPolicies)
    created_at: datetime
    updated_at: datetime
