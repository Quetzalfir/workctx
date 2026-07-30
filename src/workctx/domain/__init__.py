"""Domain core: identities, references, entities, and policies.

Public re-exports consolidated by the implementation lead at Wave 1 close.
File-level path ownership during a wave is assigned per work order; see
.agents/status/path-ownership.json.
"""

from workctx.domain.artifacts import ArtifactManifest, ArtifactSourceType, ArtifactStatus
from workctx.domain.claims import Claim, ClaimStatus
from workctx.domain.entities import EntityFrontmatter
from workctx.domain.ids import (
    ArtifactId,
    ClaimId,
    DecisionId,
    EvidenceId,
    ObservationId,
    PersonId,
    QuestionId,
    RiskId,
    StableId,
    SubtaskId,
    SystemId,
    TaskId,
)
from workctx.domain.locators import (
    SOURCE_LOCATOR_ADAPTER,
    SourceLocator,
    parse_source_locator,
)
from workctx.domain.observations import Observation, ObservationKind, ObservationSource
from workctx.domain.references import (
    ArtifactReference,
    DurableReference,
    RepoReference,
    SourceReference,
    WorkctxUri,
    normalize_workctx_uri,
    parse_durable_reference,
    parse_source_reference,
    validate_durable_reference,
    validate_workctx_entity_uri,
)
from workctx.domain.relations import Confidence, RelationType, TypedReference
from workctx.domain.tasks import (
    Task,
    TaskHierarchyError,
    TaskPriority,
    TaskStatus,
    TaskType,
    validate_task_hierarchy,
)
from workctx.domain.vocabulary import EntityType

__all__ = [
    "SOURCE_LOCATOR_ADAPTER",
    "ArtifactId",
    "ArtifactManifest",
    "ArtifactReference",
    "ArtifactSourceType",
    "ArtifactStatus",
    "Claim",
    "ClaimId",
    "ClaimStatus",
    "Confidence",
    "DecisionId",
    "DurableReference",
    "EntityFrontmatter",
    "EntityType",
    "EvidenceId",
    "Observation",
    "ObservationId",
    "ObservationKind",
    "ObservationSource",
    "PersonId",
    "QuestionId",
    "RelationType",
    "RepoReference",
    "RiskId",
    "SourceLocator",
    "SourceReference",
    "StableId",
    "SubtaskId",
    "SystemId",
    "Task",
    "TaskHierarchyError",
    "TaskId",
    "TaskPriority",
    "TaskStatus",
    "TaskType",
    "TypedReference",
    "WorkctxUri",
    "normalize_workctx_uri",
    "parse_durable_reference",
    "parse_source_locator",
    "parse_source_reference",
    "validate_durable_reference",
    "validate_task_hierarchy",
    "validate_workctx_entity_uri",
]
