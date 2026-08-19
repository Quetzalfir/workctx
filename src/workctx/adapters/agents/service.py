"""Typed install, status, targeted repair, recovery, and safe uninstall APIs."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from pydantic import ValidationError

from workctx.errors import UnavailableDependencyError

from ._install_records import (
    InstallRecordConflictError,
    InstallRecordError,
    InstallRecordObservation,
    TrustedInstallStore,
)
from ._lock import AdapterLock, inspect_adapter_lock
from ._safe_fs import (
    FileSnapshot,
    SafeFilesystemError,
    SafeRoot,
    is_credential_capable_path,
)
from ._transaction import (
    AtomicAdapterTransaction,
    FileMutation,
    inspect_recovery_transition,
    inspect_transactions,
    mutation_operations_digest,
    recover_transaction,
)
from .detection import (
    ClientDetector,
    ExecutableFinder,
    VersionProbe,
    _default_version_probe,
)
from .errors import (
    AdapterConflictError,
    InvalidAdapterStateError,
    InvalidApprovalError,
    RecoveryConflictError,
    RecoveryRequiredError,
    UnsupportedClientVersionError,
)
from .layout import InstallationLayout, derive_layout
from .manifest import (
    AdapterComponents,
    AdapterManifest,
    BackupEntry,
    BridgeSource,
    BridgeTarget,
    CanonicalSource,
    GeneratedFile,
    InstructionBridgeComponent,
    McpConfigurationComponent,
    NativeSourceFile,
    NativeSourceSet,
    RegistrySource,
    SkillAdapterEntry,
    dump_manifest_bytes,
    load_manifest,
    source_set_aggregate_hash,
)
from .models import (
    AdapterPlan,
    AdapterState,
    AdapterStatus,
    AgentClient,
    ClientAvailability,
    ClientCapability,
    DriftDetail,
    DriftReason,
    FeatureState,
    FeatureStatus,
    FileOperation,
    ManagedFileMerge,
    OpenedContext,
    OperationAction,
    OperationResult,
    PersonalizationLayerStatus,
    PlannedChange,
    SkillOverrideStatus,
    SourceOrigin,
    TargetApproval,
)
from .overrides import (
    SkillOverrides,
    skill_override_status_message,
)
from .personalization import (
    PersonalizationLayerError,
    PersonalizationLayers,
    load_personalization_layers,
    render_personalization_section,
)
from .renderers import (
    ADAPTER_VERSION,
    RenderedMcpConfiguration,
    RenderedSkill,
    content_hash,
    mcp_configuration_is_equivalent,
    render_mcp_configuration,
    render_skill,
)
from .session import ProcessSpawner, _default_spawner, open_context
from .sources import (
    CanonicalInputMissingError,
    CanonicalRegistryInvalidError,
    CanonicalRegistryMissingError,
    CanonicalSkill,
    CanonicalSourceSet,
    compose_canonical_skill,
    load_canonical_sources,
    load_context_skill_overrides,
    load_packaged_canonical_sources,
    load_packaged_skill_primary,
    refresh_registry_skills,
    registry_freshness_parts,
    restore_packaged_skill_sources,
)

Clock = Callable[[], datetime]
SessionIdFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _PreparedPlan:
    plan: AdapterPlan
    layout: InstallationLayout
    mutations: tuple[FileMutation, ...]
    backup_paths: tuple[str, ...]
    target_snapshots: tuple[tuple[str, FileSnapshot], ...]
    install_record: InstallRecordObservation | None
    next_manifest_digest: str | None
    operations_digest: str | None
    codex_restore_names: frozenset[str]
    adopt_manifest_digest: str | None


@dataclass(frozen=True, slots=True)
class _CanonicalFileChange:
    """One package-driven canonical transition bound to an exact current-file hash."""

    path: str
    content: bytes | None
    authority_hash: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class _ManagedFileState:
    """A locally edited managed file preserved against its packaged lineage."""

    path: str
    recorded_at_adoption_hash: str
    packaged_now_hash: str
    local_hash: str

    @property
    def merge_candidate(self) -> ManagedFileMerge | None:
        if (
            self.packaged_now_hash == self.recorded_at_adoption_hash
            or self.local_hash == self.packaged_now_hash
        ):
            return None
        return ManagedFileMerge(
            path=self.path,
            recorded_at_adoption_hash=self.recorded_at_adoption_hash,
            packaged_now_hash=self.packaged_now_hash,
            local_hash=self.local_hash,
        )


@dataclass(frozen=True, slots=True)
class _SourceSelection:
    """Effective inputs plus package-refresh and preservation metadata."""

    sources: CanonicalSourceSet
    fingerprint: str
    canonical_changes: tuple[_CanonicalFileChange, ...] = ()
    managed_file_states: tuple[_ManagedFileState, ...] = ()
    preserved_source_hashes: tuple[tuple[str, str], ...] = ()
    preserved_registry_hash: str | None = None
    codex_restore_names: frozenset[str] = frozenset()

    @property
    def merge_candidates(self) -> tuple[ManagedFileMerge, ...]:
        return tuple(
            candidate
            for state in self.managed_file_states
            if (candidate := state.merge_candidate) is not None
        )


# Append-only: every hash of a context-template bridge file ever shipped in a
# release or integrated on master. A context created from ANY of these template
# generations holds workctx-generated content, so healing must recognize all of
# them, not only the current template. When the template changes, append the new
# hash here; a guard test fails until the mapping is updated.
_HISTORICAL_TEMPLATE_BRIDGE_HASHES: dict[str, frozenset[str]] = {
    "AGENTS.md": frozenset(
        {
            "sha256:2d3bc378415a286d713512a71b3187fc28a57b8cc6d8b2ff04e3a98dce4d3daf",
            "sha256:9aced95da7c045aa8c9983ae3c67f0ef741263fbcf91bda879340fba293e966c",
            "sha256:dde5896c72a3ddc7d2b011e5c902f9c60610c6e79c14d30eb8520ced1fd0e27b",
            "sha256:eeca89470537e0b3ea039a20fbbd897914c2b1fb187922749c6497b9de1f8a6a",
            "sha256:8c02628c9701be42a44841e6e775c9b5c5ba3c95cbf07cad9783d12dcffe5374",
        }
    ),
}


def _pristine_template_bridge_hashes(bridge_path: str) -> frozenset[str]:
    """Hashes of every shipped context-template generation of a bridge file.

    A bridge file whose bytes exactly match ANY shipped context template is
    workctx-generated content, not operator writing, so installation may
    replace it with the full adapter bridge.
    """

    name = PurePosixPath(bridge_path).name
    historical = _HISTORICAL_TEMPLATE_BRIDGE_HASHES.get(name, frozenset())
    resource = resources.files("workctx.resources.context_template").joinpath(name)
    try:
        content = resource.read_bytes()
    except (FileNotFoundError, OSError):
        return historical
    return historical | {"sha256:" + hashlib.sha256(content).hexdigest()}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _backup_stamp(value: str) -> str:
    return value.replace("-", "").replace(":", "")


def _plan_digest(
    root: Path,
    client: AgentClient,
    action: OperationAction,
    changes: tuple[PlannedChange, ...],
    source_fingerprint: str | None,
    blocked_reason: str | None,
    target_snapshots: tuple[tuple[str, FileSnapshot], ...],
    install_record_fingerprint: str | None,
    adopt_manifest_digest: str | None,
) -> str:
    value = {
        "root": str(root),
        "client": client.value,
        "action": action.value,
        "changes": [
            {
                "path": change.path,
                "operation": change.operation.value,
                "observed_hash": change.observed_hash,
                "desired_hash": change.desired_hash,
                "requires_approval": change.requires_approval,
                "reason": change.reason,
            }
            for change in changes
        ],
        "source_fingerprint": source_fingerprint,
        "blocked_reason": blocked_reason,
        "install_record_fingerprint": install_record_fingerprint,
        "adopt_manifest_digest": adopt_manifest_digest,
        "target_snapshots": [
            {
                "path": path,
                "exists": snapshot.exists,
                "identity": (
                    None
                    if snapshot.identity is None
                    else [snapshot.identity.device, snapshot.identity.inode]
                ),
                "content_hash": snapshot.content_hash,
            }
            for path, snapshot in target_snapshots
        ],
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _source_selection_digest(
    sources: CanonicalSourceSet,
    canonical_changes: tuple[_CanonicalFileChange, ...],
    managed_file_states: tuple[_ManagedFileState, ...],
    preserved_source_hashes: tuple[tuple[str, str], ...],
    preserved_registry_hash: str | None,
    codex_restore_names: frozenset[str],
) -> str:
    value = {
        "sources": sources.fingerprint,
        "canonical_changes": [
            {
                "path": change.path,
                "desired_hash": (None if change.content is None else content_hash(change.content)),
                "authority_hash": change.authority_hash,
            }
            for change in canonical_changes
        ],
        "managed_file_states": [
            {
                "path": state.path,
                "recorded_at_adoption_hash": state.recorded_at_adoption_hash,
                "packaged_now_hash": state.packaged_now_hash,
                "local_hash": state.local_hash,
            }
            for state in managed_file_states
        ],
        "preserved_source_hashes": list(preserved_source_hashes),
        "preserved_registry_hash": preserved_registry_hash,
        "codex_restore_names": sorted(codex_restore_names),
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return content_hash(canonical)


def _canonical_skill_files(skill: CanonicalSkill) -> dict[str, bytes]:
    return {
        skill.path: skill.content,
        **{
            f".agents/skills/{skill.name}/{resource.relative_path}": resource.content
            for resource in skill.resources
        },
    }


def _managed_file_message(state: _ManagedFileState) -> str:
    prefix = (
        "Merge required for edited managed file"
        if state.merge_candidate is not None
        else "Operator-edited managed file preserved"
    )
    return (
        f"{prefix}: path={state.path}; "
        f"recorded-at-adoption={state.recorded_at_adoption_hash}; "
        f"packaged-now={state.packaged_now_hash}; local={state.local_hash}"
    )


def _managed_file_plan_changes(
    states: tuple[_ManagedFileState, ...],
) -> tuple[PlannedChange, ...]:
    return tuple(
        PlannedChange(
            path=state.path,
            operation=FileOperation.PRESERVE,
            observed_hash=state.local_hash,
            desired_hash=state.packaged_now_hash,
            reason=_managed_file_message(state),
        )
        for state in states
        if state.merge_candidate is not None
    )


def _managed_file_status_warnings(
    states: tuple[_ManagedFileState, ...],
) -> tuple[str, ...]:
    return tuple(
        _managed_file_message(state) for state in states if state.merge_candidate is not None
    )


def _observe_plan_target(
    safe: SafeRoot,
    path: str,
    observations: dict[str, FileSnapshot],
) -> FileSnapshot:
    """Capture one exact plan preimage and reject changes during planning."""

    snapshot = safe.inspect_file(path)
    previous = observations.get(path)
    if previous is not None and not snapshot.matches(
        previous.identity,
        previous.content_hash,
    ):
        raise AdapterConflictError(f"Plan target changed during dry run; replan: {path}")
    observations[path] = snapshot
    return snapshot


def _feature_mcp(
    safe: SafeRoot | None = None,
    client: AgentClient | None = None,
    manifest: AdapterManifest | None = None,
) -> FeatureStatus:
    """Detect MCP config ownership and drift from exact path and manifest hash."""

    if safe is None or client is None:
        return FeatureStatus(
            FeatureState.MISSING,
            detail="Project-scoped MCP configuration is absent.",
        )
    desired = render_mcp_configuration(client)
    component = (
        manifest.components.mcp_configuration
        if manifest is not None and manifest.components is not None
        else None
    )
    path = component.path if component is not None and component.path is not None else desired.path
    target = safe.inspect_file(path)

    if not target.exists:
        if component is not None and component.state in {"native", "divergent"}:
            return FeatureStatus(
                FeatureState.DIVERGENT,
                path,
                "User-owned MCP configuration is absent and is not recreated.",
            )
        if component is not None and component.state == "not_implemented":
            return FeatureStatus(
                FeatureState.NOT_IMPLEMENTED,
                detail="Legacy manifest predates project-scoped MCP configuration.",
            )
        return FeatureStatus(
            FeatureState.MISSING,
            path,
            "Manifest-owned MCP configuration is missing.",
        )
    if target.content is None or target.content_hash is None:
        return FeatureStatus(
            FeatureState.DIVERGENT,
            path,
            "MCP configuration content is unavailable.",
        )
    if component is None or component.state == "not_implemented":
        if mcp_configuration_is_equivalent(client, target.content):
            return FeatureStatus(
                FeatureState.NATIVE,
                path,
                "Existing user-owned MCP configuration already declares Work Context.",
            )
        return FeatureStatus(
            FeatureState.DIVERGENT,
            path,
            "Existing user-owned MCP configuration is preserved without rewriting.",
        )
    if component.state == "generated":
        if target.content_hash == component.content_hash:
            return FeatureStatus(FeatureState.GENERATED, path)
        return FeatureStatus(
            FeatureState.DIVERGENT,
            path,
            "Manifest-owned MCP configuration differs from its recorded generated bytes.",
        )
    if (
        component.state == "native"
        and target.content_hash == component.content_hash
        and mcp_configuration_is_equivalent(client, target.content)
    ):
        return FeatureStatus(FeatureState.NATIVE, path)
    return FeatureStatus(
        FeatureState.DIVERGENT,
        path,
        "User-owned MCP configuration differs from its recorded native state and is preserved.",
    )


def _manifest_from_snapshot(
    snapshot: FileSnapshot,
    client: AgentClient,
) -> AdapterManifest | None:
    if not snapshot.exists:
        return None
    if snapshot.content is None:
        raise InvalidAdapterStateError("Adapter manifest content is unavailable")
    try:
        peek = json.loads(snapshot.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidAdapterStateError("Adapter manifest is not valid UTF-8 JSON") from error
    if not isinstance(peek, dict) or isinstance(peek.get("schema_version"), bool):
        raise InvalidAdapterStateError("Adapter manifest has an invalid version field")
    version = peek.get("schema_version")
    if not isinstance(version, int):
        raise InvalidAdapterStateError("Adapter manifest has an invalid version field")
    if version > 1:
        raise UnsupportedClientVersionError(f"Manifest schema version {version} is newer than 1")
    if version != 1:
        raise InvalidAdapterStateError(f"Manifest schema version {version} is invalid")
    try:
        manifest = load_manifest(snapshot.content)
    except ValidationError as error:
        raise InvalidAdapterStateError("Adapter manifest violates its typed contract") from error
    if manifest.adapter != client.value:
        raise InvalidAdapterStateError("Adapter manifest does not match its derived location")
    if manifest.adapter_version > ADAPTER_VERSION:
        raise UnsupportedClientVersionError(
            "Installed adapter renderer is newer than this implementation."
        )
    _validate_runtime_manifest_targets(manifest, client)
    return manifest


def _personalization_status_warnings(
    statuses: tuple[PersonalizationLayerStatus, ...],
) -> tuple[str, ...]:
    """Expose present-layer status through the existing CLI warning payload seam."""

    return tuple(
        "Personalization "
        f"{status.layer.value} layer: path={status.path}; "
        f"size={status.size_bytes} bytes; merged={'yes' if status.merged else 'no'}"
        for status in statuses
        if status.present
    )


def _personalization_plan_changes(
    layers: PersonalizationLayers,
    statuses: tuple[PersonalizationLayerStatus, ...],
) -> tuple[PlannedChange, ...]:
    """Represent inert layer reads in plans without making them mutation targets."""

    by_name = {status.layer: status for status in statuses}
    return tuple(
        PlannedChange(
            path=str(layer.path),
            operation=FileOperation.VERIFY,
            observed_hash=layer.content_hash,
            desired_hash=layer.content_hash,
            reason=(
                f"{layer.layer.value.title()} personalization layer; "
                f"size={layer.size_bytes} bytes; "
                f"merged={'yes' if by_name[layer.layer].merged else 'no'}"
            ),
        )
        for layer in layers.present
    )


def _skill_override_status_warnings(overrides: SkillOverrides) -> tuple[str, ...]:
    """Surface every observed override through the existing CLI warning payload."""

    return tuple(skill_override_status_message(status) for status in overrides.statuses)


def _skill_override_plan_changes(overrides: SkillOverrides) -> tuple[PlannedChange, ...]:
    """List inert override inputs first in install plans without claiming ownership."""

    return tuple(
        PlannedChange(
            path=status.path,
            operation=FileOperation.VERIFY,
            observed_hash=status.override_hash,
            desired_hash=status.override_hash,
            reason=skill_override_status_message(status),
        )
        for status in overrides.statuses
    )


def _installed_personalization_is_current(
    layout: InstallationLayout,
    layers: PersonalizationLayers,
) -> bool:
    """Return whether a manifest-recorded generated bridge ends in the current exact section."""

    section = render_personalization_section(layers)
    if not section:
        return False
    safe = SafeRoot(layout.root)
    try:
        manifest_snapshot = safe.inspect_file(layout.manifest_path)
        manifest = _manifest_from_snapshot(manifest_snapshot, layout.client)
        if manifest is None or manifest.components is None:
            return False
        bridge = manifest.components.instruction_bridge
        if bridge.ownership != "generated":
            return False
        target = safe.inspect_file(bridge.target.path)
    except (
        InvalidAdapterStateError,
        SafeFilesystemError,
        UnsupportedClientVersionError,
    ):
        return False
    return target.content is not None and target.content.endswith(section)


def _is_safe_skill_output(path: str, client: AgentClient, skill_name: str | None = None) -> bool:
    parts = path.split("/")
    if client is AgentClient.CODEX and parts[0] == ".agents":
        return skill_name is not None and parts == [".agents", "skills", skill_name, "SKILL.md"]
    expected_root = f".{client.value}"
    if len(parts) < 4 or parts[0] != expected_root or parts[1] != "skills":
        return False
    if skill_name is not None and parts[2] != skill_name:
        return False
    return not is_credential_capable_path("/".join(parts[3:])) and all(
        part and part not in {".", ".."} for part in parts[3:]
    )


def _validate_runtime_manifest_targets(
    manifest: AdapterManifest,
    client: AgentClient,
) -> None:
    """Deny credential/config paths before any manifest-directed target read."""

    for skill in manifest.skills:
        for generated in skill.generated or ():
            if not _is_safe_skill_output(generated.path, client, skill.name):
                raise InvalidAdapterStateError(
                    "Adapter v1 manifest contains a non-skill or credential-capable target"
                )
    bridge_name = {
        AgentClient.CODEX: "AGENTS.md",
        AgentClient.CLAUDE: "CLAUDE.md",
        AgentClient.GEMINI: "GEMINI.md",
    }[client]
    mcp_path = render_mcp_configuration(client).path
    if manifest.components is not None:
        mcp = manifest.components.mcp_configuration
        if mcp.state != "not_implemented" and mcp.path != mcp_path:
            raise InvalidAdapterStateError(
                "Adapter manifest MCP configuration is outside the client-scoped path"
            )
    for backup in manifest.backups or ():
        if not (
            _is_safe_skill_output(backup.original_path, client)
            or backup.original_path == bridge_name
        ):
            raise InvalidAdapterStateError(
                "Adapter manifest backup refers to a non-skill or credential-capable target"
            )
        expected_backup_path = (
            f".workctx/backups/{_backup_stamp(backup.created_at)}/{backup.original_path}"
        )
        if backup.path != expected_backup_path:
            raise InvalidAdapterStateError("Adapter backup path does not bind its original path")


def _generated_skill_inventory(manifest: AdapterManifest | None) -> dict[str, str]:
    inventory: dict[str, str] = {}
    if manifest is None:
        return inventory
    for skill in manifest.skills:
        for generated in skill.generated or ():
            inventory[generated.path] = generated.content_hash
    return inventory


def _removed_codex_override_names(
    manifest: AdapterManifest | None,
    sources: CanonicalSourceSet,
) -> frozenset[str]:
    """Find previously owned Codex override primaries whose user files were removed."""

    if manifest is None or manifest.adapter != AgentClient.CODEX.value:
        return frozenset()
    active = {status.skill for status in sources.skill_overrides.statuses}
    removed: set[str] = set()
    for skill in manifest.skills:
        expected_path = f".agents/skills/{skill.name}/SKILL.md"
        generated_paths = {item.path for item in skill.generated or ()}
        if (
            skill.effective_mode == "generated"
            and generated_paths == {expected_path}
            and skill.name not in active
        ):
            removed.add(skill.name)
    return frozenset(removed)


def _codex_override_takeover_hashes(
    manifest: AdapterManifest | None,
    sources: CanonicalSourceSet,
) -> dict[str, str]:
    """Authorize takeover only from an authenticated native packaged adoption hash."""

    if manifest is None or manifest.adapter != AgentClient.CODEX.value:
        return {}
    recorded = {skill.name: skill for skill in manifest.skills}
    takeovers: dict[str, str] = {}
    for skill in sources.skills:
        override = skill.override
        entry = recorded.get(skill.name)
        if override is None or entry is None or entry.effective_mode != "native-verified":
            continue
        adoption_hash = override.packaged_at_adoption_hash
        if adoption_hash is None or entry.canonical.content_hash != adoption_hash:
            continue
        path = f".agents/skills/{skill.name}/SKILL.md"
        source_hashes = {
            source.path: source.content_hash
            for source in (() if entry.source_set is None else entry.source_set.files)
        }
        if source_hashes.get(path) == adoption_hash:
            takeovers[path] = adoption_hash
    return takeovers


def _generated_inventory(manifest: AdapterManifest | None) -> dict[str, str]:
    """Return every manifest-owned generated output, including MCP configuration."""

    inventory = _generated_skill_inventory(manifest)
    if manifest is not None and manifest.components is not None:
        mcp = manifest.components.mcp_configuration
        if mcp.state == "generated":
            assert mcp.path is not None and mcp.content_hash is not None
            inventory[mcp.path] = mcp.content_hash
    return inventory


def _manifest_mutation_targets(manifest: AdapterManifest | None) -> tuple[str, ...]:
    """List every manifest path that repair or uninstall could destructively mutate."""

    if manifest is None:
        return ()
    targets = list(_generated_inventory(manifest))
    if manifest.components is not None:
        bridge = manifest.components.instruction_bridge
        if bridge.ownership == "generated":
            targets.append(bridge.target.path)
    targets.extend(backup.path for backup in manifest.backups or ())
    return tuple(dict.fromkeys(targets))


def _unmanaged_skill_warnings(
    safe: SafeRoot,
    client: AgentClient,
    managed_paths: set[str],
) -> tuple[str, ...]:
    """Inventory unmanaged client-skill files without opening their contents."""

    if client is AgentClient.CODEX:
        # Codex consumes the canonical .agents tree natively. It is source input,
        # not an adapter-owned generated tree.
        return ()
    pending = [f".{client.value}/skills"]
    unmanaged: list[str] = []
    while pending:
        directory = pending.pop()
        try:
            names = safe.list_directory_names(directory)
        except FileNotFoundError:
            continue
        for name in names:
            path = f"{directory}/{name}"
            try:
                entry = safe.inspect_entry(path)
            except SafeFilesystemError:
                unmanaged.append(f"Unmanaged unsafe adapter entry: {path}")
                continue
            if entry is None:
                continue
            if entry.is_directory:
                pending.append(entry.path)
            elif entry.path not in managed_paths:
                unmanaged.append(f"Unmanaged adapter file: {entry.path}")
    return tuple(sorted(unmanaged))


def _verified_renderer_hashes(
    manifest: AdapterManifest | None,
    sources: CanonicalSourceSet,
    client: AgentClient,
) -> dict[str, str]:
    """Return hashes grounded solely in the current deterministic renderer."""

    if manifest is None or manifest.adapter_version != ADAPTER_VERSION:
        return {}
    recorded = {skill.name: skill for skill in manifest.skills}
    verified: dict[str, str] = {}
    for skill in sources.skills:
        entry = recorded.get(skill.name)
        if entry is None:
            continue
        rendered = _render_source_skill(client, skill)
        if rendered.mode == "generated":
            recorded_by_path = {
                generated.path: generated.content_hash for generated in entry.generated or ()
            }
            current_by_path = {
                generated.path: generated.target_hash for generated in rendered.generated_files
            }
            if (
                manifest.registry.content_hash == sources.registry_hash
                and entry.canonical.content_hash == skill.content_hash
                and recorded_by_path == current_by_path
            ):
                verified.update(current_by_path)
    return verified


def _render_source_skill(client: AgentClient, skill: CanonicalSkill) -> RenderedSkill:
    """Render one effective source, owning only Codex override primaries."""

    return render_skill(
        client,
        name=skill.name,
        canonical_content=skill.content,
        side_effect_class=skill.side_effect_class,
        resource_contents=tuple(
            (resource.relative_path, resource.content) for resource in skill.resources
        ),
        force_generated=client is AgentClient.CODEX and skill.override is not None,
    )


def _select_codex_context_sources(
    local: CanonicalSourceSet,
    packaged: CanonicalSourceSet,
    manifest: AdapterManifest,
    codex_restore_names: frozenset[str],
) -> _SourceSelection:
    """Select package refreshes only for manifest-tracked pristine context files."""

    if local.origin is not SourceOrigin.LOCAL:
        fingerprint = _source_selection_digest(
            local,
            (),
            (),
            (),
            None,
            codex_restore_names,
        )
        return _SourceSelection(
            sources=local,
            fingerprint=fingerprint,
            codex_restore_names=codex_restore_names,
        )

    local_skills = {skill.name: skill for skill in local.skills}
    local_packaged_skills = {skill.name: skill for skill in local.skills if not skill.custom}
    local_custom_skills = {skill.name: skill for skill in local.skills if skill.custom}
    packaged_skills = {skill.name: skill for skill in packaged.skills}
    recorded_skills = {skill.name: skill for skill in manifest.skills}
    active_overrides = local.skill_overrides.by_name
    changes: list[_CanonicalFileChange] = []
    managed_states: list[_ManagedFileState] = []
    preserved_hashes: dict[str, str] = {}

    recorded_registry_hash = manifest.registry.content_hash
    local_registry_hash = local.registry_hash
    packaged_registry_hash = packaged.registry_hash
    package_removed_skills = set(local_packaged_skills) - set(packaged_skills)
    registry_matches_managed_generation = local_registry_hash in {
        recorded_registry_hash,
        packaged_registry_hash,
    }
    if local.custom_skill_names:
        registry_matches_managed_generation = registry_matches_managed_generation or (
            content_hash(local.registry_without_custom_content) == recorded_registry_hash
            or local.registry_skills_content == packaged.registry_skills_content
        )
    registry_operator_edited = not registry_matches_managed_generation
    use_packaged_registry = not registry_operator_edited and not package_removed_skills
    preserved_registry_hash: str | None = None
    if registry_operator_edited:
        packaged_now_content = refresh_registry_skills(
            local.registry_content,
            packaged.registry_content,
        )
        managed_states.append(
            _ManagedFileState(
                path=manifest.registry.path,
                recorded_at_adoption_hash=recorded_registry_hash,
                packaged_now_hash=content_hash(packaged_now_content),
                local_hash=local_registry_hash,
            )
        )
        preserved_registry_hash = recorded_registry_hash
    effective_registry_content = (
        refresh_registry_skills(local.registry_content, packaged.registry_content)
        if use_packaged_registry
        else local.registry_content
    )
    effective_registry_hash = content_hash(effective_registry_content)
    if use_packaged_registry and local_registry_hash != effective_registry_hash:
        # A custom-bearing registry is intentionally mixed-ownership. The deterministic
        # rewrite changes only the validated packaged section, preserves custom bytes, and
        # remains bound to the exact complete local preimage through this authority hash.
        changes.append(
            _CanonicalFileChange(
                path=manifest.registry.path,
                content=effective_registry_content,
                authority_hash=(
                    local_registry_hash if local.custom_skill_names else recorded_registry_hash
                ),
                reason=(
                    "Refresh packaged skills in the canonical registry while preserving "
                    "custom_skills verbatim"
                    if local.custom_skill_names
                    else "Refresh pristine canonical skill registry from the current package"
                ),
            )
        )

    selected_registry_skills = [
        *(packaged.skills if use_packaged_registry else tuple(local_packaged_skills.values())),
        *local_custom_skills.values(),
    ]
    selected_skills: list[CanonicalSkill] = []
    for selected_registry_skill in selected_registry_skills:
        name = selected_registry_skill.name
        local_skill = local_skills.get(name)
        packaged_skill = packaged_skills.get(name)
        override = active_overrides.get(name)

        if packaged_skill is None:
            if local_skill is not None:
                selected_skills.append(local_skill)
            continue

        packaged_files = _canonical_skill_files(packaged_skill)
        if override is not None:
            override_files = dict(packaged_files)
            override_files[packaged_skill.path] = override.file.content
            selected_skills.append(
                compose_canonical_skill(
                    packaged_skill,
                    override_files,
                    override=override,
                )
            )
            continue

        if name in codex_restore_names:
            selected_skills.append(packaged_skill)
            continue

        if local_skill is None:
            selected_skills.append(packaged_skill)
            changes.extend(
                _CanonicalFileChange(
                    path=path,
                    content=content,
                    authority_hash=None,
                    reason="Materialize a newly packaged canonical skill file",
                )
                for path, content in sorted(packaged_files.items())
            )
            continue

        recorded = recorded_skills.get(name)
        if recorded is None or recorded.effective_mode != "native-verified":
            selected_skills.append(local_skill)
            continue
        assert recorded.source_set is not None
        recorded_files = {source.path: source.content_hash for source in recorded.source_set.files}
        local_files = _canonical_skill_files(local_skill)
        selected_files: dict[str, bytes] = {}
        for path in sorted(set(recorded_files) | set(local_files) | set(packaged_files)):
            recorded_hash = recorded_files.get(path)
            local_content = local_files.get(path)
            packaged_content = packaged_files.get(path)
            local_hash = None if local_content is None else content_hash(local_content)
            packaged_hash = None if packaged_content is None else content_hash(packaged_content)

            if packaged_content is None:
                if local_content is None:
                    continue
                if recorded_hash is not None and local_hash == recorded_hash:
                    changes.append(
                        _CanonicalFileChange(
                            path=path,
                            content=None,
                            authority_hash=recorded_hash,
                            reason="Remove a pristine canonical skill resource no longer packaged",
                        )
                    )
                    continue
                selected_files[path] = local_content
                if recorded_hash is not None:
                    preserved_hashes[path] = recorded_hash
                continue

            assert packaged_hash is not None
            if local_content is None:
                if recorded_hash is not None:
                    raise CanonicalInputMissingError(
                        f"Tracked canonical skill file is missing: {path}",
                        path=path,
                        skill=name,
                    )
                selected_files[path] = packaged_content
                changes.append(
                    _CanonicalFileChange(
                        path=path,
                        content=packaged_content,
                        authority_hash=None,
                        reason="Materialize a newly packaged canonical skill file",
                    )
                )
                continue

            if recorded_hash is None:
                selected_files[path] = local_content
                continue
            if local_hash == recorded_hash:
                selected_files[path] = packaged_content
                if local_hash != packaged_hash:
                    changes.append(
                        _CanonicalFileChange(
                            path=path,
                            content=packaged_content,
                            authority_hash=recorded_hash,
                            reason=(
                                "Refresh pristine manifest-tracked canonical skill file "
                                "from the current package"
                            ),
                        )
                    )
                continue
            if local_hash == packaged_hash:
                selected_files[path] = local_content
                continue

            selected_files[path] = local_content
            preserved_hashes[path] = recorded_hash
            managed_states.append(
                _ManagedFileState(
                    path=path,
                    recorded_at_adoption_hash=recorded_hash,
                    packaged_now_hash=packaged_hash,
                    local_hash=cast(str, local_hash),
                )
            )

        template = packaged_skill if use_packaged_registry else selected_registry_skill
        selected_skills.append(compose_canonical_skill(template, selected_files))

    registry_skills_content, registry_without_custom_content = registry_freshness_parts(
        effective_registry_content
    )
    selected_sources = replace(
        local,
        registry_content=effective_registry_content,
        registry_hash=effective_registry_hash,
        registry_skills_content=registry_skills_content,
        registry_without_custom_content=registry_without_custom_content,
        skills=tuple(sorted(selected_skills, key=lambda skill: skill.name)),
    )
    ordered_changes = tuple(sorted(changes, key=lambda change: change.path))
    ordered_states = tuple(sorted(managed_states, key=lambda state: state.path))
    ordered_preserved = tuple(sorted(preserved_hashes.items()))
    fingerprint = _source_selection_digest(
        selected_sources,
        ordered_changes,
        ordered_states,
        ordered_preserved,
        preserved_registry_hash,
        codex_restore_names,
    )
    return _SourceSelection(
        sources=selected_sources,
        fingerprint=fingerprint,
        canonical_changes=ordered_changes,
        managed_file_states=ordered_states,
        preserved_source_hashes=ordered_preserved,
        preserved_registry_hash=preserved_registry_hash,
        codex_restore_names=codex_restore_names,
    )


def _bridge_managed_file_state(
    manifest: AdapterManifest | None,
    sources: CanonicalSourceSet,
    target: FileSnapshot,
) -> _ManagedFileState | None:
    if (
        manifest is None
        or manifest.components is None
        or not target.exists
        or target.content_hash is None
    ):
        return None
    bridge = manifest.components.instruction_bridge
    recorded_at_adoption = bridge.source.content_hash
    packaged_now = (
        sources.bridge_hash if bridge.ownership == "generated" else sources.bridge_template_hash
    )
    if target.content_hash in {recorded_at_adoption, packaged_now}:
        return None
    return _ManagedFileState(
        path=bridge.target.path,
        recorded_at_adoption_hash=recorded_at_adoption,
        packaged_now_hash=packaged_now,
        local_hash=target.content_hash,
    )


class AgentAdapterService:
    """Project-scoped agent adapter lifecycle service with injectable client discovery."""

    def __init__(
        self,
        *,
        executable_finder: ExecutableFinder = shutil.which,
        version_probe: VersionProbe = _default_version_probe,
        spawner: ProcessSpawner = _default_spawner,
        clock: Clock = _utc_now,
        session_id_factory: SessionIdFactory = lambda: secrets.token_hex(16),
    ) -> None:
        self._detector = ClientDetector(
            executable_finder=executable_finder,
            version_probe=version_probe,
        )
        self._finder = executable_finder
        self._probe = version_probe
        self._spawner = spawner
        self._clock = clock
        self._session_id = session_id_factory
        self._install_records = TrustedInstallStore()
        self._prepared: dict[str, _PreparedPlan] = {}

    def detect(self, project_root: Path) -> tuple[ClientCapability, ...]:
        return self._detector.detect(project_root)

    def open_context(self, root: Path, client: AgentClient) -> OpenedContext:
        return open_context(
            root,
            client,
            executable_finder=self._finder,
            version_probe=self._probe,
            spawner=self._spawner,
        )

    @staticmethod
    def _load_source_selection(
        layout: InstallationLayout,
        manifest: AdapterManifest | None,
        personalization: PersonalizationLayers,
        skill_overrides: SkillOverrides | None = None,
    ) -> _SourceSelection:
        selected_overrides = (
            load_context_skill_overrides(
                layout.root,
                include_context=layout.is_context,
            )
            if skill_overrides is None
            else skill_overrides
        )
        local = load_canonical_sources(
            layout.root,
            layout.client,
            personalization=personalization,
            skill_overrides=selected_overrides,
            include_context_overrides=layout.is_context,
        )
        codex_restore_names = _removed_codex_override_names(manifest, local)
        local = restore_packaged_skill_sources(local, codex_restore_names)
        packaged: CanonicalSourceSet | None = None
        if layout.is_context:
            packaged = load_packaged_canonical_sources(
                layout.root,
                layout.client,
                personalization=personalization,
            )
            packaged_names = {skill.name for skill in packaged.skills}
            misplaced = tuple(
                sorted(
                    skill.name
                    for skill in local.skills
                    if not skill.custom and skill.name not in packaged_names
                )
            )
            if misplaced:
                names = ", ".join(misplaced)
                raise CanonicalRegistryInvalidError(
                    f"The packaged skills section contains context-local entries: {names}.",
                    repair_action=(
                        f"Move {names} from skills: to custom_skills: in "
                        ".agents/skills/registry.yaml and leave the source directories under "
                        ".agents/skills/<id>/."
                    ),
                )
        if manifest is None or layout.client is not AgentClient.CODEX or not layout.is_context:
            return _SourceSelection(
                sources=local,
                fingerprint=local.fingerprint,
                codex_restore_names=codex_restore_names,
            )
        assert packaged is not None
        return _select_codex_context_sources(
            local,
            packaged,
            manifest,
            codex_restore_names,
        )

    def status(self, root: Path, client: AgentClient) -> AdapterStatus:
        """Derive adapter, personalization, and override status without writing."""

        layout = derive_layout(root, client)
        try:
            personalization = load_personalization_layers(
                layout.root,
                include_context=layout.is_context,
            )
            skill_overrides = load_context_skill_overrides(
                layout.root,
                include_context=layout.is_context,
            )
        except (PersonalizationLayerError, InvalidAdapterStateError) as error:
            return AdapterStatus(
                client=client,
                state=AdapterState.INVALID,
                manifest_path=layout.manifest_path,
                warnings=(str(error),),
                repair_blocked=True,
            )
        derived = self._status(layout, personalization, skill_overrides)
        layer_statuses = personalization.statuses(
            merged=_installed_personalization_is_current(layout, personalization)
        )
        override_statuses = skill_overrides.statuses
        override_warnings = skill_overrides.warnings
        return replace(
            derived,
            warnings=(
                *derived.warnings,
                *_skill_override_status_warnings(skill_overrides),
                *_personalization_status_warnings(layer_statuses),
            ),
            personalization_layers=layer_statuses,
            skill_overrides=override_statuses,
            skill_override_warnings=override_warnings,
        )

    def _status(
        self,
        layout: InstallationLayout,
        personalization: PersonalizationLayers,
        skill_overrides: SkillOverrides,
    ) -> AdapterStatus:
        """Derive status without writing, following the normative precedence order."""

        client = layout.client
        safe = SafeRoot(layout.root)
        mcp = _feature_mcp()
        lock = inspect_adapter_lock(layout, now=self._clock())
        if lock.invalid:
            return AdapterStatus(
                client,
                AdapterState.INVALID,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=(lock.detail or "Invalid lock state",),
                repair_blocked=True,
            )
        if lock.live:
            return AdapterStatus(
                client,
                AdapterState.BUSY,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                repair_blocked=True,
            )
        transaction = inspect_transactions(layout)
        if transaction.invalid:
            return AdapterStatus(
                client,
                AdapterState.INVALID,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=(transaction.detail or "Invalid staging state",),
                repair_blocked=True,
            )
        if transaction.intents:
            return AdapterStatus(
                client,
                AdapterState.RECOVERY_REQUIRED,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                repair_blocked=True,
            )

        try:
            manifest_snapshot = safe.inspect_file(layout.manifest_path)
            manifest = _manifest_from_snapshot(manifest_snapshot, client)
        except UnsupportedClientVersionError as error:
            return AdapterStatus(
                client,
                AdapterState.UNSUPPORTED,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=(str(error),),
                repair_blocked=True,
            )
        except (InvalidAdapterStateError, SafeFilesystemError) as error:
            return AdapterStatus(
                client,
                AdapterState.INVALID,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=(str(error),),
                repair_blocked=True,
            )
        try:
            mcp = _feature_mcp(safe, client, manifest)
        except SafeFilesystemError as error:
            return AdapterStatus(
                client,
                AdapterState.INVALID,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=FeatureStatus(
                    FeatureState.DIVERGENT,
                    render_mcp_configuration(client).path,
                    str(error),
                ),
                warnings=(str(error),),
                repair_blocked=True,
            )
        authority_warning: str | None = None
        install_record: InstallRecordObservation | None = None
        try:
            install_record = self._install_records.observe(
                layout.root,
                client,
                layout.manifest_path,
            )
        except InstallRecordError as error:
            authority_warning = f"Trusted install record is unavailable: {error}"
        else:
            if install_record.has_pending_transition:
                return AdapterStatus(
                    client,
                    AdapterState.RECOVERY_REQUIRED,
                    layout.manifest_path,
                    instruction_bridge=FeatureStatus(FeatureState.MISSING),
                    mcp_configuration=mcp,
                    warnings=(
                        "Trusted install record has a pending transition; recovery is "
                        "required before mutation.",
                    ),
                    repair_blocked=True,
                )
            if manifest_snapshot.exists:
                if not install_record.authenticates(manifest_snapshot.content_hash):
                    authority_warning = (
                        "Project manifest is not authenticated by the trusted user-config "
                        "install record; repair and uninstall are report-only."
                    )
            elif install_record.record is not None:
                authority_warning = (
                    "Trusted user-config install record exists while the project manifest "
                    "is absent; recovery is required before installation."
                )

        if manifest is None:
            warnings = tuple(
                f"Orphan staging directory: {path}" for path in transaction.orphan_directories
            )
            if authority_warning is not None:
                warnings += (authority_warning,)
            try:
                warnings += _unmanaged_skill_warnings(safe, client, set())
            except SafeFilesystemError as error:
                warnings += (f"Unmanaged client tree could not be inventoried: {error}",)
            return AdapterStatus(
                client,
                (
                    AdapterState.CONFLICT
                    if authority_warning is not None
                    else AdapterState.NOT_INSTALLED
                ),
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=warnings,
                repair_blocked=authority_warning is not None,
            )
        capability = self._detector.detect_one(layout.root, client)
        if capability.availability is ClientAvailability.INVALID:
            return AdapterStatus(
                client,
                AdapterState.INVALID,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=((capability.detail or "Invalid project marker state"),),
                repair_blocked=True,
            )
        if capability.availability is ClientAvailability.UNSUPPORTED:
            return AdapterStatus(
                client,
                AdapterState.UNSUPPORTED,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=((capability.detail or "Unsupported client"),),
                repair_blocked=True,
            )
        if manifest.adapter_version > ADAPTER_VERSION:
            return AdapterStatus(
                client,
                AdapterState.UNSUPPORTED,
                layout.manifest_path,
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=("Installed adapter renderer is newer than this implementation.",),
                repair_blocked=True,
            )
        try:
            source_selection = self._load_source_selection(
                layout,
                manifest,
                personalization,
                skill_overrides,
            )
        except CanonicalInputMissingError as error:
            return self._status_missing_input(
                layout,
                manifest,
                error,
                transaction.orphan_directories,
                authority_warning,
            )
        except CanonicalRegistryInvalidError as error:
            return AdapterStatus(
                client,
                AdapterState.INVALID,
                layout.manifest_path,
                drift=(
                    DriftDetail(
                        DriftReason.REGISTRY_INVALID,
                        path=".agents/skills/registry.yaml",
                        detail=str(error),
                    ),
                ),
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=(str(error),),
                repair_blocked=True,
            )
        except (InvalidAdapterStateError, SafeFilesystemError) as error:
            return AdapterStatus(
                client,
                AdapterState.INVALID,
                layout.manifest_path,
                drift=(DriftDetail(DriftReason.SOURCE_INVALID, detail=str(error)),),
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp,
                warnings=(str(error),),
                repair_blocked=True,
            )
        derived = self._derive_freshness(
            layout,
            manifest,
            source_selection,
            transaction.orphan_directories,
            authority_warning,
        )
        return replace(
            derived,
            custom_skills=source_selection.sources.custom_skill_names,
        )

    def _status_missing_input(
        self,
        layout: InstallationLayout,
        manifest: AdapterManifest,
        error: CanonicalInputMissingError,
        orphan_directories: tuple[str, ...],
        authority_warning: str | None,
    ) -> AdapterStatus:
        reason = (
            DriftReason.REGISTRY_MISSING
            if isinstance(error, CanonicalRegistryMissingError)
            else DriftReason.SOURCE_MISSING
        )
        drift = [
            DriftDetail(
                reason,
                path=path,
                skill=skill,
                detail=str(error),
            )
            for path, skill in error.missing_inputs
        ]
        if (
            any(skill.mode is None for skill in manifest.skills)
            or manifest.components is None
            or manifest.backups is None
            or (
                manifest.components is not None
                and manifest.components.mcp_configuration.state == "not_implemented"
            )
        ):
            drift.append(DriftDetail(DriftReason.LEGACY_MANIFEST))
        safe = SafeRoot(layout.root)
        try:
            mcp_feature = _feature_mcp(safe, layout.client, manifest)
        except SafeFilesystemError as unsafe:
            return AdapterStatus(
                layout.client,
                AdapterState.INVALID,
                layout.manifest_path,
                drift=tuple(drift),
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=FeatureStatus(
                    FeatureState.DIVERGENT,
                    render_mcp_configuration(layout.client).path,
                    str(unsafe),
                ),
                warnings=(str(unsafe),),
                repair_blocked=True,
            )
        modified = False
        for path, expected_hash in sorted(_generated_inventory(manifest).items()):
            try:
                target = safe.inspect_file(path)
            except SafeFilesystemError as unsafe:
                return AdapterStatus(
                    layout.client,
                    AdapterState.INVALID,
                    layout.manifest_path,
                    drift=tuple(drift),
                    instruction_bridge=FeatureStatus(FeatureState.MISSING),
                    mcp_configuration=mcp_feature,
                    warnings=(str(unsafe),),
                    repair_blocked=True,
                )
            if not target.exists:
                drift.append(
                    DriftDetail(
                        DriftReason.GENERATED_MISSING,
                        path=path,
                        expected_hash=expected_hash,
                    )
                )
            elif target.content_hash != expected_hash:
                modified = True
                drift.append(
                    DriftDetail(
                        DriftReason.GENERATED_MODIFIED,
                        path=path,
                        expected_hash=expected_hash,
                        actual_hash=target.content_hash,
                    )
                )
        for backup in manifest.backups or ():
            try:
                target = safe.inspect_file(backup.path)
            except SafeFilesystemError as unsafe:
                return AdapterStatus(
                    layout.client,
                    AdapterState.INVALID,
                    layout.manifest_path,
                    drift=tuple(drift),
                    instruction_bridge=FeatureStatus(FeatureState.MISSING),
                    mcp_configuration=mcp_feature,
                    warnings=(str(unsafe),),
                    repair_blocked=True,
                )
            if not target.exists:
                drift.append(
                    DriftDetail(
                        DriftReason.BACKUP_MISSING,
                        path=backup.path,
                        expected_hash=backup.content_hash,
                    )
                )
            elif target.content_hash != backup.content_hash:
                modified = True
                drift.append(
                    DriftDetail(
                        DriftReason.BACKUP_MODIFIED,
                        path=backup.path,
                        expected_hash=backup.content_hash,
                        actual_hash=target.content_hash,
                    )
                )
        warnings = [f"Orphan staging directory: {path}" for path in orphan_directories]
        if authority_warning is not None:
            warnings.append(authority_warning)
        bridge_feature = FeatureStatus(FeatureState.MISSING)
        if manifest.components is not None:
            bridge = manifest.components.instruction_bridge
            try:
                target = safe.inspect_file(bridge.target.path)
            except SafeFilesystemError as unsafe:
                return AdapterStatus(
                    layout.client,
                    AdapterState.INVALID,
                    layout.manifest_path,
                    drift=tuple(drift),
                    instruction_bridge=FeatureStatus(FeatureState.MISSING, detail=str(unsafe)),
                    mcp_configuration=mcp_feature,
                    warnings=(str(unsafe),),
                    repair_blocked=True,
                )
            if not target.exists:
                if bridge.ownership == "user-owned":
                    bridge_feature = FeatureStatus(
                        FeatureState.DIVERGED,
                        bridge.target.path,
                        "User-owned instruction bridge is absent and is not recreated.",
                    )
                    drift.append(
                        DriftDetail(
                            DriftReason.BRIDGE_DIVERGED,
                            path=bridge.target.path,
                            expected_hash=bridge.target.content_hash,
                        )
                    )
                    warnings.append(
                        f"User-owned instruction bridge is absent: {bridge.target.path}"
                    )
                else:
                    drift.append(
                        DriftDetail(
                            DriftReason.GENERATED_MISSING,
                            path=bridge.target.path,
                            expected_hash=bridge.target.content_hash,
                        )
                    )
            elif target.content_hash == bridge.target.content_hash:
                bridge_feature = FeatureStatus(FeatureState.CURRENT, bridge.target.path)
            elif bridge.ownership == "generated":
                modified = True
                bridge_feature = FeatureStatus(FeatureState.DIVERGED, bridge.target.path)
                drift.append(
                    DriftDetail(
                        DriftReason.GENERATED_MODIFIED,
                        path=bridge.target.path,
                        expected_hash=bridge.target.content_hash,
                        actual_hash=target.content_hash,
                    )
                )
            else:
                bridge_feature = FeatureStatus(FeatureState.DIVERGED, bridge.target.path)
                drift.append(
                    DriftDetail(
                        DriftReason.BRIDGE_DIVERGED,
                        path=bridge.target.path,
                        expected_hash=bridge.target.content_hash,
                        actual_hash=target.content_hash,
                    )
                )
        try:
            warnings.extend(
                _unmanaged_skill_warnings(
                    safe,
                    layout.client,
                    set(_generated_inventory(manifest)),
                )
            )
        except SafeFilesystemError as unsafe:
            return AdapterStatus(
                layout.client,
                AdapterState.INVALID,
                layout.manifest_path,
                drift=tuple(drift),
                instruction_bridge=bridge_feature,
                mcp_configuration=mcp_feature,
                warnings=(str(unsafe),),
                repair_blocked=True,
            )
        if (
            manifest.components is not None
            and manifest.components.mcp_configuration.state in {"native", "divergent"}
            and mcp_feature.state is FeatureState.DIVERGENT
        ):
            mcp = manifest.components.mcp_configuration
            drift.append(
                DriftDetail(
                    DriftReason.MCP_DIVERGENT,
                    path=mcp.path,
                    expected_hash=mcp.content_hash,
                    detail=mcp_feature.detail,
                )
            )
            warnings.append(f"User-owned MCP configuration is divergent: {mcp.path}")
        return AdapterStatus(
            layout.client,
            (
                AdapterState.CONFLICT
                if modified or authority_warning is not None
                else AdapterState.STALE
            ),
            layout.manifest_path,
            drift=tuple(drift),
            instruction_bridge=bridge_feature,
            mcp_configuration=mcp_feature,
            warnings=tuple(warnings),
            repair_blocked=True,
        )

    def _derive_freshness(
        self,
        layout: InstallationLayout,
        manifest: AdapterManifest,
        source_selection: _SourceSelection,
        orphan_directories: tuple[str, ...],
        authority_warning: str | None,
    ) -> AdapterStatus:
        sources = source_selection.sources
        safe = SafeRoot(layout.root)
        drift: list[DriftDetail] = []
        warnings = [f"Orphan staging directory: {path}" for path in orphan_directories]
        managed_file_states = list(source_selection.managed_file_states)
        warnings.extend(_managed_file_status_warnings(source_selection.managed_file_states))
        if authority_warning is not None:
            warnings.append(authority_warning)
        try:
            mcp_feature = _feature_mcp(safe, layout.client, manifest)
        except SafeFilesystemError as error:
            return AdapterStatus(
                layout.client,
                AdapterState.INVALID,
                layout.manifest_path,
                drift=(DriftDetail(DriftReason.SOURCE_INVALID, detail=str(error)),),
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=FeatureStatus(
                    FeatureState.DIVERGENT,
                    render_mcp_configuration(layout.client).path,
                    str(error),
                ),
                warnings=(*warnings, str(error)),
                repair_blocked=True,
            )
        generated_inventory = _generated_inventory(manifest)
        observed_outputs: dict[str, FileSnapshot] = {}
        try:
            observed_outputs = {
                path: safe.inspect_file(path) for path in sorted(generated_inventory)
            }
        except SafeFilesystemError as error:
            return AdapterStatus(
                layout.client,
                AdapterState.INVALID,
                layout.manifest_path,
                drift=(DriftDetail(DriftReason.SOURCE_INVALID, detail=str(error)),),
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp_feature,
                warnings=tuple(warnings),
                repair_blocked=True,
            )
        verified_renderer_hashes = _verified_renderer_hashes(
            manifest,
            sources,
            layout.client,
        )
        try:
            warnings.extend(
                _unmanaged_skill_warnings(
                    safe,
                    layout.client,
                    set(generated_inventory),
                )
            )
        except SafeFilesystemError as error:
            return AdapterStatus(
                layout.client,
                AdapterState.INVALID,
                layout.manifest_path,
                drift=(DriftDetail(DriftReason.SOURCE_INVALID, detail=str(error)),),
                instruction_bridge=FeatureStatus(FeatureState.MISSING),
                mcp_configuration=mcp_feature,
                warnings=tuple(warnings),
                repair_blocked=True,
            )
        if (
            any(skill.mode is None for skill in manifest.skills)
            or manifest.components is None
            or manifest.backups is None
            or (
                manifest.components is not None
                and manifest.components.mcp_configuration.state == "not_implemented"
            )
        ):
            drift.append(DriftDetail(DriftReason.LEGACY_MANIFEST))
        if manifest.adapter_version < ADAPTER_VERSION:
            drift.append(DriftDetail(DriftReason.ADAPTER_VERSION_CHANGED))
        if manifest.registry.content_hash != sources.registry_hash:
            drift.append(
                DriftDetail(
                    DriftReason.REGISTRY_CHANGED,
                    path=manifest.registry.path,
                    expected_hash=manifest.registry.content_hash,
                    actual_hash=sources.registry_hash,
                )
            )
        manifest_skills = {skill.name: skill for skill in manifest.skills}
        current_skills = {skill.name: skill for skill in sources.skills}
        if set(manifest_skills) != set(current_skills):
            drift.append(DriftDetail(DriftReason.INVENTORY_CHANGED))
        for name in sorted(set(manifest_skills) & set(current_skills)):
            recorded = manifest_skills[name]
            current = current_skills[name]
            if (
                recorded.effective_mode != "native-verified"
                and recorded.canonical.content_hash != current.content_hash
            ):
                drift.append(
                    DriftDetail(
                        DriftReason.SOURCE_CHANGED,
                        path=recorded.canonical.path,
                        skill=name,
                        expected_hash=recorded.canonical.content_hash,
                        actual_hash=current.content_hash,
                    )
                )
            expected = _render_source_skill(layout.client, current)
            if expected.mode == "native-verified":
                recorded_sources = {
                    source.path: source.content_hash
                    for source in (() if recorded.source_set is None else recorded.source_set.files)
                }
                current_sources = dict(current.source_files)
                for source_path in sorted(set(recorded_sources) | set(current_sources)):
                    try:
                        native_target = safe.inspect_file(source_path)
                    except SafeFilesystemError as error:
                        return AdapterStatus(
                            layout.client,
                            AdapterState.INVALID,
                            layout.manifest_path,
                            drift=tuple(drift),
                            instruction_bridge=FeatureStatus(FeatureState.MISSING),
                            mcp_configuration=mcp_feature,
                            warnings=(*warnings, str(error)),
                            repair_blocked=True,
                        )
                    recorded_hash = recorded_sources.get(source_path)
                    current_hash = current_sources.get(source_path)
                    if current_hash is None:
                        drift.append(
                            DriftDetail(
                                DriftReason.SOURCE_CHANGED,
                                path=source_path,
                                skill=name,
                                expected_hash=recorded_hash,
                            )
                        )
                    elif not native_target.exists:
                        drift.append(
                            DriftDetail(
                                DriftReason.SOURCE_MISSING,
                                path=source_path,
                                skill=name,
                                expected_hash=recorded_hash or current_hash,
                            )
                        )
                    elif (
                        recorded_hash is None
                        or current_hash != recorded_hash
                        or native_target.content_hash != current_hash
                    ):
                        drift.append(
                            DriftDetail(
                                DriftReason.SOURCE_CHANGED,
                                path=source_path,
                                skill=name,
                                expected_hash=recorded_hash,
                                actual_hash=native_target.content_hash,
                            )
                        )
            recorded_paths = tuple(sorted(item.path for item in recorded.generated or ()))
            expected_paths = tuple(sorted(item.path for item in expected.generated_files))
            if recorded.effective_mode != expected.mode or recorded_paths != expected_paths:
                drift.append(DriftDetail(DriftReason.TARGET_SET_CHANGED, skill=name))
            elif expected.mode == "generated":
                recorded_by_path = {
                    item.path: item.content_hash for item in recorded.generated or ()
                }
                for generated in expected.generated_files:
                    rendered_hash = verified_renderer_hashes.get(generated.path)
                    recorded_hash = recorded_by_path.get(generated.path)
                    if rendered_hash is not None and recorded_hash != rendered_hash:
                        drift.append(
                            DriftDetail(
                                DriftReason.TARGET_SET_CHANGED,
                                path=generated.path,
                                skill=name,
                                expected_hash=rendered_hash,
                                actual_hash=recorded_hash,
                                detail=(
                                    "Recorded output hash differs from deterministic current "
                                    "renderer output."
                                ),
                            )
                        )
                expected_resources = {
                    generated.path: generated for generated in expected.auxiliary_files
                }
                for resource in current.resources:
                    generated_path = (
                        expected.target_path.removesuffix("SKILL.md") + resource.relative_path
                    )
                    resource_file = expected_resources.get(generated_path)
                    recorded_hash = recorded_by_path.get(generated_path)
                    if (
                        resource_file is not None
                        and recorded_hash is not None
                        and recorded_hash != resource_file.target_hash
                    ):
                        drift.append(
                            DriftDetail(
                                DriftReason.SOURCE_CHANGED,
                                path=f".agents/skills/{name}/{resource.relative_path}",
                                skill=name,
                                expected_hash=recorded_hash,
                                actual_hash=resource_file.target_hash,
                            )
                        )
        generated_modified = False
        for path, expected_hash in sorted(generated_inventory.items()):
            trusted_hash = verified_renderer_hashes.get(path)
            target = observed_outputs[path]
            if not target.exists:
                drift.append(
                    DriftDetail(
                        DriftReason.GENERATED_MISSING,
                        path=path,
                        expected_hash=trusted_hash or expected_hash,
                    )
                )
            elif target.content_hash != expected_hash:
                generated_modified = True
                drift.append(
                    DriftDetail(
                        DriftReason.GENERATED_MODIFIED,
                        path=path,
                        expected_hash=expected_hash,
                        actual_hash=target.content_hash,
                    )
                )

        backup_modified = False
        for backup in manifest.backups or ():
            try:
                target = safe.inspect_file(backup.path)
            except SafeFilesystemError as error:
                return AdapterStatus(
                    layout.client,
                    AdapterState.INVALID,
                    layout.manifest_path,
                    drift=tuple(drift),
                    instruction_bridge=FeatureStatus(FeatureState.MISSING),
                    mcp_configuration=mcp_feature,
                    warnings=(*warnings, str(error)),
                    repair_blocked=True,
                )
            if not target.exists:
                drift.append(
                    DriftDetail(
                        DriftReason.BACKUP_MISSING,
                        path=backup.path,
                        expected_hash=backup.content_hash,
                    )
                )
            elif target.content_hash != backup.content_hash:
                backup_modified = True
                drift.append(
                    DriftDetail(
                        DriftReason.BACKUP_MODIFIED,
                        path=backup.path,
                        expected_hash=backup.content_hash,
                        actual_hash=target.content_hash,
                    )
                )

        bridge_feature = FeatureStatus(FeatureState.MISSING)
        components = manifest.components
        if components is not None:
            bridge = components.instruction_bridge
            try:
                target = safe.inspect_file(bridge.target.path)
            except SafeFilesystemError as error:
                return AdapterStatus(
                    layout.client,
                    AdapterState.INVALID,
                    layout.manifest_path,
                    drift=tuple(drift),
                    instruction_bridge=FeatureStatus(FeatureState.MISSING, detail=str(error)),
                    mcp_configuration=mcp_feature,
                    warnings=tuple(warnings),
                    repair_blocked=True,
                )
            bridge_managed_state = _bridge_managed_file_state(manifest, sources, target)
            if bridge_managed_state is not None:
                managed_file_states.append(bridge_managed_state)
                if bridge_managed_state.merge_candidate is not None:
                    warnings.append(_managed_file_message(bridge_managed_state))
            if not target.exists:
                if bridge.ownership == "user-owned":
                    bridge_feature = FeatureStatus(
                        FeatureState.DIVERGED,
                        bridge.target.path,
                        "User-owned instruction bridge is absent and is not recreated.",
                    )
                    drift.append(
                        DriftDetail(
                            DriftReason.BRIDGE_DIVERGED,
                            path=bridge.target.path,
                            expected_hash=bridge.target.content_hash,
                        )
                    )
                    warnings.append(
                        f"User-owned instruction bridge is absent: {bridge.target.path}"
                    )
                else:
                    bridge_feature = FeatureStatus(FeatureState.MISSING, bridge.target.path)
                    drift.append(
                        DriftDetail(
                            DriftReason.GENERATED_MISSING,
                            path=bridge.target.path,
                            expected_hash=sources.bridge_hash,
                        )
                    )
            elif bridge.ownership == "generated":
                if (
                    target.content_hash != bridge.target.content_hash
                    and target.content_hash != sources.bridge_hash
                ):
                    generated_modified = True
                    bridge_feature = FeatureStatus(
                        FeatureState.DIVERGED,
                        bridge.target.path,
                        "Manifest-owned bridge differs from its recorded generated bytes.",
                    )
                    drift.append(
                        DriftDetail(
                            DriftReason.GENERATED_MODIFIED,
                            path=bridge.target.path,
                            expected_hash=bridge.target.content_hash,
                            actual_hash=target.content_hash,
                        )
                    )
                elif target.content_hash != sources.bridge_hash:
                    bridge_feature = FeatureStatus(
                        FeatureState.DIVERGED,
                        bridge.target.path,
                        "Generated instruction bridge does not contain the current source "
                        "and personalization layers.",
                    )
                else:
                    bridge_feature = FeatureStatus(FeatureState.CURRENT, bridge.target.path)
            elif target.content_hash != sources.bridge_hash:
                bridge_feature = FeatureStatus(
                    FeatureState.DIVERGED,
                    bridge.target.path,
                    "User-owned instruction bridge differs from the packaged template "
                    "and is preserved.",
                )
                drift.append(
                    DriftDetail(
                        DriftReason.BRIDGE_DIVERGED,
                        path=bridge.target.path,
                        expected_hash=sources.bridge_hash,
                        actual_hash=target.content_hash,
                    )
                )
                warnings.append(
                    f"User-owned instruction bridge differs from its source: {bridge.target.path}"
                )
            else:
                bridge_feature = FeatureStatus(
                    FeatureState.DIVERGED,
                    bridge.target.path,
                    "User-owned instruction bridge is preserved.",
                )
            if (
                bridge.ownership == "generated"
                and bridge.source.content_hash != sources.bridge_hash
            ):
                drift.append(
                    DriftDetail(
                        DriftReason.SOURCE_CHANGED,
                        path=bridge.source.path,
                        expected_hash=bridge.source.content_hash,
                        actual_hash=sources.bridge_hash,
                    )
                )
        if components is not None:
            mcp = components.mcp_configuration
            desired_mcp = render_mcp_configuration(layout.client)
            if mcp.state == "generated" and mcp.content_hash != desired_mcp.target_hash:
                drift.append(
                    DriftDetail(
                        DriftReason.TARGET_SET_CHANGED,
                        path=mcp.path,
                        expected_hash=desired_mcp.target_hash,
                        actual_hash=mcp.content_hash,
                        detail="Recorded MCP config differs from the current renderer output.",
                    )
                )
            if mcp.state in {"native", "divergent"} and mcp_feature.state is FeatureState.DIVERGENT:
                drift.append(
                    DriftDetail(
                        DriftReason.MCP_DIVERGENT,
                        path=mcp.path,
                        expected_hash=mcp.content_hash,
                        detail=mcp_feature.detail,
                    )
                )
                warnings.append(f"User-owned MCP configuration is divergent: {mcp.path}")
        if authority_warning is not None or generated_modified or backup_modified:
            state = AdapterState.CONFLICT
        elif any(
            item.reason not in {DriftReason.BRIDGE_DIVERGED, DriftReason.MCP_DIVERGENT}
            for item in drift
        ):
            state = AdapterState.STALE
        else:
            state = AdapterState.CURRENT
        return AdapterStatus(
            client=layout.client,
            state=state,
            manifest_path=layout.manifest_path,
            drift=tuple(drift),
            instruction_bridge=bridge_feature,
            mcp_configuration=mcp_feature,
            warnings=tuple(warnings),
            repair_blocked=(
                authority_warning is not None
                or generated_modified
                or backup_modified
                or any(item.reason is DriftReason.SOURCE_MISSING for item in drift)
            ),
            merge_candidates=tuple(
                candidate
                for item in sorted(managed_file_states, key=lambda state: state.path)
                if (candidate := item.merge_candidate) is not None
            ),
        )

    def plan_install(self, root: Path, client: AgentClient) -> AdapterPlan:
        """Build a complete dry run for first install or idempotent reinstall."""

        try:
            return self._prepare_install_or_repair(root, client, OperationAction.INSTALL)
        except SafeFilesystemError as error:
            raise InvalidAdapterStateError(str(error)) from error

    def forget(self, root: Path) -> tuple[AgentClient, ...]:
        """Remove only machine-local trusted entries for one resolved root."""

        return self._install_records.forget(root)

    def _install_record_for_plan(
        self,
        layout: InstallationLayout,
        manifest_snapshot: FileSnapshot,
    ) -> tuple[InstallRecordObservation | None, str | None]:
        """Load stable mutation authority without changing either trust domain."""

        try:
            observation = self._install_records.observe(
                layout.root,
                layout.client,
                layout.manifest_path,
            )
        except InstallRecordError as error:
            return None, f"Trusted install record cannot authenticate this plan: {error}"

        if observation.has_pending_transition:
            return observation, (
                "Trusted install record has a pending transition; run recovery before "
                "planning another mutation"
            )
        if manifest_snapshot.exists:
            if not observation.authenticates(manifest_snapshot.content_hash):
                return observation, (
                    "Project manifest digest does not match the stable trusted user-config "
                    "install record"
                )
        elif observation.record is not None:
            return observation, (
                "Trusted user-config install record exists while the project manifest is absent"
            )
        return observation, None

    def _prepare_untracked_adoption(
        self,
        layout: InstallationLayout,
        manifest: AdapterManifest,
        manifest_snapshot: FileSnapshot,
        observations: dict[str, FileSnapshot],
        install_record: InstallRecordObservation,
        *,
        personalization: PersonalizationLayers,
        skill_overrides: SkillOverrides,
    ) -> AdapterPlan:
        """Plan only a trust-record adoption after exact project-state verification."""

        if manifest_snapshot.content_hash is None:
            raise InvalidAdapterStateError("Untracked manifest content hash is unavailable")
        expected: dict[str, str] = {manifest.registry.path: manifest.registry.content_hash}
        for skill in manifest.skills:
            expected[skill.canonical.path] = skill.canonical.content_hash
            if skill.effective_mode == "native-verified":
                assert skill.source_set is not None
                candidates = (
                    (source.path, source.content_hash) for source in skill.source_set.files
                )
            else:
                candidates = (
                    (generated.path, generated.content_hash) for generated in skill.generated or ()
                )
            for path, digest in candidates:
                previous = expected.setdefault(path, digest)
                if previous != digest:
                    raise InvalidAdapterStateError(
                        "Untracked manifest assigns conflicting hashes to one project path"
                    )
        if manifest.components is not None:
            bridge = manifest.components.instruction_bridge
            expected[bridge.target.path] = bridge.target.content_hash
            mcp = manifest.components.mcp_configuration
            if mcp.path is not None and mcp.content_hash is not None:
                expected[mcp.path] = mcp.content_hash
        for backup in manifest.backups or ():
            expected[backup.path] = backup.content_hash

        failures: list[str] = []
        for path, digest in sorted(expected.items()):
            target = _observe_plan_target(SafeRoot(layout.root), path, observations)
            if not target.exists or target.content_hash != digest:
                failures.append(path)
        if failures:
            return self._report_only_plan(
                layout,
                OperationAction.INSTALL,
                "Untracked adapter state does not exactly match its complete manifest",
                observations,
                install_record=install_record,
                affected_paths=tuple(expected),
                personalization=personalization,
                skill_overrides=skill_overrides,
            )

        changes = (
            PlannedChange(
                path=layout.manifest_path,
                operation=FileOperation.VERIFY,
                observed_hash=manifest_snapshot.content_hash,
                desired_hash=manifest_snapshot.content_hash,
                reason=(
                    "Adopt an exact untracked manifest into the machine-local trust record; "
                    "no project file will be changed"
                ),
            ),
            *(
                PlannedChange(
                    path=path,
                    operation=FileOperation.VERIFY,
                    observed_hash=digest,
                    desired_hash=digest,
                    reason="Verify exact manifest-recorded state before trust adoption",
                )
                for path, digest in sorted(expected.items())
            ),
        )
        personalization_statuses = personalization.statuses(merged=False)
        return self._save_plan(
            layout,
            OperationAction.INSTALL,
            (
                *_skill_override_plan_changes(skill_overrides),
                *_personalization_plan_changes(
                    personalization,
                    personalization_statuses,
                ),
                *changes,
            ),
            (),
            None,
            None,
            (),
            observations,
            install_record,
            personalization_layers=personalization_statuses,
            skill_overrides=skill_overrides.statuses,
            adopt_manifest_digest=manifest_snapshot.content_hash,
        )

    def _report_only_plan(
        self,
        layout: InstallationLayout,
        action: OperationAction,
        reason: str,
        observations: dict[str, FileSnapshot],
        *,
        source_fingerprint: str | None = None,
        install_record: InstallRecordObservation | None = None,
        affected_paths: tuple[str, ...] = (),
        personalization: PersonalizationLayers | None = None,
        skill_overrides: SkillOverrides | None = None,
        managed_file_states: tuple[_ManagedFileState, ...] = (),
    ) -> AdapterPlan:
        """Return a no-write plan when any D-032 authority factor fails."""

        paths = (layout.manifest_path, *affected_paths)
        changes = tuple(
            PlannedChange(
                path,
                FileOperation.PRESERVE,
                observed_hash=(observations[path].content_hash if path in observations else None),
                reason=f"Report-only: {reason}",
            )
            for path in dict.fromkeys(paths)
        )
        personalization_statuses = (
            () if personalization is None else personalization.statuses(merged=False)
        )
        skill_override_statuses = () if skill_overrides is None else skill_overrides.statuses
        if personalization is not None:
            changes = (
                *_personalization_plan_changes(personalization, personalization_statuses),
                *changes,
            )
        if skill_overrides is not None:
            changes = (*_skill_override_plan_changes(skill_overrides), *changes)
        changes = (*_managed_file_plan_changes(managed_file_states), *changes)
        return self._save_plan(
            layout,
            action,
            changes,
            (),
            source_fingerprint,
            reason,
            (),
            observations,
            install_record,
            personalization_layers=personalization_statuses,
            skill_overrides=skill_override_statuses,
            merge_candidates=tuple(
                candidate
                for state in managed_file_states
                if (candidate := state.merge_candidate) is not None
            ),
        )

    def plan_repair(self, root: Path, client: AgentClient) -> AdapterPlan:
        """Build a targeted repair dry run from current manifest ownership."""

        try:
            return self._prepare_install_or_repair(root, client, OperationAction.REPAIR)
        except SafeFilesystemError as error:
            raise InvalidAdapterStateError(str(error)) from error

    def plan_uninstall(self, root: Path, client: AgentClient) -> AdapterPlan:
        """Build a manifest-bounded uninstall dry run."""

        try:
            return self._prepare_uninstall(root, client)
        except SafeFilesystemError as error:
            raise InvalidAdapterStateError(str(error)) from error

    def _prepare_uninstall(self, root: Path, client: AgentClient) -> AdapterPlan:
        """Build uninstall details after the public filesystem-error boundary."""

        layout = derive_layout(root, client)
        self._require_settled(layout)
        safe = SafeRoot(layout.root)
        observations: dict[str, FileSnapshot] = {}
        try:
            manifest_snapshot = _observe_plan_target(
                safe,
                layout.manifest_path,
                observations,
            )
            manifest = _manifest_from_snapshot(manifest_snapshot, client)
        except SafeFilesystemError as error:
            raise InvalidAdapterStateError(str(error)) from error
        install_record, authority_error = self._install_record_for_plan(
            layout,
            manifest_snapshot,
        )
        if authority_error is not None:
            affected_paths = _manifest_mutation_targets(manifest)
            for path in affected_paths:
                _observe_plan_target(safe, path, observations)
            return self._report_only_plan(
                layout,
                OperationAction.UNINSTALL,
                authority_error,
                observations,
                install_record=install_record,
                affected_paths=affected_paths,
            )
        if manifest is None:
            return self._save_plan(
                layout,
                OperationAction.UNINSTALL,
                (),
                (),
                None,
                None,
                (),
                observations,
                install_record,
            )

        generated_inventory = _generated_inventory(manifest)
        observed_outputs = {
            path: _observe_plan_target(safe, path, observations)
            for path in sorted(generated_inventory)
        }
        authority_failures = [
            path
            for path, recorded_hash in sorted(generated_inventory.items())
            if observed_outputs[path].exists
            and observed_outputs[path].content_hash != recorded_hash
        ]
        if manifest.components is not None:
            bridge = manifest.components.instruction_bridge
            bridge_target = _observe_plan_target(safe, bridge.target.path, observations)
            if (
                bridge.ownership == "generated"
                and bridge_target.exists
                and bridge_target.content_hash != bridge.target.content_hash
            ):
                authority_failures.append(bridge.target.path)
        for backup in manifest.backups or ():
            backup_target = _observe_plan_target(safe, backup.path, observations)
            if backup_target.exists and backup_target.content_hash != backup.content_hash:
                authority_failures.append(backup.path)
        if authority_failures:
            affected_paths = _manifest_mutation_targets(manifest)
            return self._report_only_plan(
                layout,
                OperationAction.UNINSTALL,
                "One or more manifest-listed targets fail the recorded content-hash factor",
                observations,
                install_record=install_record,
                affected_paths=affected_paths,
            )

        mutations: list[FileMutation] = []
        planned: list[PlannedChange] = []
        codex_restores: dict[str, bytes] = {}
        if client is AgentClient.CODEX:
            for skill in manifest.skills:
                path = f".agents/skills/{skill.name}/SKILL.md"
                if skill.effective_mode == "generated" and {
                    item.path for item in skill.generated or ()
                } == {path}:
                    codex_restores[path] = load_packaged_skill_primary(skill.name)

        for path in sorted(generated_inventory):
            target = observed_outputs[path]
            restored = codex_restores.get(path)
            if restored is not None:
                desired_hash = content_hash(restored)
                if target.content_hash == desired_hash:
                    continue
                operation = FileOperation.REPLACE if target.exists else FileOperation.CREATE
                mutations.append(FileMutation(path, target, restored))
                planned.append(
                    PlannedChange(
                        path,
                        operation,
                        observed_hash=target.content_hash,
                        desired_hash=desired_hash,
                        reason=(
                            "Restore the packaged Codex source while uninstalling its "
                            "manifest-owned override output"
                        ),
                    )
                )
                continue
            if not target.exists:
                continue
            mutations.append(FileMutation(path, target, None))
            planned.append(
                PlannedChange(
                    path,
                    FileOperation.DELETE,
                    observed_hash=target.content_hash,
                    reason="Remove manifest-listed generated adapter output",
                )
            )

        if manifest.components is not None:
            bridge = manifest.components.instruction_bridge
            target = _observe_plan_target(safe, bridge.target.path, observations)
            if bridge.ownership == "generated" and target.exists:
                if target.content is None or target.content_hash is None:
                    raise InvalidAdapterStateError("Generated instruction bridge is unreadable")
                mutations.append(FileMutation(bridge.target.path, target, None))
                planned.append(
                    PlannedChange(
                        bridge.target.path,
                        FileOperation.DELETE,
                        observed_hash=target.content_hash,
                        reason="Remove deterministic manifest-owned instruction bridge",
                    )
                )
            mcp = manifest.components.mcp_configuration
            if mcp.path is not None:
                _observe_plan_target(safe, mcp.path, observations)

        for backup in manifest.backups or ():
            target = _observe_plan_target(safe, backup.path, observations)
            if target.exists and target.content_hash == backup.content_hash:
                mutations.append(FileMutation(backup.path, target, None))
                planned.append(
                    PlannedChange(
                        backup.path,
                        FileOperation.DELETE,
                        observed_hash=target.content_hash,
                        reason="Remove a manifest-listed retained backup during uninstall",
                    )
                )

        mutations.append(FileMutation(layout.manifest_path, manifest_snapshot, None))
        planned.append(
            PlannedChange(
                layout.manifest_path,
                FileOperation.DELETE,
                observed_hash=manifest_snapshot.content_hash,
                reason="Remove ownership manifest after all managed files",
            )
        )
        return self._save_plan(
            layout,
            OperationAction.UNINSTALL,
            tuple(planned),
            tuple(mutations),
            None,
            None,
            (),
            observations,
            install_record,
        )

    def _prepare_install_or_repair(
        self,
        root: Path,
        client: AgentClient,
        action: OperationAction,
    ) -> AdapterPlan:
        layout = derive_layout(root, client)
        self._require_settled(layout)
        self._require_supported(layout)
        safe = SafeRoot(layout.root)
        observations: dict[str, FileSnapshot] = {}
        try:
            manifest_snapshot = _observe_plan_target(
                safe,
                layout.manifest_path,
                observations,
            )
            old_manifest = _manifest_from_snapshot(manifest_snapshot, client)
            personalization = load_personalization_layers(
                layout.root,
                include_context=layout.is_context,
            )
            source_selection = self._load_source_selection(
                layout,
                old_manifest,
                personalization,
            )
        except SafeFilesystemError as error:
            raise InvalidAdapterStateError(str(error)) from error
        sources = source_selection.sources
        codex_restore_names = source_selection.codex_restore_names
        install_record, authority_error = self._install_record_for_plan(
            layout,
            manifest_snapshot,
        )
        if authority_error is not None:
            if (
                action is OperationAction.INSTALL
                and old_manifest is not None
                and install_record is not None
                and install_record.record is None
            ):
                return self._prepare_untracked_adoption(
                    layout,
                    old_manifest,
                    manifest_snapshot,
                    observations,
                    install_record,
                    personalization=sources.personalization,
                    skill_overrides=sources.skill_overrides,
                )
            affected_paths = _manifest_mutation_targets(old_manifest)
            for path in affected_paths:
                _observe_plan_target(safe, path, observations)
            return self._report_only_plan(
                layout,
                action,
                authority_error,
                observations,
                source_fingerprint=source_selection.fingerprint,
                install_record=install_record,
                affected_paths=affected_paths,
                personalization=sources.personalization,
                skill_overrides=sources.skill_overrides,
                managed_file_states=source_selection.managed_file_states,
            )

        rendered = tuple(_render_source_skill(client, skill) for skill in sources.skills)
        rendered_mcp = render_mcp_configuration(client)
        desired_outputs = {
            generated.path: generated for item in rendered for generated in item.generated_files
        }
        codex_takeovers = _codex_override_takeover_hashes(old_manifest, sources)
        codex_restore_outputs = {
            f".agents/skills/{skill.name}/SKILL.md": skill.content
            for skill in sources.skills
            if skill.name in codex_restore_names
        }
        desired_managed_hashes = {
            path: rendered_skill.target_hash for path, rendered_skill in desired_outputs.items()
        }
        desired_managed_hashes.update(
            {path: content_hash(content) for path, content in codex_restore_outputs.items()}
        )
        desired_managed_hashes[rendered_mcp.path] = rendered_mcp.target_hash
        old_skill_outputs = _generated_skill_inventory(old_manifest)
        old_outputs = _generated_inventory(old_manifest)
        observed_outputs = {
            path: _observe_plan_target(safe, path, observations) for path in sorted(old_outputs)
        }
        authority_failures = [
            path
            for path, recorded_hash in sorted(old_outputs.items())
            if observed_outputs[path].exists
            and observed_outputs[path].content_hash != recorded_hash
            and observed_outputs[path].content_hash != desired_managed_hashes.get(path)
        ]
        canonical_targets: dict[str, FileSnapshot] = {}
        for change in source_selection.canonical_changes:
            target = _observe_plan_target(safe, change.path, observations)
            canonical_targets[change.path] = target
            if change.authority_hash is None:
                if target.exists:
                    authority_failures.append(change.path)
            elif not target.exists or target.content_hash != change.authority_hash:
                authority_failures.append(change.path)
        managed_file_states = list(source_selection.managed_file_states)
        if old_manifest is not None and old_manifest.components is not None:
            old_bridge = old_manifest.components.instruction_bridge
            old_bridge_target = _observe_plan_target(
                safe,
                old_bridge.target.path,
                observations,
            )
            if (
                old_bridge.ownership == "generated"
                and old_bridge_target.exists
                and old_bridge_target.content_hash != old_bridge.target.content_hash
                and old_bridge_target.content_hash != sources.bridge_hash
            ):
                authority_failures.append(old_bridge.target.path)
            bridge_state = _bridge_managed_file_state(
                old_manifest,
                sources,
                old_bridge_target,
            )
            if bridge_state is not None:
                managed_file_states.append(bridge_state)
        for backup in () if old_manifest is None else old_manifest.backups or ():
            backup_target = _observe_plan_target(safe, backup.path, observations)
            if not backup_target.exists or backup_target.content_hash != backup.content_hash:
                authority_failures.append(backup.path)
        untracked_conflicts: list[str] = []
        for path in sorted(desired_outputs):
            if path in old_skill_outputs:
                continue
            target = _observe_plan_target(safe, path, observations)
            if not target.exists:
                continue
            takeover_hash = codex_takeovers.get(path)
            if takeover_hash is None or target.content_hash != takeover_hash:
                untracked_conflicts.append(path)
        authority_failures.extend(untracked_conflicts)
        if authority_failures:
            affected_paths = tuple(
                dict.fromkeys(
                    (
                        *_manifest_mutation_targets(old_manifest),
                        *desired_outputs,
                        sources.bridge_path,
                        rendered_mcp.path,
                    )
                )
            )
            for path in affected_paths:
                _observe_plan_target(safe, path, observations)
            return self._report_only_plan(
                layout,
                action,
                "One or more mutation targets fail the three-factor authority check",
                observations,
                source_fingerprint=source_selection.fingerprint,
                install_record=install_record,
                affected_paths=affected_paths,
                personalization=sources.personalization,
                skill_overrides=sources.skill_overrides,
                managed_file_states=tuple(
                    sorted(managed_file_states, key=lambda state: state.path)
                ),
            )
        mutations: list[FileMutation] = []
        planned: list[PlannedChange] = []
        backup_entries = list(old_manifest.backups or ()) if old_manifest is not None else []
        blocked: list[str] = []
        now_text = _timestamp(self._clock())

        for change in source_selection.canonical_changes:
            target = canonical_targets[change.path]
            if change.content is None:
                if not target.exists:
                    continue
                operation = FileOperation.DELETE
                desired_hash = None
            else:
                desired_hash = content_hash(change.content)
                if target.exists and target.content_hash == desired_hash:
                    continue
                operation = FileOperation.REPLACE if target.exists else FileOperation.CREATE
            mutations.append(FileMutation(change.path, target, change.content))
            planned.append(
                PlannedChange(
                    path=change.path,
                    operation=operation,
                    observed_hash=target.content_hash,
                    desired_hash=desired_hash,
                    reason=change.reason,
                )
            )

        for backup in backup_entries:
            backup_snapshot = _observe_plan_target(safe, backup.path, observations)
            if not backup_snapshot.exists:
                blocked.append(f"Manifest-listed retained backup is missing: {backup.path}")
            elif backup_snapshot.content_hash != backup.content_hash:
                blocked.append(f"Manifest-listed retained backup was modified: {backup.path}")

        if client is AgentClient.CODEX and sources.origin.value == "packaged":
            seed_files = {
                ".agents/skills/registry.yaml": sources.registry_content,
                **{skill.path: skill.content for skill in sources.skills if skill.override is None},
                **{
                    f".agents/skills/{skill.name}/{resource.relative_path}": resource.content
                    for skill in sources.skills
                    for resource in skill.resources
                },
            }
            for path, desired in sorted(seed_files.items()):
                target = _observe_plan_target(safe, path, observations)
                if target.exists:
                    return self._report_only_plan(
                        layout,
                        action,
                        f"Packaged canonical seed target already exists: {path}",
                        observations,
                        source_fingerprint=source_selection.fingerprint,
                        install_record=install_record,
                        affected_paths=(path,),
                        personalization=sources.personalization,
                        skill_overrides=sources.skill_overrides,
                    )
                mutations.append(FileMutation(path, target, desired))
                planned.append(
                    PlannedChange(
                        path,
                        FileOperation.CREATE,
                        desired_hash=content_hash(desired),
                        reason="Seed packaged canonical source for Codex native discovery",
                    )
                )

        for path, rendered_skill in sorted(desired_outputs.items()):
            target = _observe_plan_target(safe, path, observations)
            recorded_hash = old_skill_outputs.get(path, codex_takeovers.get(path))
            if recorded_hash is None and target.exists:
                blocked.append(f"Untracked desired target already exists: {path}")
                planned.append(
                    PlannedChange(
                        path,
                        FileOperation.PRESERVE,
                        observed_hash=target.content_hash,
                        desired_hash=rendered_skill.target_hash,
                        reason="Untracked files cannot be overwritten or approved",
                    )
                )
                continue
            if target.exists and target.content_hash == rendered_skill.target_hash:
                continue
            if not target.exists:
                mutations.append(FileMutation(path, target, rendered_skill.content))
                planned.append(
                    PlannedChange(
                        path,
                        FileOperation.CREATE,
                        desired_hash=rendered_skill.target_hash,
                        reason="Create missing native skill adapter",
                    )
                )
                continue
            mutations.append(FileMutation(path, target, rendered_skill.content))
            planned.append(
                PlannedChange(
                    path,
                    FileOperation.REPLACE,
                    observed_hash=target.content_hash,
                    desired_hash=rendered_skill.target_hash,
                    reason="Regenerate stale native skill adapter",
                )
            )

        for path in sorted(set(old_skill_outputs) - set(desired_outputs)):
            target = _observe_plan_target(safe, path, observations)
            if not target.exists:
                continue
            restored = codex_restore_outputs.get(path)
            if restored is not None:
                desired_hash = content_hash(restored)
                if target.content_hash != desired_hash:
                    mutations.append(FileMutation(path, target, restored))
                    planned.append(
                        PlannedChange(
                            path,
                            FileOperation.REPLACE,
                            observed_hash=target.content_hash,
                            desired_hash=desired_hash,
                            reason=(
                                "Restore the current packaged skill after its per-context "
                                "override was removed"
                            ),
                        )
                    )
                continue
            mutations.append(FileMutation(path, target, None))
            planned.append(
                PlannedChange(
                    path,
                    FileOperation.DELETE,
                    observed_hash=target.content_hash,
                    reason="Remove obsolete manifest-listed skill output",
                )
            )

        bridge_snapshot = _observe_plan_target(safe, sources.bridge_path, observations)
        bridge_ownership, bridge_target_hash = self._plan_bridge(
            old_manifest,
            sources,
            bridge_snapshot,
            mutations,
            planned,
            blocked,
        )
        mcp_snapshot = _observe_plan_target(safe, rendered_mcp.path, observations)
        mcp_component = self._plan_mcp_configuration(
            old_manifest,
            client,
            rendered_mcp,
            mcp_snapshot,
            mutations,
            planned,
        )
        preserved_bridge_source_hash = next(
            (
                state.recorded_at_adoption_hash
                for state in managed_file_states
                if state.path == sources.bridge_path
            ),
            None,
        )
        preserved_source_hashes = dict(source_selection.preserved_source_hashes)

        old_generated_at = old_manifest.generated_at if old_manifest is not None else now_text
        desired_manifest = self._build_manifest(
            client,
            sources,
            rendered,
            bridge_ownership,
            bridge_target_hash,
            mcp_component,
            old_generated_at,
            backup_entries,
            preserved_registry_hash=source_selection.preserved_registry_hash,
            preserved_source_hashes=preserved_source_hashes,
            preserved_bridge_source_hash=preserved_bridge_source_hash,
        )
        desired_bytes = dump_manifest_bytes(desired_manifest)
        old_bytes = manifest_snapshot.content
        if old_bytes is not None and desired_bytes != old_bytes:
            desired_manifest = self._build_manifest(
                client,
                sources,
                rendered,
                bridge_ownership,
                bridge_target_hash,
                mcp_component,
                now_text,
                backup_entries,
                preserved_registry_hash=source_selection.preserved_registry_hash,
                preserved_source_hashes=preserved_source_hashes,
                preserved_bridge_source_hash=preserved_bridge_source_hash,
            )
            desired_bytes = dump_manifest_bytes(desired_manifest)

        substantive_mutations = bool(mutations)
        if old_bytes != desired_bytes or substantive_mutations:
            manifest_operation = (
                FileOperation.CREATE if not manifest_snapshot.exists else FileOperation.REPLACE
            )
            mutations.append(FileMutation(layout.manifest_path, manifest_snapshot, desired_bytes))
            planned.append(
                PlannedChange(
                    layout.manifest_path,
                    manifest_operation,
                    observed_hash=manifest_snapshot.content_hash,
                    desired_hash=content_hash(desired_bytes),
                    reason="Commit adapter ownership manifest last",
                )
            )
        blocked_reason = "; ".join(blocked) or None
        personalization_statuses = sources.personalization.statuses(
            merged=bridge_ownership == "generated" and blocked_reason is None
        )
        planned = [
            *_skill_override_plan_changes(sources.skill_overrides),
            *_managed_file_plan_changes(
                tuple(sorted(managed_file_states, key=lambda state: state.path))
            ),
            *_personalization_plan_changes(
                sources.personalization,
                personalization_statuses,
            ),
            *planned,
        ]
        return self._save_plan(
            layout,
            action,
            tuple(planned),
            tuple(mutations),
            source_selection.fingerprint,
            blocked_reason,
            (),
            observations,
            install_record,
            personalization_layers=personalization_statuses,
            skill_overrides=sources.skill_overrides.statuses,
            merge_candidates=tuple(
                candidate
                for state in sorted(managed_file_states, key=lambda item: item.path)
                if (candidate := state.merge_candidate) is not None
            ),
            codex_restore_names=codex_restore_names,
        )

    def _plan_bridge(
        self,
        old_manifest: AdapterManifest | None,
        sources: CanonicalSourceSet,
        target: FileSnapshot,
        mutations: list[FileMutation],
        planned: list[PlannedChange],
        blocked: list[str],
    ) -> tuple[Literal["generated", "user-owned"], str]:
        old_bridge = (
            old_manifest.components.instruction_bridge
            if old_manifest is not None and old_manifest.components is not None
            else None
        )
        if old_bridge is not None and old_bridge.ownership == "user-owned":
            # A recorded user-owned bridge whose CURRENT bytes are exactly a
            # shipped context-template generation was misclassified at an
            # earlier install (the template ships the file); heal it like a
            # fresh one.
            if (
                target.exists
                and target.content_hash is not None
                and target.content_hash in _pristine_template_bridge_hashes(sources.bridge_path)
            ):
                mutations.append(FileMutation(sources.bridge_path, target, sources.bridge_content))
                planned.append(
                    PlannedChange(
                        sources.bridge_path,
                        FileOperation.REPLACE,
                        observed_hash=target.content_hash,
                        desired_hash=sources.bridge_hash,
                        reason=(
                            "Replace the pristine context-template bridge previously "
                            "recorded as user-owned"
                        ),
                    )
                )
                return "generated", sources.bridge_hash
            return (
                "user-owned",
                target.content_hash
                if target.exists and target.content_hash is not None
                else old_bridge.target.content_hash,
            )
        if not target.exists:
            mutations.append(FileMutation(sources.bridge_path, target, sources.bridge_content))
            planned.append(
                PlannedChange(
                    sources.bridge_path,
                    FileOperation.CREATE,
                    desired_hash=sources.bridge_hash,
                    reason="Generate missing project instruction bridge from packaged template",
                )
            )
            return "generated", sources.bridge_hash
        if target.content_hash is None:
            raise InvalidAdapterStateError("Instruction bridge content hash is unavailable")
        if old_bridge is None:
            if target.content_hash in _pristine_template_bridge_hashes(sources.bridge_path):
                mutations.append(FileMutation(sources.bridge_path, target, sources.bridge_content))
                planned.append(
                    PlannedChange(
                        sources.bridge_path,
                        FileOperation.REPLACE,
                        observed_hash=target.content_hash,
                        desired_hash=sources.bridge_hash,
                        reason=(
                            "Replace the pristine context-template bridge with the "
                            "packaged adapter bridge"
                        ),
                    )
                )
                return "generated", sources.bridge_hash
            return "user-owned", target.content_hash
        if (
            target.content_hash != old_bridge.target.content_hash
            and target.content_hash == sources.bridge_hash
        ):
            return "generated", sources.bridge_hash
        if target.content_hash != old_bridge.target.content_hash:
            blocked.append(
                "Generated instruction bridge differs from its authenticated manifest record"
            )
            planned.append(
                PlannedChange(
                    sources.bridge_path,
                    FileOperation.PRESERVE,
                    observed_hash=target.content_hash,
                    desired_hash=sources.bridge_hash,
                    reason="Report-only: generated bridge ownership authority failed",
                )
            )
            return "generated", old_bridge.target.content_hash
        if target.content_hash != sources.bridge_hash:
            mutations.append(FileMutation(sources.bridge_path, target, sources.bridge_content))
            planned.append(
                PlannedChange(
                    sources.bridge_path,
                    FileOperation.REPLACE,
                    observed_hash=target.content_hash,
                    desired_hash=sources.bridge_hash,
                    reason="Regenerate an authenticated stale instruction bridge",
                )
            )
            return "generated", sources.bridge_hash
        return "generated", sources.bridge_hash

    @staticmethod
    def _plan_mcp_configuration(
        old_manifest: AdapterManifest | None,
        client: AgentClient,
        desired: RenderedMcpConfiguration,
        target: FileSnapshot,
        mutations: list[FileMutation],
        planned: list[PlannedChange],
    ) -> McpConfigurationComponent:
        """Generate only when absent and preserve every user-owned client config."""

        old_component = (
            old_manifest.components.mcp_configuration
            if old_manifest is not None and old_manifest.components is not None
            else None
        )
        if old_component is not None and old_component.state in {"native", "divergent"}:
            assert old_component.content_hash is not None
            if not target.exists:
                return McpConfigurationComponent(
                    state="divergent",
                    path=desired.path,
                    content_hash=old_component.content_hash,
                )
            if target.content is None or target.content_hash is None:
                raise InvalidAdapterStateError("User-owned MCP configuration is unreadable")
            state: Literal["native", "divergent"] = (
                "native" if mcp_configuration_is_equivalent(client, target.content) else "divergent"
            )
            return McpConfigurationComponent(
                state=state,
                path=desired.path,
                content_hash=target.content_hash,
            )

        if target.exists and (old_component is None or old_component.state == "not_implemented"):
            if target.content is None or target.content_hash is None:
                raise InvalidAdapterStateError("User-owned MCP configuration is unreadable")
            existing_state: Literal["native", "divergent"] = (
                "native" if mcp_configuration_is_equivalent(client, target.content) else "divergent"
            )
            return McpConfigurationComponent(
                state=existing_state,
                path=desired.path,
                content_hash=target.content_hash,
            )

        if not target.exists:
            mutations.append(FileMutation(desired.path, target, desired.content))
            planned.append(
                PlannedChange(
                    desired.path,
                    FileOperation.CREATE,
                    desired_hash=desired.target_hash,
                    reason="Generate missing project-scoped Work Context MCP configuration",
                )
            )
        elif target.content_hash != desired.target_hash:
            mutations.append(FileMutation(desired.path, target, desired.content))
            planned.append(
                PlannedChange(
                    desired.path,
                    FileOperation.REPLACE,
                    observed_hash=target.content_hash,
                    desired_hash=desired.target_hash,
                    reason="Regenerate authenticated stale Work Context MCP configuration",
                )
            )
        return McpConfigurationComponent(
            state="generated",
            path=desired.path,
            content_hash=desired.target_hash,
        )

    @staticmethod
    def _build_manifest(
        client: AgentClient,
        sources: CanonicalSourceSet,
        rendered: tuple[RenderedSkill, ...],
        bridge_ownership: Literal["generated", "user-owned"],
        bridge_target_hash: str,
        mcp_configuration: McpConfigurationComponent,
        generated_at: str,
        backups: list[BackupEntry],
        *,
        preserved_registry_hash: str | None = None,
        preserved_source_hashes: Mapping[str, str] | None = None,
        preserved_bridge_source_hash: str | None = None,
    ) -> AdapterManifest:
        source_hash_overrides = {} if preserved_source_hashes is None else preserved_source_hashes
        skills: list[SkillAdapterEntry] = []
        canonical_skills = {skill.name: skill for skill in sources.skills}
        for skill in rendered:
            canonical_skill = canonical_skills[skill.name]
            canonical = CanonicalSource(
                path=skill.canonical_path,
                content_hash=source_hash_overrides.get(
                    skill.canonical_path,
                    skill.canonical_hash,
                ),
            )
            if skill.mode == "native-verified":
                skills.append(
                    SkillAdapterEntry(
                        name=skill.name,
                        mode="native-verified",
                        canonical=canonical,
                        source_set=NativeSourceSet(
                            files=[
                                NativeSourceFile(
                                    path=path,
                                    content_hash=source_hash_overrides.get(path, digest),
                                )
                                for path, digest in canonical_skill.source_files
                            ],
                            aggregate_hash=source_set_aggregate_hash(
                                (
                                    path,
                                    source_hash_overrides.get(path, digest),
                                )
                                for path, digest in canonical_skill.source_files
                            ),
                        ),
                    )
                )
            else:
                skills.append(
                    SkillAdapterEntry(
                        name=skill.name,
                        mode="generated",
                        canonical=canonical,
                        generated=[
                            GeneratedFile(
                                path=generated.path,
                                content_hash=generated.target_hash,
                            )
                            for generated in skill.generated_files
                        ],
                    )
                )
        bridge_name = cast(
            Literal["AGENTS.md", "CLAUDE.md", "GEMINI.md"],
            sources.bridge_path,
        )
        return AdapterManifest(
            schema_version=1,
            adapter=client.value,
            adapter_version=ADAPTER_VERSION,
            scope="project",
            generated_at=generated_at,
            registry=RegistrySource(
                path=".agents/skills/registry.yaml",
                content_hash=preserved_registry_hash or sources.registry_hash,
            ),
            skills=skills,
            components=AdapterComponents(
                instruction_bridge=InstructionBridgeComponent(
                    ownership=bridge_ownership,
                    source=BridgeSource(
                        path=bridge_name,
                        content_hash=(
                            preserved_bridge_source_hash
                            or (
                                sources.bridge_hash
                                if bridge_ownership == "generated"
                                else sources.bridge_template_hash
                            )
                        ),
                    ),
                    target=BridgeTarget(
                        path=bridge_name,
                        content_hash=bridge_target_hash,
                    ),
                ),
                mcp_configuration=mcp_configuration,
            ),
            backups=backups,
        )

    def _save_plan(
        self,
        layout: InstallationLayout,
        action: OperationAction,
        changes: tuple[PlannedChange, ...],
        mutations: tuple[FileMutation, ...],
        source_fingerprint: str | None,
        blocked_reason: str | None,
        backup_paths: tuple[str, ...],
        observations: dict[str, FileSnapshot],
        install_record: InstallRecordObservation | None = None,
        *,
        personalization_layers: tuple[PersonalizationLayerStatus, ...] = (),
        skill_overrides: tuple[SkillOverrideStatus, ...] = (),
        merge_candidates: tuple[ManagedFileMerge, ...] = (),
        codex_restore_names: frozenset[str] = frozenset(),
        adopt_manifest_digest: str | None = None,
    ) -> AdapterPlan:
        for mutation in mutations:
            observed = observations.get(mutation.path)
            if observed is None or not observed.matches(
                mutation.expected.identity,
                mutation.expected.content_hash,
            ):
                raise InvalidAdapterStateError(
                    f"Mutation lacks its exact dry-run target snapshot: {mutation.path}"
                )
        target_snapshots = tuple(sorted(observations.items()))
        operations_digest = (
            mutation_operations_digest(
                mutations,
                client=layout.client,
                manifest_path=layout.manifest_path,
            )
            if mutations
            else None
        )
        next_manifest_digest: str | None = None
        if mutations:
            if mutations[-1].path != layout.manifest_path:
                raise InvalidAdapterStateError("The ownership manifest mutation must be last")
            next_manifest_digest = mutations[-1].desired_hash
        if adopt_manifest_digest is not None and (
            mutations or install_record is None or install_record.record is not None
        ):
            raise InvalidAdapterStateError(
                "Trust adoption requires an untracked, non-mutating install plan"
            )
        digest = _plan_digest(
            layout.root,
            layout.client,
            action,
            changes,
            source_fingerprint,
            blocked_reason,
            target_snapshots,
            None if install_record is None else install_record.fingerprint,
            adopt_manifest_digest,
        )
        plan = AdapterPlan(
            root=layout.root,
            client=layout.client,
            action=action,
            changes=changes,
            plan_hash=digest,
            source_fingerprint=source_fingerprint,
            blocked_reason=blocked_reason,
            personalization_layers=personalization_layers,
            skill_overrides=skill_overrides,
            merge_candidates=merge_candidates,
            adopts_trust=adopt_manifest_digest is not None,
        )
        self._prepared[digest] = _PreparedPlan(
            plan=plan,
            layout=layout,
            mutations=mutations,
            backup_paths=backup_paths,
            target_snapshots=target_snapshots,
            install_record=install_record,
            next_manifest_digest=next_manifest_digest,
            operations_digest=operations_digest,
            codex_restore_names=codex_restore_names,
            adopt_manifest_digest=adopt_manifest_digest,
        )
        return plan

    @staticmethod
    def _revalidate_plan_targets(prepared: _PreparedPlan) -> None:
        """Require every observed target to retain its exact dry-run preimage."""

        safe = SafeRoot(prepared.layout.root)
        for path, expected in prepared.target_snapshots:
            try:
                current = safe.inspect_file(path)
            except SafeFilesystemError as error:
                raise InvalidAdapterStateError(
                    f"Plan target became unsafe before apply: {path}"
                ) from error
            if not current.matches(expected.identity, expected.content_hash):
                raise AdapterConflictError(f"Plan target changed after dry run; replan: {path}")

    def _require_supported(self, layout: InstallationLayout) -> None:
        capability = self._detector.detect_one(layout.root, layout.client)
        if capability.availability is ClientAvailability.AVAILABLE:
            return
        if capability.availability is ClientAvailability.UNSUPPORTED:
            raise UnsupportedClientVersionError(
                capability.detail or f"Unsupported {layout.client.value} version"
            )
        if capability.availability is ClientAvailability.INVALID:
            raise InvalidAdapterStateError(capability.detail or "Unsafe client marker state")
        raise UnavailableDependencyError(
            f"The {layout.client.value} executable is unavailable; native format cannot "
            "be verified."
        )

    def _require_settled(self, layout: InstallationLayout) -> None:
        lock = inspect_adapter_lock(layout, now=self._clock())
        if lock.invalid:
            raise InvalidAdapterStateError(lock.detail or "Invalid adapter lock")
        if lock.live:
            raise AdapterConflictError("Another writer holds the project adapter lock")
        transaction = inspect_transactions(layout)
        if transaction.invalid:
            raise InvalidAdapterStateError(transaction.detail or "Invalid adapter staging state")
        if transaction.intents:
            raise RecoveryRequiredError("An adapter transaction must be recovered before planning")

    @staticmethod
    def _validate_approvals(
        plan: AdapterPlan,
        approvals: tuple[TargetApproval, ...],
    ) -> None:
        required = {
            (
                change.path,
                change.operation,
                change.observed_hash,
                change.desired_hash,
            )
            for change in plan.changes
            if change.requires_approval
        }
        supplied = {
            (
                approval.path,
                approval.operation,
                approval.observed_hash,
                approval.desired_hash,
            )
            for approval in approvals
        }
        if supplied != required:
            raise InvalidApprovalError(
                "Approvals must exactly match every destructive path, operation, observed hash, "
                "and desired hash in the dry run."
            )

    def apply_plan(
        self,
        plan: AdapterPlan,
        *,
        approvals: tuple[TargetApproval, ...] = (),
    ) -> OperationResult:
        """Apply one plan after exact approval and source/preimage revalidation."""

        prepared = self._prepared.get(plan.plan_hash)
        if prepared is None or prepared.plan != plan:
            raise InvalidAdapterStateError("Plan was not prepared by this service instance")
        if plan.blocked_reason is not None:
            raise AdapterConflictError(plan.blocked_reason)
        self._validate_approvals(plan, approvals)
        with AdapterLock.acquire(
            prepared.layout,
            session_id=self._session_id(),
            now=self._clock(),
        ) as lock:
            if plan.action in {OperationAction.INSTALL, OperationAction.REPAIR}:
                self._require_supported(prepared.layout)
            if plan.source_fingerprint is not None:
                locked_personalization = load_personalization_layers(
                    prepared.layout.root,
                    include_context=prepared.layout.is_context,
                )
                locked_manifest_snapshot = SafeRoot(prepared.layout.root).inspect_file(
                    prepared.layout.manifest_path
                )
                locked_manifest = _manifest_from_snapshot(
                    locked_manifest_snapshot,
                    plan.client,
                )
                locked_selection = self._load_source_selection(
                    prepared.layout,
                    locked_manifest,
                    locked_personalization,
                )
                if locked_selection.fingerprint != plan.source_fingerprint:
                    raise AdapterConflictError(
                        "Canonical sources changed before lock-held preflight; replan"
                    )
            transaction = inspect_transactions(prepared.layout)
            if transaction.invalid:
                raise InvalidAdapterStateError(
                    transaction.detail or "Invalid adapter staging state"
                )
            if transaction.intents:
                raise RecoveryRequiredError("An adapter transaction requires recovery")
            self._revalidate_plan_targets(prepared)
            lock.verify()
            if not prepared.mutations and prepared.install_record is not None:
                current_install_record = self._install_records.observe(
                    prepared.layout.root,
                    plan.client,
                    prepared.layout.manifest_path,
                )
                if current_install_record != prepared.install_record:
                    raise AdapterConflictError(
                        "Trusted install record changed after dry run; replan"
                    )
            changed: tuple[str, ...]
            if prepared.adopt_manifest_digest is not None:
                assert prepared.install_record is not None
                self._install_records.adopt(
                    prepared.install_record,
                    manifest_digest=prepared.adopt_manifest_digest,
                )
                changed = ()
            elif prepared.mutations:
                if prepared.install_record is None or prepared.operations_digest is None:
                    raise InvalidAdapterStateError(
                        "A mutating plan lacks its trusted install-record preflight"
                    )
                pending = self._install_records.begin_transition(
                    prepared.install_record,
                    next_manifest_digest=prepared.next_manifest_digest,
                    operations_digest=prepared.operations_digest,
                )

                def require_pending_authority() -> None:
                    current_pending = self._install_records.observe(
                        prepared.layout.root,
                        plan.client,
                        prepared.layout.manifest_path,
                    ).pending
                    if current_pending != pending:
                        raise AdapterConflictError(
                            "Trusted install transition changed immediately before project mutation"
                        )

                require_pending_authority()
                try:
                    changed = AtomicAdapterTransaction(
                        prepared.layout,
                        plan.client,
                        lock,
                    ).apply(
                        prepared.mutations,
                        target_snapshots=prepared.target_snapshots,
                        authority_check=require_pending_authority,
                    )
                except Exception:
                    transaction_after_failure = inspect_transactions(prepared.layout)
                    if (
                        not transaction_after_failure.invalid
                        and not transaction_after_failure.intents
                    ):
                        try:
                            current_authority = self._install_records.observe(
                                prepared.layout.root,
                                plan.client,
                                prepared.layout.manifest_path,
                            )
                        except InstallRecordError:
                            current_authority = None
                        if current_authority is not None and current_authority.pending == pending:
                            try:
                                actual_manifest = SafeRoot(prepared.layout.root).inspect_file(
                                    prepared.layout.manifest_path
                                )
                                self._install_records.resolve_transition(
                                    pending,
                                    operations_digest=prepared.operations_digest,
                                    actual_manifest_digest=actual_manifest.content_hash,
                                )
                            except (
                                InstallRecordError,
                                InstallRecordConflictError,
                                SafeFilesystemError,
                            ) as resolution_error:
                                raise RecoveryRequiredError(
                                    "The project transaction settled, but its trusted install "
                                    "transition still requires recovery"
                                ) from resolution_error
                    raise
                try:
                    actual_manifest = SafeRoot(prepared.layout.root).inspect_file(
                        prepared.layout.manifest_path
                    )
                    self._install_records.resolve_transition(
                        pending,
                        operations_digest=prepared.operations_digest,
                        actual_manifest_digest=actual_manifest.content_hash,
                    )
                except (
                    InstallRecordError,
                    InstallRecordConflictError,
                    SafeFilesystemError,
                ) as error:
                    raise RecoveryRequiredError(
                        "Project files committed, but the trusted install transition "
                        "requires recovery"
                    ) from error
            else:
                changed = ()
        self._prepared.pop(plan.plan_hash, None)
        return OperationResult(
            plan.root,
            plan.client,
            plan.action,
            changed,
            backups=prepared.backup_paths,
            no_op=not prepared.mutations and prepared.adopt_manifest_digest is None,
        )

    def install(
        self,
        plan: AdapterPlan,
        *,
        approvals: tuple[TargetApproval, ...] = (),
    ) -> OperationResult:
        """Apply an explicitly reviewed install plan."""

        if plan.action is not OperationAction.INSTALL:
            raise ValueError("Expected an install plan")
        return self.apply_plan(plan, approvals=approvals)

    def repair(
        self,
        plan: AdapterPlan,
        *,
        approvals: tuple[TargetApproval, ...] = (),
    ) -> OperationResult:
        """Apply a targeted repair after source and target revalidation."""

        if plan.action is not OperationAction.REPAIR:
            raise ValueError("Expected a repair plan")
        return self.apply_plan(plan, approvals=approvals)

    def uninstall(
        self,
        plan: AdapterPlan,
        *,
        approvals: tuple[TargetApproval, ...] = (),
    ) -> OperationResult:
        """Remove only manifest-owned files, preserving unmanaged and user-owned files."""

        if plan.action is not OperationAction.UNINSTALL:
            raise ValueError("Expected an uninstall plan")
        return self.apply_plan(plan, approvals=approvals)

    def recover(self, root: Path, client: AgentClient) -> OperationResult:
        """Resolve only a project intent authenticated by the trusted transition."""

        layout = derive_layout(root, client)
        transaction = inspect_transactions(layout)
        if transaction.invalid:
            raise InvalidAdapterStateError(transaction.detail or "Invalid staging state")
        observation = self._install_records.observe(
            layout.root,
            client,
            layout.manifest_path,
        )
        if not transaction.intents and not observation.has_pending_transition:
            return OperationResult(
                layout.root,
                client,
                OperationAction.RECOVER,
                (),
                no_op=True,
            )
        with AdapterLock.acquire(
            layout,
            session_id=self._session_id(),
            now=self._clock(),
        ) as lock:
            locked_transaction = inspect_transactions(layout)
            if locked_transaction.invalid:
                raise InvalidAdapterStateError(locked_transaction.detail or "Invalid staging state")
            locked_observation = self._install_records.observe(
                layout.root,
                client,
                layout.manifest_path,
            )
            pending = locked_observation.pending
            if not locked_transaction.intents:
                if pending is None:
                    return OperationResult(
                        layout.root,
                        client,
                        OperationAction.RECOVER,
                        (),
                        no_op=True,
                    )
                trusted_transition = pending.transition
                try:
                    actual_manifest = SafeRoot(layout.root).inspect_file(layout.manifest_path)
                    self._install_records.resolve_transition(
                        pending,
                        operations_digest=trusted_transition.operations_digest,
                        actual_manifest_digest=actual_manifest.content_hash,
                    )
                except InstallRecordConflictError as error:
                    raise RecoveryConflictError(str(error)) from error
                except SafeFilesystemError as error:
                    raise InvalidAdapterStateError(str(error)) from error
                return OperationResult(
                    layout.root,
                    client,
                    OperationAction.RECOVER,
                    (),
                    no_op=False,
                )

            if pending is None:
                raise RecoveryConflictError(
                    "Project transaction has no matching pending trusted install record"
                )
            project_transition = inspect_recovery_transition(layout, client)
            if project_transition is None:  # pragma: no cover - fenced above
                raise RecoveryConflictError("Project transaction disappeared before recovery")
            trusted_transition = pending.transition
            if (
                project_transition.manifest_before != trusted_transition.from_manifest_digest
                or project_transition.manifest_after != trusted_transition.to_manifest_digest
                or project_transition.operations_digest != trusted_transition.operations_digest
            ):
                raise RecoveryConflictError(
                    "Project transaction does not match the pending trusted transition"
                )
            try:
                actual_manifest = SafeRoot(layout.root).inspect_file(layout.manifest_path)
                self._install_records.verify_recovery(
                    pending,
                    operations_digest=project_transition.operations_digest,
                    actual_manifest_digest=actual_manifest.content_hash,
                )
            except InstallRecordConflictError as error:
                raise RecoveryConflictError(str(error)) from error
            except SafeFilesystemError as error:
                raise InvalidAdapterStateError(str(error)) from error

            def require_recovery_authority() -> None:
                current_pending = self._install_records.observe(
                    layout.root,
                    client,
                    layout.manifest_path,
                ).pending
                if current_pending != pending:
                    raise RecoveryConflictError(
                        "Trusted install transition changed immediately before recovery write"
                    )

            changed = recover_transaction(
                layout,
                client,
                lock,
                expected_transition=project_transition,
                authority_check=require_recovery_authority,
            )
            try:
                actual_manifest = SafeRoot(layout.root).inspect_file(layout.manifest_path)
                self._install_records.resolve_transition(
                    pending,
                    operations_digest=project_transition.operations_digest,
                    actual_manifest_digest=actual_manifest.content_hash,
                )
            except (InstallRecordError, InstallRecordConflictError) as error:
                raise RecoveryRequiredError(
                    "Project recovery settled, but the trusted transition remains pending"
                ) from error
            except SafeFilesystemError as error:
                raise InvalidAdapterStateError(str(error)) from error
        return OperationResult(
            layout.root,
            client,
            OperationAction.RECOVER,
            changed,
        )
