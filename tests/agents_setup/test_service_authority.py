from __future__ import annotations

import json
from pathlib import Path

import pytest

from workctx.adapters.agents._install_records import (
    InstallRecordObservation,
    PendingInstallRecord,
    TrustedInstallStore,
)
from workctx.adapters.agents._safe_fs import FileSnapshot, SafeRoot
from workctx.adapters.agents.errors import AdapterConflictError, RecoveryConflictError
from workctx.adapters.agents.layout import derive_layout
from workctx.adapters.agents.models import (
    AdapterState,
    AgentClient,
    FileOperation,
)
from workctx.adapters.agents.renderers import content_hash
from workctx.adapters.agents.service import AgentAdapterService

_FIRST_SKILL = "first-skill"
_SECOND_SKILL = "second-skill"
_MANIFEST_PATH = ".workctx/agent-adapters/claude/skill-manifest.json"
_FIRST_TARGET = ".claude/skills/first-skill/SKILL.md"
_SECOND_TARGET = ".claude/skills/second-skill/SKILL.md"


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    assert executable == "fake-claude"
    return "Claude Code 2.0.0"


def _service() -> AgentAdapterService:
    return AgentAdapterService(
        executable_finder=_finder,
        version_probe=_probe,
        session_id_factory=lambda: "authority-test-session",
    )


def _skill_bytes(name: str, body: str) -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use when exercising trusted adapter authority behavior.\n"
        "---\n\n"
        f"{body}\n"
    ).encode()


def _project(tmp_path: Path, *, two_skills: bool = False) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    names = [_FIRST_SKILL]
    if two_skills:
        names.append(_SECOND_SKILL)
    skills = project / ".agents" / "skills"
    skills.mkdir(parents=True)
    registry_lines = ["schema_version: 1", "skills:"]
    for name in names:
        registry_lines.extend(
            [
                f"  - id: {name}",
                "    side_effect_class: read_only",
            ]
        )
        skill = skills / name / "SKILL.md"
        skill.parent.mkdir()
        skill.write_bytes(_skill_bytes(name, f"# {name}"))
    (skills / "registry.yaml").write_text(
        "\n".join(registry_lines) + "\n",
        encoding="utf-8",
    )
    return project


def _record_file() -> Path:
    return TrustedInstallStore().path


def _install(service: AgentAdapterService, project: Path) -> None:
    service.install(service.plan_install(project, AgentClient.CLAUDE))


