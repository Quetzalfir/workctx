from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from workctx.adapters.agents._lock import AdapterLock
from workctx.adapters.agents._safe_fs import FileSnapshot, SafeRoot, UnsafeFilesystemError
from workctx.adapters.agents._transaction import (
    AtomicAdapterTransaction,
    FileMutation,
    inspect_transactions,
)
from workctx.adapters.agents.errors import (
    InvalidAdapterStateError,
    RecoveryConflictError,
    RecoveryRequiredError,
)
from workctx.adapters.agents.layout import derive_layout
from workctx.adapters.agents.models import AdapterState, AgentClient
from workctx.adapters.agents.service import AgentAdapterService

_SKILL_NAME = "fixture-skill"


def _service() -> AgentAdapterService:
    return AgentAdapterService(
        executable_finder=lambda name: f"fake-{name}",
        version_probe=lambda executable, _root: {
            "fake-codex": "0.5.0",
            "fake-claude": "2.0.0",
            "fake-gemini": "0.5.0",
        }[executable],
        session_id_factory=lambda: "test-session",
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    skill = project / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    skill.parent.mkdir(parents=True)
    (project / ".agents" / "skills" / "registry.yaml").write_text(
        f"schema_version: 1\nskills:\n  - id: {_SKILL_NAME}\n    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    skill.write_text(
        "---\n"
        f"name: {_SKILL_NAME}\n"
        "description: Use when testing rollback and recovery transaction behavior.\n"
        "---\n\n"
        "# Fixture skill\n",
        encoding="utf-8",
    )
    return project


def _leave_committed_intent(
    project: Path,
    service: AgentAdapterService,
    monkeypatch: Any,
    *,
    repair: bool = False,
) -> tuple[object, str, dict[str, object]]:
    """Interrupt cleanup after every postimage, leaving one parseable intent."""

    original_cleanup = AtomicAdapterTransaction._resolve_and_cleanup

    def interrupt_cleanup(
        self: AtomicAdapterTransaction,
        transaction_root: str,
        intent_path: str,
        operations: list[dict[str, object]],
        mutations: tuple[FileMutation, ...],
        intent_snapshot: FileSnapshot,
        *,
        resolved_state: str,
        target_snapshots: tuple[tuple[str, FileSnapshot], ...] = (),
    ) -> None:
        raise RecoveryRequiredError("injected crash after commit")

    monkeypatch.setattr(AtomicAdapterTransaction, "_resolve_and_cleanup", interrupt_cleanup)
    plan = (
        service.plan_repair(project, AgentClient.CLAUDE)
        if repair
        else service.plan_install(project, AgentClient.CLAUDE)
    )
    with pytest.raises(RecoveryRequiredError, match="injected crash after commit"):
        if repair:
            service.repair(plan)
        else:
            service.install(plan)
    monkeypatch.setattr(AtomicAdapterTransaction, "_resolve_and_cleanup", original_cleanup)

    layout = derive_layout(project, AgentClient.CLAUDE)
    inspection = inspect_transactions(layout)
    assert not inspection.invalid
    assert len(inspection.intents) == 1
    intent_path = inspection.intents[0]
    intent = SafeRoot(project).inspect_file(intent_path)
    assert intent.content is not None
    value = json.loads(intent.content)
    assert isinstance(value, dict)
    return layout, intent_path, value


def test_safe_intentless_staging_tree_is_only_an_orphan_warning(tmp_path: Path) -> None:
    project = _project(tmp_path)
    layout = derive_layout(project, AgentClient.CLAUDE)
    orphan = project / Path(layout.staging_path) / ("a" * 32) / "staged"
    orphan.mkdir(parents=True)
    (orphan / "0000.post").write_bytes(b"safe orphan bytes")

    inspection = inspect_transactions(layout)
    status = _service().status(project, AgentClient.CLAUDE)

    assert not inspection.invalid
    assert inspection.orphan_directories == (f"{layout.staging_path}/{'a' * 32}",)
    assert status.state is AdapterState.NOT_INSTALLED
    assert status.warnings == (f"Orphan staging directory: {layout.staging_path}/{'a' * 32}",)


def test_unsafe_intentless_staging_tree_is_invalid_and_blocks_planning(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside canary")
    layout = derive_layout(project, AgentClient.CLAUDE)
    orphan = project / Path(layout.staging_path) / ("b" * 32) / "staged"
    orphan.mkdir(parents=True)
    linked = orphan / "0000.post"
    try:
        linked.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    inspection = inspect_transactions(layout)

    assert inspection.invalid
    assert _service().status(project, AgentClient.CLAUDE).state is AdapterState.INVALID
    with pytest.raises(InvalidAdapterStateError, match=r"reparse|regular|safe|unsafe"):
        _service().plan_install(project, AgentClient.CLAUDE)
    assert outside.read_bytes() == b"outside canary"


def test_boolean_intent_schema_version_is_invalid(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    layout, intent_path, value = _leave_committed_intent(project, service, monkeypatch)
    value["schema_version"] = True
    (project / Path(intent_path)).write_text(
        json.dumps(value, sort_keys=True),
        encoding="utf-8",
    )

    inspection = inspect_transactions(layout)  # type: ignore[arg-type]

    assert inspection.invalid
    assert not inspection.intents


def test_mid_apply_failure_rolls_back_all_targets_and_clears_intent(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    original_replace = SafeRoot.replace
    calls = 0

    def fail_second_replace(
        self: SafeRoot,
        source: str,
        target: str,
        **preconditions: object,
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replace failure")
        return original_replace(self, source, target, **preconditions)  # type: ignore[arg-type]

    monkeypatch.setattr(SafeRoot, "replace", fail_second_replace)
    plan = service.plan_install(project, AgentClient.CLAUDE)
    with pytest.raises(OSError, match="injected replace failure"):
        service.install(plan)

    layout = derive_layout(project, AgentClient.CLAUDE)
    inspection = inspect_transactions(layout)
    assert not inspection.invalid
    assert inspection.intents == ()
    assert not (project / ".claude" / "skills" / _SKILL_NAME / "SKILL.md").exists()
    assert not (project / "CLAUDE.md").exists()
    assert not (project / Path(layout.manifest_path)).exists()


def test_crash_after_all_postimages_can_be_recovered_by_fresh_writer(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    original_cleanup = AtomicAdapterTransaction._resolve_and_cleanup
    interrupted = False

    def fail_first_cleanup(
        self: AtomicAdapterTransaction,
        transaction_root: str,
        intent_path: str,
        operations: list[dict[str, object]],
        mutations: tuple[FileMutation, ...],
        intent_snapshot: FileSnapshot,
        *,
        resolved_state: str,
        target_snapshots: tuple[tuple[str, FileSnapshot], ...] = (),
    ) -> None:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise RecoveryRequiredError("injected crash after commit")
        original_cleanup(
            self,
            transaction_root,
            intent_path,
            operations,
            mutations,
            intent_snapshot,
            resolved_state=resolved_state,
            target_snapshots=target_snapshots,
        )

    monkeypatch.setattr(AtomicAdapterTransaction, "_resolve_and_cleanup", fail_first_cleanup)
    plan = service.plan_install(project, AgentClient.CLAUDE)
    with pytest.raises(RecoveryRequiredError, match="injected crash after commit"):
        service.install(plan)
    monkeypatch.setattr(AtomicAdapterTransaction, "_resolve_and_cleanup", original_cleanup)

    assert service.status(project, AgentClient.CLAUDE).state is AdapterState.RECOVERY_REQUIRED
    result = service.recover(project, AgentClient.CLAUDE)

    assert not result.no_op
    assert service.status(project, AgentClient.CLAUDE).state is AdapterState.CURRENT
    assert inspect_transactions(derive_layout(project, AgentClient.CLAUDE)).intents == ()


def test_live_cleanup_requires_the_exact_intent_written_by_apply(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    original_cleanup = AtomicAdapterTransaction._resolve_and_cleanup

    def replace_intent_before_cleanup(
        self: AtomicAdapterTransaction,
        transaction_root: str,
        intent_path: str,
        operations: list[dict[str, object]],
        mutations: tuple[FileMutation, ...],
        intent_snapshot: FileSnapshot,
        *,
        resolved_state: str,
        target_snapshots: tuple[tuple[str, FileSnapshot], ...] = (),
    ) -> None:
        intent_file = project / Path(intent_path)
        intent_file.write_bytes(intent_file.read_bytes() + b" ")
        original_cleanup(
            self,
            transaction_root,
            intent_path,
            operations,
            mutations,
            intent_snapshot,
            resolved_state=resolved_state,
            target_snapshots=target_snapshots,
        )

    monkeypatch.setattr(
        AtomicAdapterTransaction,
        "_resolve_and_cleanup",
        replace_intent_before_cleanup,
    )
    with pytest.raises(RecoveryConflictError, match="intent changed during recovery"):
        service.install(service.plan_install(project, AgentClient.CLAUDE))
    monkeypatch.setattr(AtomicAdapterTransaction, "_resolve_and_cleanup", original_cleanup)

    inspection = inspect_transactions(derive_layout(project, AgentClient.CLAUDE))
    assert not inspection.invalid
    assert len(inspection.intents) == 1


def test_live_cleanup_rechecks_full_target_set_before_intent_removal(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    original_cleanup = AtomicAdapterTransaction._resolve_and_cleanup
    changed_target: Path | None = None

    def alter_target_before_cleanup(
        self: AtomicAdapterTransaction,
        transaction_root: str,
        intent_path: str,
        operations: list[dict[str, object]],
        mutations: tuple[FileMutation, ...],
        intent_snapshot: FileSnapshot,
        *,
        resolved_state: str,
        target_snapshots: tuple[tuple[str, FileSnapshot], ...] = (),
    ) -> None:
        nonlocal changed_target
        changed_target = project / Path(mutations[0].path)
        changed_target.write_bytes(b"unrecorded concurrent content\n")
        original_cleanup(
            self,
            transaction_root,
            intent_path,
            operations,
            mutations,
            intent_snapshot,
            resolved_state=resolved_state,
            target_snapshots=target_snapshots,
        )

    monkeypatch.setattr(
        AtomicAdapterTransaction,
        "_resolve_and_cleanup",
        alter_target_before_cleanup,
    )
    with pytest.raises(RecoveryRequiredError, match="targets changed before cleanup"):
        service.install(service.plan_install(project, AgentClient.CLAUDE))
    monkeypatch.setattr(AtomicAdapterTransaction, "_resolve_and_cleanup", original_cleanup)

    assert changed_target is not None
    assert changed_target.read_bytes() == b"unrecorded concurrent content\n"
    inspection = inspect_transactions(derive_layout(project, AgentClient.CLAUDE))
    assert not inspection.invalid
    assert len(inspection.intents) == 1


def test_all_postimage_recovery_rechecks_targets_before_removing_intent(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    _layout, intent_path, value = _leave_committed_intent(project, service, monkeypatch)
    operations = value["operations"]
    assert isinstance(operations, list)
    first = operations[0]
    assert isinstance(first, dict)
    target = first["target"]
    assert isinstance(target, str)
    target_path = project / Path(target)
    original_inspect = SafeRoot.inspect_file
    target_reads = 0

    def alter_after_initial_classification(self: SafeRoot, path: str) -> object:
        nonlocal target_reads
        snapshot = original_inspect(self, path)
        if path == target:
            target_reads += 1
            if target_reads == 1:
                target_path.write_bytes(b"unrecorded concurrent content\n")
        return snapshot

    monkeypatch.setattr(SafeRoot, "inspect_file", alter_after_initial_classification)

    with pytest.raises(RecoveryConflictError, match="changed before transaction resolution"):
        service.recover(project, AgentClient.CLAUDE)

    monkeypatch.setattr(SafeRoot, "inspect_file", original_inspect)
    assert target_path.read_bytes() == b"unrecorded concurrent content\n"
    assert (project / Path(intent_path)).exists()


def test_partial_recovery_rechecks_backup_before_restore(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    service.install(service.plan_install(project, AgentClient.CLAUDE))
    canonical = project / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    canonical.write_text(canonical.read_text(encoding="utf-8") + "\nUpdated.\n", encoding="utf-8")
    _layout, intent_path, value = _leave_committed_intent(
        project,
        service,
        monkeypatch,
        repair=True,
    )
    operations = value["operations"]
    assert isinstance(operations, list)
    manifest = operations[-1]
    selected = operations[-2]
    assert isinstance(manifest, dict)
    assert isinstance(selected, dict)
    manifest_target = manifest["target"]
    manifest_backup = manifest["backup"]
    selected_target = selected["target"]
    selected_backup = selected["backup"]
    assert isinstance(manifest_target, str)
    assert isinstance(manifest_backup, str)
    assert isinstance(selected_target, str)
    assert isinstance(selected_backup, str)
    (project / Path(manifest_target)).write_bytes((project / Path(manifest_backup)).read_bytes())
    selected_postimage = (project / Path(selected_target)).read_bytes()
    selected_backup_path = project / Path(selected_backup)
    original_inspect = SafeRoot.inspect_file
    backup_reads = 0

    def alter_after_initial_backup_validation(self: SafeRoot, path: str) -> object:
        nonlocal backup_reads
        snapshot = original_inspect(self, path)
        if path == selected_backup:
            backup_reads += 1
            if backup_reads == 1:
                selected_backup_path.write_bytes(b"unrecorded backup content\n")
        return snapshot

    monkeypatch.setattr(SafeRoot, "inspect_file", alter_after_initial_backup_validation)

    with pytest.raises(RecoveryConflictError, match="preimage evidence was modified"):
        service.recover(project, AgentClient.CLAUDE)

    monkeypatch.setattr(SafeRoot, "inspect_file", original_inspect)
    assert (project / Path(selected_target)).read_bytes() == selected_postimage
    assert selected_backup_path.read_bytes() == b"unrecorded backup content\n"
    assert (project / Path(intent_path)).exists()


def test_partial_recovery_verifies_intent_identity_after_rollback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    _layout, intent_path, value = _leave_committed_intent(project, service, monkeypatch)
    operations = value["operations"]
    assert isinstance(operations, list)
    remaining = operations[0]
    assert isinstance(remaining, dict)
    remaining_target = remaining["target"]
    assert isinstance(remaining_target, str)
    for operation in operations[1:]:
        assert isinstance(operation, dict)
        target = operation["target"]
        assert isinstance(target, str)
        (project / Path(target)).unlink()

    original_unlink = SafeRoot.unlink
    changed_intent = False

    def alter_intent_after_rollback_unlink(
        self: SafeRoot,
        path: str,
        **preconditions: object,
    ) -> bool:
        nonlocal changed_intent
        result = original_unlink(self, path, **preconditions)  # type: ignore[arg-type]
        if path == remaining_target and not changed_intent:
            changed_intent = True
            intent_file = project / Path(intent_path)
            intent_file.write_bytes(intent_file.read_bytes() + b" ")
        return result

    monkeypatch.setattr(SafeRoot, "unlink", alter_intent_after_rollback_unlink)

    with pytest.raises(RecoveryConflictError, match="intent changed during recovery"):
        service.recover(project, AgentClient.CLAUDE)

    monkeypatch.setattr(SafeRoot, "unlink", original_unlink)
    assert not (project / Path(remaining_target)).exists()
    assert (project / Path(intent_path)).exists()


def test_recovery_restarts_after_preimage_restore_consumes_backup(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    service = _service()
    service.install(service.plan_install(project, AgentClient.CLAUDE))
    canonical = project / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    canonical.write_text(canonical.read_text(encoding="utf-8") + "\nUpdated.\n", encoding="utf-8")
    _layout, intent_path, value = _leave_committed_intent(
        project,
        service,
        monkeypatch,
        repair=True,
    )
    operations = value["operations"]
    assert isinstance(operations, list)
    manifest = operations[-1]
    selected = operations[-2]
    assert isinstance(manifest, dict)
    assert isinstance(selected, dict)
    manifest_target = manifest["target"]
    manifest_backup = manifest["backup"]
    selected_target = selected["target"]
    selected_backup = selected["backup"]
    assert isinstance(manifest_target, str)
    assert isinstance(manifest_backup, str)
    assert isinstance(selected_target, str)
    assert isinstance(selected_backup, str)
    (project / Path(manifest_target)).write_bytes((project / Path(manifest_backup)).read_bytes())

    original_replace = SafeRoot.replace
    interrupted = False

    def interrupt_after_restore(
        self: SafeRoot,
        source: str,
        target: str,
        **preconditions: object,
    ) -> object:
        nonlocal interrupted
        result = original_replace(self, source, target, **preconditions)  # type: ignore[arg-type]
        if source == selected_backup and target == selected_target and not interrupted:
            interrupted = True
            raise OSError("injected crash after preimage restore")
        return result

    monkeypatch.setattr(SafeRoot, "replace", interrupt_after_restore)
    with pytest.raises(OSError, match="injected crash after preimage restore"):
        service.recover(project, AgentClient.CLAUDE)
    monkeypatch.setattr(SafeRoot, "replace", original_replace)

    assert not (project / Path(selected_backup)).exists()
    assert (project / Path(intent_path)).exists()
    result = service.recover(project, AgentClient.CLAUDE)
    assert not result.no_op
    assert inspect_transactions(derive_layout(project, AgentClient.CLAUDE)).intents == ()


def test_heartbeat_cannot_overwrite_a_new_lock_owner(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    layout = derive_layout(project, AgentClient.CLAUDE)
    lock = AdapterLock.acquire(layout, session_id="first-owner")
    original_replace = SafeRoot.replace
    takeover_nonce = "f" * 32
    takeover_done = False

    def replace_after_takeover(
        self: SafeRoot,
        source: str,
        target: str,
        **preconditions: object,
    ) -> object:
        nonlocal takeover_done
        if target == layout.lock_path and ".heartbeat-" in source and not takeover_done:
            takeover_done = True
            existing = self.inspect_file(target)
            assert existing.content is not None
            payload = json.loads(existing.content)
            payload["session_id"] = "new-owner"
            payload["nonce"] = takeover_nonce
            payload["heartbeat_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            self.unlink(target)
            self.write_exclusive(target, (json.dumps(payload, indent=2) + "\n").encode())
        return original_replace(self, source, target, **preconditions)  # type: ignore[arg-type]

    monkeypatch.setattr(SafeRoot, "replace", replace_after_takeover)

    with pytest.raises(UnsafeFilesystemError, match="target precondition changed"):
        lock.heartbeat()

    current = SafeRoot(project).inspect_file(layout.lock_path)
    assert current.content is not None
    assert json.loads(current.content)["nonce"] == takeover_nonce


def test_release_cannot_delete_a_new_lock_owner(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    project = _project(tmp_path)
    layout = derive_layout(project, AgentClient.CLAUDE)
    lock = AdapterLock.acquire(layout, session_id="first-owner")
    original_unlink = SafeRoot.unlink
    takeover_nonce = "e" * 32
    takeover_done = False

    def unlink_after_takeover(
        self: SafeRoot,
        path: str,
        **preconditions: object,
    ) -> bool:
        nonlocal takeover_done
        if path == layout.lock_path and not takeover_done:
            takeover_done = True
            existing = self.inspect_file(path)
            assert existing.content is not None
            payload = json.loads(existing.content)
            payload["session_id"] = "new-owner"
            payload["nonce"] = takeover_nonce
            payload["heartbeat_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            original_unlink(self, path)
            self.write_exclusive(path, (json.dumps(payload, indent=2) + "\n").encode())
        return original_unlink(self, path, **preconditions)  # type: ignore[arg-type]

    monkeypatch.setattr(SafeRoot, "unlink", unlink_after_takeover)

    with pytest.raises(UnsafeFilesystemError, match="Unlink precondition changed"):
        lock.release()

    current = SafeRoot(project).inspect_file(layout.lock_path)
    assert current.content is not None
    assert json.loads(current.content)["nonce"] == takeover_nonce
