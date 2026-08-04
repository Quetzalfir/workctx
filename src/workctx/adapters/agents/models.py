"""Typed public values shared by agent-adapter operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

type McpConfigurationPath = Literal[
    ".codex/config.toml",
    ".mcp.json",
    ".gemini/settings.json",
]


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
    GENERATED = "generated"
    NATIVE = "native"
    DIVERGENT = "divergent"


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
    MCP_DIVERGENT = "mcp_divergent"
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


class PersonalizationLayerName(StrEnum):
    """The two fixed user-owned personalization layer locations."""

    USER = "user"
    CONTEXT = "context"


class SkillOverrideWarningCode(StrEnum):
    """Stable warning kinds emitted by per-context skill override discovery."""

    UNKNOWN_SKILL = "unknown_skill"
    OLDER_PACKAGED_SKILL = "older_packaged_skill"


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
class PersonalizationLayerStatus:
    """Presence and merge state for one fixed personalization layer."""

    layer: PersonalizationLayerName
    path: str
    present: bool
    size_bytes: int | None
    merged: bool

    def __post_init__(self) -> None:
        if self.present != (self.size_bytes is not None):
            raise ValueError("A present personalization layer must report its byte size")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("Personalization layer byte size cannot be negative")
        if self.merged and not self.present:
            raise ValueError("An absent personalization layer cannot be merged")


@dataclass(frozen=True, slots=True)
class SkillOverrideStatus:
    """One safely observed per-context skill override and its three-way hashes."""

    skill: str
    path: str
    size_bytes: int
    override_hash: str
    known: bool
    packaged_at_adoption_hash: str | None = None
    packaged_now_hash: str | None = None

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError("Skill override byte size cannot be negative")
        if self.known != (
            self.packaged_at_adoption_hash is not None and self.packaged_now_hash is not None
        ):
            raise ValueError("Known skill overrides must carry both packaged hashes")

    @property
    def stale(self) -> bool:
        """Return whether the packaged skill changed after override adoption."""

        return bool(self.known and self.packaged_at_adoption_hash != self.packaged_now_hash)


@dataclass(frozen=True, slots=True)
class SkillOverrideWarning:
    """Typed non-blocking warning for an unknown or stale skill override."""

    code: SkillOverrideWarningCode
    skill: str
    path: str
    override_hash: str
    packaged_at_adoption_hash: str | None = None
    packaged_now_hash: str | None = None

    def __post_init__(self) -> None:
        has_three_way_hashes = (
            self.packaged_at_adoption_hash is not None and self.packaged_now_hash is not None
        )
        if self.code is SkillOverrideWarningCode.OLDER_PACKAGED_SKILL:
            if not has_three_way_hashes:
                raise ValueError("Stale override warnings require all three hashes")
        elif has_three_way_hashes:
            raise ValueError("Unknown-skill warnings cannot claim packaged hashes")

    def __str__(self) -> str:
        if self.code is SkillOverrideWarningCode.UNKNOWN_SKILL:
            return (
                "Unknown skill override ignored: "
                f"skill={self.skill}; path={self.path}; override={self.override_hash}"
            )
        return (
            "override written against an older packaged skill: "
            f"skill={self.skill}; path={self.path}; "
            f"packaged-at-adoption={self.packaged_at_adoption_hash}; "
            f"packaged-now={self.packaged_now_hash}; override={self.override_hash}"
        )


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
            FeatureState.MISSING,
            detail="Project-scoped MCP configuration is absent.",
        )
    )
    warnings: tuple[str, ...] = ()
    repair_blocked: bool = False
    personalization_layers: tuple[PersonalizationLayerStatus, ...] = ()
    skill_overrides: tuple[SkillOverrideStatus, ...] = ()
    skill_override_warnings: tuple[SkillOverrideWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One project file change or inert external-source verification in a dry-run plan."""

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
    personalization_layers: tuple[PersonalizationLayerStatus, ...] = ()
    skill_overrides: tuple[SkillOverrideStatus, ...] = ()

    @property
    def requires_approval(self) -> bool:
        return any(change.requires_approval for change in self.changes)

    @property
    def is_noop(self) -> bool:
        return (
            not any(change.operation is not FileOperation.VERIFY for change in self.changes)
            and self.blocked_reason is None
        )


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
