"""Typed public values shared by agent-adapter operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class AgentClient(StrEnum):
    """Supported project-local agent clients."""

    CODEX = "codex"
    CLAUDE = "claude"
    GEMINI = "gemini"


class ClientAvailability(StrEnum):
    """Executable/configuration state discovered without reading configuration."""

    AVAILABLE = "available"
    CONFIGURED_ONLY = "configured_only"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


class FeatureState(StrEnum):
    """Status of one independently owned adapter component."""

    CURRENT = "current"
    MISSING = "missing"
    DIVERGED = "diverged"
    NOT_IMPLEMENTED = "not_implemented"


class AdapterState(StrEnum):
    """Aggregate state of one client adapter."""

    INVALID = "invalid"
    BUSY = "busy"
    RECOVERY_REQUIRED = "recovery_required"
    UNSUPPORTED = "unsupported"
    NOT_INSTALLED = "not_installed"
    CONFLICT = "conflict"
    STALE = "stale"
    CURRENT = "current"


class DriftReason(StrEnum):
    """Stable machine-readable drift classifications."""

    LEGACY_MANIFEST = "legacy_manifest"
    ADAPTER_VERSION_CHANGED = "adapter_version_changed"
    REGISTRY_CHANGED = "registry_changed"
    REGISTRY_MISSING = "registry_missing"
    REGISTRY_INVALID = "registry_invalid"
    INVENTORY_CHANGED = "inventory_changed"
    SOURCE_CHANGED = "source_changed"
    SOURCE_MISSING = "source_missing"
    SOURCE_INVALID = "source_invalid"
    TARGET_SET_CHANGED = "target_set_changed"
    GENERATED_MISSING = "generated_missing"
    GENERATED_MODIFIED = "generated_modified"
    BRIDGE_DIVERGED = "bridge_diverged"
    BACKUP_MISSING = "backup_missing"
    BACKUP_MODIFIED = "backup_modified"
    ORPHAN_STAGING = "orphan_staging"


class OperationAction(StrEnum):
    """High-level planned adapter operation."""

    INSTALL = "install"
    REPAIR = "repair"
    UNINSTALL = "uninstall"
    RECOVER = "recover"


class FileOperation(StrEnum):
    """One exact filesystem transition."""

    CREATE = "create"
    REPLACE = "replace"
    DELETE = "delete"
    VERIFY = "verify"
    PRESERVE = "preserve"


class SourceOrigin(StrEnum):
    """Origin selected for canonical skill inputs."""

    LOCAL = "local"
    PACKAGED = "packaged"


@dataclass(frozen=True, order=True, slots=True)
class SemanticVersion:
    """Minimal semantic version used for client capability checks."""

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        if min(self.major, self.minor, self.patch) < 0:
            raise ValueError("semantic version components must be non-negative")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class SupportedVersionRange:
    """Inclusive lower and exclusive upper supported client version bounds."""

    minimum: SemanticVersion
    maximum_exclusive: SemanticVersion

    def contains(self, version: SemanticVersion) -> bool:
        return self.minimum <= version < self.maximum_exclusive

    def __str__(self) -> str:
        return f">={self.minimum},<{self.maximum_exclusive}"


@dataclass(frozen=True, slots=True)
class ClientCapability:
    """Read-only capability report for one client."""

    client: AgentClient
    availability: ClientAvailability
    executable: str | None
    version: SemanticVersion | None
    supported_range: SupportedVersionRange
    project_markers: tuple[str, ...] = ()
    detail: str | None = None

    @property
    def can_open(self) -> bool:
        return self.availability is ClientAvailability.AVAILABLE


@dataclass(frozen=True, slots=True)
class FeatureStatus:
    """Status for a bridge or deferred integration component."""

    state: FeatureState
    path: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DriftDetail:
    """One exact drift observation."""

    reason: DriftReason
    path: str | None = None
    skill: str | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterStatus:
    """Aggregate, derived status for one installed client adapter."""

    client: AgentClient
    state: AdapterState
    manifest_path: str
    drift: tuple[DriftDetail, ...] = ()
    instruction_bridge: FeatureStatus = field(
        default_factory=lambda: FeatureStatus(FeatureState.MISSING)
    )
    mcp_configuration: FeatureStatus = field(
        default_factory=lambda: FeatureStatus(
            FeatureState.NOT_IMPLEMENTED,
            detail="Deferred until WP-330; no MCP server identity is configured.",
        )
    )
    warnings: tuple[str, ...] = ()
    repair_blocked: bool = False


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One project-relative file change in a dry-run plan."""

    path: str
    operation: FileOperation
    observed_hash: str | None = None
    desired_hash: str | None = None
    requires_approval: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TargetApproval:
    """Exact approval bound to an observed and optionally desired file hash."""

    path: str
    operation: FileOperation
    observed_hash: str
    desired_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterPlan:
    """Immutable dry-run plan; mutations must revalidate every precondition."""

    root: Path
    client: AgentClient
    action: OperationAction
    changes: tuple[PlannedChange, ...]
    plan_hash: str
    source_fingerprint: str | None = None
    blocked_reason: str | None = None

    @property
    def requires_approval(self) -> bool:
        return any(change.requires_approval for change in self.changes)

    @property
    def is_noop(self) -> bool:
        return not self.changes and self.blocked_reason is None


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Result of an applied or no-op plan."""

    root: Path
    client: AgentClient
    action: OperationAction
    changed_paths: tuple[str, ...]
    backups: tuple[str, ...] = ()
    no_op: bool = False


@dataclass(frozen=True, slots=True)
class OpenedContext:
    """A spawned agent process associated with a context root."""

    client: AgentClient
    root: Path
    executable: str
    pid: int
