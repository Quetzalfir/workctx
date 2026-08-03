"""Typed install, status, targeted repair, recovery, and safe uninstall APIs."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
    OpenedContext,
    OperationAction,
    OperationResult,
    PlannedChange,
    TargetApproval,
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
    CanonicalSourceSet,
    load_canonical_sources,
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


def _is_safe_skill_output(path: str, client: AgentClient, skill_name: str | None = None) -> bool:
    parts = path.split("/")
    if len(parts) < 4 or parts[0] != f".{client.value}" or parts[1] != "skills":
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
        rendered = render_skill(
            client,
            name=skill.name,
            canonical_content=skill.content,
            side_effect_class=skill.side_effect_class,
            resource_contents=tuple(
                (resource.relative_path, resource.content) for resource in skill.resources
            ),
        )
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

    def status(self, root: Path, client: AgentClient) -> AdapterStatus:
        """Derive status without writing, following the normative precedence order."""

        layout = derive_layout(root, client)
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
            sources = load_canonical_sources(layout.root, client)
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
        return self._derive_freshness(
            layout,
            manifest,
            sources,
            transaction.orphan_directories,
            authority_warning,
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
        sources: CanonicalSourceSet,
        orphan_directories: tuple[str, ...],
        authority_warning: str | None,
    ) -> AdapterStatus:
        safe = SafeRoot(layout.root)
        drift: list[DriftDetail] = []
        warnings = [f"Orphan staging directory: {path}" for path in orphan_directories]
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
            expected = render_skill(
                layout.client,
                name=name,
                canonical_content=current.content,
                side_effect_class=current.side_effect_class,
                resource_contents=tuple(
                    (resource.relative_path, resource.content) for resource in current.resources
                ),
            )
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
                if target.content_hash != bridge.target.content_hash:
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
        )

    def plan_install(self, root: Path, client: AgentClient) -> AdapterPlan:
        """Build a complete dry run for first install or idempotent reinstall."""

        try:
            return self._prepare_install_or_repair(root, client, OperationAction.INSTALL)
        except SafeFilesystemError as error:
            raise InvalidAdapterStateError(str(error)) from error

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

        for path in sorted(generated_inventory):
            target = observed_outputs[path]
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
            sources = load_canonical_sources(layout.root, client)
        except SafeFilesystemError as error:
            raise InvalidAdapterStateError(str(error)) from error
        install_record, authority_error = self._install_record_for_plan(
            layout,
            manifest_snapshot,
        )
        if authority_error is not None:
            affected_paths = _manifest_mutation_targets(old_manifest)
            for path in affected_paths:
                _observe_plan_target(safe, path, observations)
            return self._report_only_plan(
                layout,
                action,
                authority_error,
                observations,
                source_fingerprint=sources.fingerprint,
                install_record=install_record,
                affected_paths=affected_paths,
            )

        rendered = tuple(
            render_skill(
                client,
                name=skill.name,
                canonical_content=skill.content,
                side_effect_class=skill.side_effect_class,
                resource_contents=tuple(
                    (resource.relative_path, resource.content) for resource in skill.resources
                ),
            )
            for skill in sources.skills
        )
        rendered_mcp = render_mcp_configuration(client)
        desired_outputs = {
            generated.path: generated for item in rendered for generated in item.generated_files
        }
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
        ]
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
            ):
                authority_failures.append(old_bridge.target.path)
        for backup in () if old_manifest is None else old_manifest.backups or ():
            backup_target = _observe_plan_target(safe, backup.path, observations)
            if not backup_target.exists or backup_target.content_hash != backup.content_hash:
                authority_failures.append(backup.path)
        untracked_conflicts = [
            path
            for path in sorted(desired_outputs)
            if path not in old_skill_outputs
            and _observe_plan_target(safe, path, observations).exists
        ]
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
                source_fingerprint=sources.fingerprint,
                install_record=install_record,
                affected_paths=affected_paths,
            )
        mutations: list[FileMutation] = []
        planned: list[PlannedChange] = []
        backup_entries = list(old_manifest.backups or ()) if old_manifest is not None else []
        blocked: list[str] = []
        now_text = _timestamp(self._clock())

        for backup in backup_entries:
            backup_snapshot = _observe_plan_target(safe, backup.path, observations)
            if not backup_snapshot.exists:
                blocked.append(f"Manifest-listed retained backup is missing: {backup.path}")
            elif backup_snapshot.content_hash != backup.content_hash:
                blocked.append(f"Manifest-listed retained backup was modified: {backup.path}")

        if client is AgentClient.CODEX and sources.origin.value == "packaged":
            seed_files = {
                ".agents/skills/registry.yaml": sources.registry_content,
                **{skill.path: skill.content for skill in sources.skills},
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
                        source_fingerprint=sources.fingerprint,
                        install_record=install_record,
                        affected_paths=(path,),
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
            recorded_hash = old_skill_outputs.get(path)
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
        return self._save_plan(
            layout,
            action,
            tuple(planned),
            tuple(mutations),
            sources.fingerprint,
            blocked_reason,
            (),
            observations,
            install_record,
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
            return "user-owned", old_bridge.target.content_hash
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
            return "user-owned", target.content_hash
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
    ) -> AdapterManifest:
        skills: list[SkillAdapterEntry] = []
        canonical_skills = {skill.name: skill for skill in sources.skills}
        for skill in rendered:
            canonical_skill = canonical_skills[skill.name]
            canonical = CanonicalSource(
                path=skill.canonical_path,
                content_hash=skill.canonical_hash,
            )
            if skill.mode == "native-verified":
                skills.append(
                    SkillAdapterEntry(
                        name=skill.name,
                        mode="native-verified",
                        canonical=canonical,
                        source_set=NativeSourceSet(
                            files=[
                                NativeSourceFile(path=path, content_hash=digest)
                                for path, digest in canonical_skill.source_files
                            ],
                            aggregate_hash=canonical_skill.source_set_hash,
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
                content_hash=sources.registry_hash,
            ),
            skills=skills,
            components=AdapterComponents(
                instruction_bridge=InstructionBridgeComponent(
                    ownership=bridge_ownership,
                    source=BridgeSource(
                        path=bridge_name,
                        content_hash=sources.bridge_hash,
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
        digest = _plan_digest(
            layout.root,
            layout.client,
            action,
            changes,
            source_fingerprint,
            blocked_reason,
            target_snapshots,
            None if install_record is None else install_record.fingerprint,
        )
        plan = AdapterPlan(
            root=layout.root,
            client=layout.client,
            action=action,
            changes=changes,
            plan_hash=digest,
            source_fingerprint=source_fingerprint,
            blocked_reason=blocked_reason,
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
                locked_sources = load_canonical_sources(plan.root, plan.client)
                if locked_sources.fingerprint != plan.source_fingerprint:
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
            if prepared.mutations:
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
            no_op=not prepared.mutations,
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