def _project_files(project: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


@pytest.mark.parametrize("authority_state", ("missing", "tampered"))
def test_missing_or_tampered_trusted_record_makes_repair_and_uninstall_report_only(
    tmp_path: Path,
    authority_state: str,
) -> None:
    project = _project(tmp_path)
    record_file = _record_file()
    service = _service()
    _install(service, project)
    canonical = project / ".agents" / "skills" / _FIRST_SKILL / "SKILL.md"
    canonical.write_bytes(_skill_bytes(_FIRST_SKILL, "# Canonical revision"))

    if authority_state == "missing":
        record_file.unlink()
    else:
        payload = json.loads(record_file.read_text(encoding="utf-8"))
        payload["projects"][0]["adapters"][0]["trusted_manifest_digest"] = "sha256:" + "f" * 64
        record_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    before = _project_files(project)
    status = service.status(project, AgentClient.CLAUDE)
    repair = service.plan_repair(project, AgentClient.CLAUDE)
    uninstall = service.plan_uninstall(project, AgentClient.CLAUDE)

    assert status.repair_blocked
    assert any("trusted" in warning.casefold() for warning in status.warnings)
    for plan in (repair, uninstall):
        assert plan.blocked_reason is not None
        assert plan.changes
        assert {change.operation for change in plan.changes} == {FileOperation.PRESERVE}
        assert {change.path for change in plan.changes} == {
            _MANIFEST_PATH,
            _FIRST_TARGET,
            "CLAUDE.md",
            ".mcp.json",
        }
        with pytest.raises(AdapterConflictError):
            service.apply_plan(plan)
        assert _project_files(project) == before


def test_modified_manifest_target_makes_whole_repair_and_uninstall_report_only(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, two_skills=True)
    service = _service()
    _install(service, project)
    first_target = project / Path(_FIRST_TARGET)
    second_target = project / Path(_SECOND_TARGET)
    first_target.write_bytes(b"operator-owned target bytes\n")
    second_source = project / ".agents" / "skills" / _SECOND_SKILL / "SKILL.md"
    second_source.write_bytes(_skill_bytes(_SECOND_SKILL, "# Canonical revision"))
    before = _project_files(project)

    status = service.status(project, AgentClient.CLAUDE)
    repair = service.plan_repair(project, AgentClient.CLAUDE)
    uninstall = service.plan_uninstall(project, AgentClient.CLAUDE)

    assert status.state is AdapterState.CONFLICT
    assert status.repair_blocked
    for plan in (repair, uninstall):
        assert plan.blocked_reason is not None
        assert plan.changes
        assert {change.operation for change in plan.changes} == {FileOperation.PRESERVE}
        assert all(not change.requires_approval for change in plan.changes)
        with pytest.raises(AdapterConflictError):
            service.apply_plan(plan)
        assert _project_files(project) == before
    assert second_target.read_bytes() == before[_SECOND_TARGET]


def test_stable_trusted_install_allows_targeted_canonical_drift_repair(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = _service()
    _install(service, project)
    manifest = project / Path(_MANIFEST_PATH)
    trusted = TrustedInstallStore().observe(
        project,
        AgentClient.CLAUDE,
        _MANIFEST_PATH,
    )
    assert trusted.authenticates(content_hash(manifest.read_bytes()))
    canonical = project / ".agents" / "skills" / _FIRST_SKILL / "SKILL.md"
    canonical.write_bytes(_skill_bytes(_FIRST_SKILL, "# Canonical revision"))
    target = project / Path(_FIRST_TARGET)
    target_before = target.read_bytes()

    status = service.status(project, AgentClient.CLAUDE)
    plan = service.plan_repair(project, AgentClient.CLAUDE)

    assert status.state is AdapterState.STALE
    assert not status.repair_blocked
    assert plan.blocked_reason is None
    assert {change.path for change in plan.changes} == {
        _FIRST_TARGET,
        _MANIFEST_PATH,
    }
    assert all(not change.requires_approval for change in plan.changes)
    result = service.repair(plan)

    assert set(result.changed_paths) == {_FIRST_TARGET, _MANIFEST_PATH}
    assert target.read_bytes() != target_before
    assert b"# Canonical revision" in target.read_bytes()
    assert service.status(project, AgentClient.CLAUDE).state is AdapterState.CURRENT


def test_noop_plan_revalidates_its_trusted_record_observation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    record_file = _record_file()
    service = _service()
    _install(service, project)
    plan = service.plan_install(project, AgentClient.CLAUDE)
    assert plan.is_noop

    record_file.unlink()

    with pytest.raises(AdapterConflictError, match="Trusted install record changed"):
        service.install(plan)


def test_transition_is_rechecked_immediately_before_first_project_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    record_file = _record_file()
    service = _service()
    _install(service, project)
    canonical = project / ".agents" / "skills" / _FIRST_SKILL / "SKILL.md"
    canonical.write_bytes(_skill_bytes(_FIRST_SKILL, "# Canonical revision"))
    plan = service.plan_repair(project, AgentClient.CLAUDE)
    before = _project_files(project)
    begin_transition = service._install_records.begin_transition

    def begin_then_remove_record(
        observation: InstallRecordObservation,
        *,
        next_manifest_digest: str | None,
        operations_digest: str,
    ) -> PendingInstallRecord:
        pending = begin_transition(
            observation,
            next_manifest_digest=next_manifest_digest,
            operations_digest=operations_digest,
        )
        record_file.unlink()
        return pending

    monkeypatch.setattr(
        service._install_records,
        "begin_transition",
        begin_then_remove_record,
    )

    with pytest.raises(AdapterConflictError, match="immediately before project mutation"):
        service.repair(plan)

    assert _project_files(project) == before


def test_transition_is_rechecked_after_intent_flush_before_first_project_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    record_file = _record_file()
    service = _service()
    _install(service, project)
    canonical = project / ".agents" / "skills" / _FIRST_SKILL / "SKILL.md"
    canonical.write_bytes(_skill_bytes(_FIRST_SKILL, "# Canonical revision"))
    plan = service.plan_repair(project, AgentClient.CLAUDE)
    before = _project_files(project)
    write_exclusive = SafeRoot.write_exclusive

    def write_then_remove_record(
        safe: SafeRoot,
        relative_path: str,
        content: bytes,
        *,
        mode: int = 0o600,
    ) -> FileSnapshot:
        snapshot = write_exclusive(safe, relative_path, content, mode=mode)
        if relative_path.endswith("/intent.json"):
            record_file.unlink()
        return snapshot

    monkeypatch.setattr(SafeRoot, "write_exclusive", write_then_remove_record)

    with pytest.raises(AdapterConflictError, match="immediately before project mutation"):
        service.repair(plan)

    assert _project_files(project) == before


def test_forged_project_intent_without_pending_trusted_transition_cannot_recover(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    record_file = _record_file()
    service = _service()
    _install(service, project)
    layout = derive_layout(project, AgentClient.CLAUDE)
    transaction_id = "a" * 32
    transaction_root = f"{layout.staging_path}/{transaction_id}"
    root_path = project / Path(transaction_root)
    for child in ("staged", "verified", "removed"):
        (root_path / child).mkdir(parents=True, exist_ok=True)
    target = project / Path(_FIRST_TARGET)
    manifest = project / Path(_MANIFEST_PATH)
    target_bytes = target.read_bytes()
    manifest_bytes = manifest.read_bytes()
    target_hash = content_hash(target_bytes)
    manifest_hash = content_hash(manifest_bytes)
    (root_path / "verified" / "0000.pre").write_bytes(target_bytes)
    (root_path / "staged" / "0001.post").write_bytes(manifest_bytes)
    (root_path / "verified" / "0001.pre").write_bytes(manifest_bytes)
    intent_path = root_path / "intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "lock_nonce": "b" * 32,
                "adapter": "claude",
                "manifest_path": layout.manifest_path,
                "operations": [
                    {
                        "operation": "delete",
                        "target": _FIRST_TARGET,
                        "expected": target_hash,
                        "postimage": "absent",
                        "staged": None,
                        "backup": f"{transaction_root}/verified/0000.pre",
                        "removed": f"{transaction_root}/removed/0000.pre",
                    },
                    {
                        "operation": "replace",
                        "target": _MANIFEST_PATH,
                        "expected": manifest_hash,
                        "postimage": manifest_hash,
                        "staged": f"{transaction_root}/staged/0001.post",
                        "backup": f"{transaction_root}/verified/0001.pre",
                        "removed": None,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    record_before = record_file.read_bytes()

    assert service.status(project, AgentClient.CLAUDE).state is AdapterState.RECOVERY_REQUIRED
    with pytest.raises(RecoveryConflictError, match=r"pending|trusted"):
        service.recover(project, AgentClient.CLAUDE)

    assert intent_path.is_file()
    assert target.read_bytes() == target_bytes
    assert manifest.read_bytes() == manifest_bytes
    assert record_file.read_bytes() == record_before


def test_pending_trusted_transition_is_recovery_required_and_resolves_preimage(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = _service()
    _install(service, project)
    manifest = project / Path(_MANIFEST_PATH)
    manifest_digest = content_hash(manifest.read_bytes())
    store = TrustedInstallStore()
    stable = store.observe(project, AgentClient.CLAUDE, _MANIFEST_PATH)
    store.begin_transition(
        stable,
        next_manifest_digest="sha256:" + "e" * 64,
        operations_digest="sha256:" + "d" * 64,
    )

    pending_status = service.status(project, AgentClient.CLAUDE)
    assert pending_status.state is AdapterState.RECOVERY_REQUIRED
    assert pending_status.repair_blocked

    service.recover(project, AgentClient.CLAUDE)

    resolved = store.observe(project, AgentClient.CLAUDE, _MANIFEST_PATH)
    assert resolved.authenticates(manifest_digest)
    assert service.status(project, AgentClient.CLAUDE).state is AdapterState.CURRENT
