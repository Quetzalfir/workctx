"""Frozen application records returned by retrieval APIs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from workctx.adapters.sqlite import (
    ClaimRecord,
    EdgeRecord,
    EntityRecord,
    ObservationRecord,
    TaskRecord,
)
from workctx.domain import (
    ArtifactReference,
    RelationType,
    RepoReference,
    SourceLocator,
    WorkctxUri,
)

type DocumentRecord = EntityRecord | TaskRecord | ClaimRecord | ObservationRecord


class ResolutionStatus(StrEnum):
    """Whether a syntactically valid reference resolved to a projected document."""

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"


class ReferenceKind(StrEnum):
    """Reference families understood by the deterministic resolver."""

    WORKCTX = "workctx"
    ARTIFACT = "artifact"
    REPOSITORY = "repository"


@dataclass(frozen=True, slots=True)
class WorkctxReferenceDescriptor:
    """Structural components of a canonical Work Context URI."""

    uri: WorkctxUri
    kind: ReferenceKind = ReferenceKind.WORKCTX

    @property
    def context_id(self) -> str:
        return self.uri.context_id

    @property
    def entity_type(self) -> str:
        return self.uri.entity_type

    @property
    def entity_id(self) -> str:
        return self.uri.entity_id

    @property
    def reference(self) -> str:
        return str(self.uri)


@dataclass(frozen=True, slots=True)
class ArtifactReferenceDescriptor:
    """Structural components of an immutable artifact reference."""

    digest: str
    algorithm: str = "sha256"
    kind: ReferenceKind = ReferenceKind.ARTIFACT

    @classmethod
    def from_reference(cls, reference: ArtifactReference) -> ArtifactReferenceDescriptor:
        return cls(digest=reference.digest)

    @property
    def reference(self) -> str:
        return f"artifact://{self.algorithm}/{self.digest}"


@dataclass(frozen=True, slots=True)
class RepoReferenceDescriptor:
    """Structural components of an immutable repository source reference."""

    repo_id: str
    commit: str
    path: str
    start_line: int
    end_line: int
    kind: ReferenceKind = ReferenceKind.REPOSITORY

    @classmethod
    def from_reference(cls, reference: RepoReference) -> RepoReferenceDescriptor:
        return cls(
            repo_id=reference.repo_id,
            commit=reference.commit,
            path=reference.path,
            start_line=reference.start_line,
            end_line=reference.end_line,
        )

    @property
    def reference(self) -> str:
        return str(
            RepoReference(
                repo_id=self.repo_id,
                commit=self.commit,
                path=self.path,
                start_line=self.start_line,
                end_line=self.end_line,
            )
        )


type ReferenceDescriptor = (
    WorkctxReferenceDescriptor | ArtifactReferenceDescriptor | RepoReferenceDescriptor
)


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Resolution result with an explicit not-found state."""

    status: ResolutionStatus
    descriptor: ReferenceDescriptor
    record: DocumentRecord | None = None

    @property
    def reference(self) -> str:
        return self.descriptor.reference

    @property
    def found(self) -> bool:
        return self.status is ResolutionStatus.RESOLVED


class TraversalDirection(StrEnum):
    """Requested directions for related-entity traversal."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"
    BOTH = "both"


class EdgeDirection(StrEnum):
    """Direction in which one stored edge was encountered."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"


@dataclass(frozen=True, slots=True)
class RelatedNode:
    """One unique reference discovered by breadth-first traversal."""

    depth: int
    resolution: ResolutionResult

    @property
    def reference(self) -> str:
        return self.resolution.reference

    @property
    def record(self) -> DocumentRecord | None:
        return self.resolution.record


@dataclass(frozen=True, slots=True)
class TraversedEdge:
    """One unique typed edge encountered during traversal."""

    depth: int
    direction: EdgeDirection
    edge: EdgeRecord


@dataclass(frozen=True, slots=True)
class RelatedResult:
    """Deterministic bounded traversal result."""

    focal: ResolutionResult
    max_depth: int
    direction: TraversalDirection
    relations: frozenset[RelationType] | None
    nodes: tuple[RelatedNode, ...]
    edges: tuple[TraversedEdge, ...]


class MissingObservationReason(StrEnum):
    """Stable reasons an authored source-observation reference could not be followed."""

    NOT_FOUND = "not_found"
    INVALID_REFERENCE = "invalid_reference"
    CONTEXT_BOUNDARY = "context_boundary"


@dataclass(frozen=True, slots=True)
class TracedObservation:
    """An exact projected observation and the records that referred to it."""

    observation: ObservationRecord
    referenced_by: tuple[str, ...]

    @property
    def source_ref(self) -> ArtifactReference:
        return self.observation.source_ref

    @property
    def locator(self) -> SourceLocator:
        return self.observation.locator


@dataclass(frozen=True, slots=True)
class MissingObservation:
    """A missing source reference without an adapter exception or source-content leak."""

    reference: str
    reason: MissingObservationReason
    referenced_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TraceResult:
    """Claim and observation provenance for one focal reference."""

    focal: ResolutionResult
    include_history: bool
    claims: tuple[ClaimRecord, ...]
    observations: tuple[TracedObservation, ...]
    missing_observations: tuple[MissingObservation, ...]
