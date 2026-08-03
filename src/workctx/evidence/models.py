"""Typed values exchanged across the deterministic evidence workflow."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from workctx.domain import (
    ArtifactManifest,
    Claim,
    Confidence,
    EntityFrontmatter,
    EvidenceId,
    Observation,
    RelationType,
    Task,
    TypedReference,
)
from workctx.domain.relations import ContractDateTime
from workctx.domain.transactions import Actor
from workctx.retrieval import ContextPack

_CONTENT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceContentDescriptor(_EvidenceRecord):
    """Content-free pointer to preserved evidence bytes."""

    path: str = Field(min_length=1)
    content_hash: str = Field(pattern=_CONTENT_HASH_PATTERN)
    media_type: str = Field(min_length=1)


class CandidateContextPack(_EvidenceRecord):
    """One bounded context pack matched from artifact metadata."""

    candidate: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    pack: ContextPack


class ObservationSchemaExpectations(_EvidenceRecord):
    """Agent-facing observation contract bound to one immutable artifact."""

    source_ref: str = Field(min_length=1)
    id_shape: Literal["<EVD-ID>#OBS-NNN"] = "<EVD-ID>#OBS-NNN"
    observation_kinds: tuple[str, ...]
    locator_types: tuple[str, ...]
    json_schema: dict[str, JsonValue]


class ProcessingPacket(_EvidenceRecord):
    """Safe, content-free input for agent-side evidence inspection."""

    schema_version: Literal[1] = 1
    context_id: str = Field(min_length=1)
    manifest_path: str = Field(min_length=1)
    manifest: ArtifactManifest
    artifact_ref: str = Field(min_length=1)
    content: EvidenceContentDescriptor
    context_packs: tuple[CandidateContextPack, ...] = ()
    unresolved_candidates: tuple[str, ...] = ()
    observation_expectations: ObservationSchemaExpectations


class EvidenceNoteDraft(_EvidenceRecord):
    """Agent-authored evidence-note metadata and Markdown body."""

    id: str
    title: str = Field(min_length=1)
    body: str = ""
    aliases: tuple[str, ...] = ()
    status: str = "active"
    confidence: Confidence = Confidence.HIGH
    tags: tuple[str, ...] = ()
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        return str(EvidenceId.parse(value))

    @field_validator("aliases", "tags")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("values must be unique")
        return values


class ProposedDocument(_EvidenceRecord):
    """An agent-authored document awaiting domain validation and resolution."""

    document: dict[str, JsonValue]
    body: str = ""


class ProposedRelation(_EvidenceRecord):
    """An outbound typed relation whose endpoints may use aliases or IDs."""

    source: str = Field(min_length=1)
    relation: RelationType
    target: str = Field(min_length=1)
    confidence: Confidence | None = None
    source_observations: tuple[str, ...] = Field(min_length=1)
    valid_from: ContractDateTime | None = None
    valid_to: ContractDateTime | None = None
    note: str | None = None

    @field_validator("source_observations")
    @classmethod
    def validate_unique_sources(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("source_observations must be unique")
        return values


class EvidenceStagingPayload(_EvidenceRecord):
    """Closed input accepted from an extracting agent."""

    schema_version: Literal[1] = 1
    actor: Actor
    source_refs: tuple[str, ...] = Field(min_length=1)
    evidence_note: EvidenceNoteDraft
    observations: tuple[dict[str, JsonValue], ...] = Field(min_length=1)
    new_entities: tuple[ProposedDocument, ...] = ()
    tasks: tuple[ProposedDocument, ...] = ()
    claims: tuple[ProposedDocument, ...] = ()
    relations: tuple[ProposedRelation, ...] = ()

    @field_validator("source_refs")
    @classmethod
    def validate_unique_source_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("source_refs must be unique")
        return values


class ResolvedEntityReference(_EvidenceRecord):
    authored: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    declared: bool


class ResolvedRelation(_EvidenceRecord):
    source_uri: str = Field(min_length=1)
    reference: TypedReference


class StagedEntityDocument(_EvidenceRecord):
    operation: Literal["create", "update"]
    target: str = Field(min_length=1)
    document: EntityFrontmatter
    body: str = ""
    expected_hash: str | None = Field(default=None, pattern=_CONTENT_HASH_PATTERN)

    @model_validator(mode="after")
    def validate_expected_hash(self) -> Self:
        if (self.operation == "update") != (self.expected_hash is not None):
            raise ValueError("only update operations require expected_hash")
        return self


class StagedTaskDocument(_EvidenceRecord):
    operation: Literal["create", "update"]
    target: str = Field(min_length=1)
    document: Task
    body: str = ""
    expected_hash: str | None = Field(default=None, pattern=_CONTENT_HASH_PATTERN)

    @model_validator(mode="after")
    def validate_expected_hash(self) -> Self:
        if (self.operation == "update") != (self.expected_hash is not None):
            raise ValueError("only update operations require expected_hash")
        return self


class StagedClaimDocument(_EvidenceRecord):
    target: str = Field(min_length=1)
    document: Claim
    body: str = ""


class EvidenceStagingResult(_EvidenceRecord):
    """Fully resolved, domain-valid material ready for proposal construction."""

    schema_version: Literal[1] = 1
    context_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_ref: str = Field(min_length=1)
    base_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str = Field(min_length=1)
    created_at: AwareDatetime
    actor: Actor
    evidence_note: StagedEntityDocument
    entity_documents: tuple[StagedEntityDocument, ...] = ()
    task_documents: tuple[StagedTaskDocument, ...] = ()
    claim_documents: tuple[StagedClaimDocument, ...] = ()
    observations: tuple[Observation, ...]
    relations: tuple[ResolvedRelation, ...] = ()
    resolutions: tuple[ResolvedEntityReference, ...] = ()


__all__ = [
    "CandidateContextPack",
    "EvidenceContentDescriptor",
    "EvidenceNoteDraft",
    "EvidenceStagingPayload",
    "EvidenceStagingResult",
    "ObservationSchemaExpectations",
    "ProcessingPacket",
    "ProposedDocument",
    "ProposedRelation",
    "ResolvedEntityReference",
    "ResolvedRelation",
    "StagedClaimDocument",
    "StagedEntityDocument",
    "StagedTaskDocument",
]
