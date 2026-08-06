from __future__ import annotations

from pathlib import Path

import pytest

import workctx.adapters.agents.sources as sources_module
from workctx.adapters.agents.manifest import load_manifest
from workctx.adapters.agents.models import AgentClient, FileOperation
from workctx.adapters.agents.renderers import content_hash
from workctx.adapters.agents.service import AgentAdapterService
from workctx.cli import _adapter_plan_payload, _adapter_status_payload
from workctx.services.contexts import initialize_context

_SKILL_NAME = "manage-tasks"
_SKILL_PATH = f".agents/skills/{_SKILL_NAME}/SKILL.md"
_REGISTRY_PATH = ".agents/skills/registry.yaml"
_NEW_SKILL = "fictional-freshness"


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    assert executable == "fake-codex"
    return "codex 0.5.0"


def _service() -> AgentAdapterService:
    return AgentAdapterService(
        executable_finder=_finder,
        version_probe=_probe,
        session_id_factory=lambda: "managed-freshness-session",
    )


def _installed_context(tmp_path: Path, context_id: str) -> tuple[Path, AgentAdapterService]:
    root = tmp_path / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    service = _service()
    service.install(service.plan_install(root, AgentClient.CODEX))
    return root, service


def _patch_packaged_kit(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: bytes | None = None,
    skills: dict[str, bytes] | None = None,
    bridges: dict[str, bytes] | None = None,
) -> None:
    original_file = sources_module._packaged_file
    original_resources = sources_module._packaged_skill_resources
    replacements = {} if skills is None else skills
    bridge_replacements = {} if bridges is None else bridges

    def packaged_file(*parts: str) -> bytes:
        if parts == ("skills", "registry.yaml") and registry is not None:
            return registry
        if len(parts) == 3 and parts[:1] == ("skills",) and parts[2] == "SKILL.md":
            replacement = replacements.get(parts[1])
            if replacement is not None:
                return replacement
        if len(parts) == 2 and parts[0] == "bridges":
            replacement = bridge_replacements.get(parts[1])
            if replacement is not None:
                return replacement
        return original_file(*parts)

    def packaged_resources(name: str):
        if name == _NEW_SKILL:
            return ()
        return original_resources(name)

    monkeypatch.setattr(sources_module, "_packaged_file", packaged_file)
    monkeypatch.setattr(sources_module, "_packaged_skill_resources", packaged_resources)


@pytest.mark.parametrize("operator_edited", [False, True], ids=["pristine", "edited"])
def test_tracked_skill_pristine_vs_edited_freshness_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator_edited: bool,
) -> None:
    root, service = _installed_context(tmp_path, f"skill-{operator_edited}")
    skill = root / Path(_SKILL_PATH)
    recorded = skill.read_bytes()
    packaged_now = recorded + b"\nFictional packaged freshness revision.\n"
    if operator_edited:
        skill.write_bytes(recorded + b"\nFictional operator-managed revision.\n")
    local_before = skill.read_bytes()
    _patch_packaged_kit(monkeypatch, skills={_SKILL_NAME: packaged_now})

    status = service.status(root, AgentClient.CODEX)
    plan = service.plan_install(root, AgentClient.CODEX)
    path_changes = [change for change in plan.changes if change.path == _SKILL_PATH]

    if not operator_edited:
        assert [(change.operation, change.desired_hash) for change in path_changes] == [
            (FileOperation.REPLACE, content_hash(packaged_now))
        ]
        assert status.merge_candidates == ()
        assert plan.merge_candidates == ()
        service.install(plan)
        assert skill.read_bytes() == packaged_now
        return

    expected = (
        _SKILL_PATH,
        content_hash(recorded),
        content_hash(packaged_now),
        content_hash(local_before),
    )
    assert [(change.operation, change.observed_hash) for change in path_changes] == [
        (FileOperation.PRESERVE, content_hash(local_before))
    ]
    assert [
        (
            item.path,
            item.recorded_at_adoption_hash,
            item.packaged_now_hash,
            item.local_hash,
        )
        for item in status.merge_candidates
    ] == [expected]
    assert [
        (
            item.path,
            item.recorded_at_adoption_hash,
            item.packaged_now_hash,
            item.local_hash,
        )
        for item in plan.merge_candidates
    ] == [expected]
    receipt = service.install(plan)
    assert receipt.no_op
    assert skill.read_bytes() == local_before


@pytest.mark.parametrize("operator_edited", [False, True], ids=["pristine", "edited"])
def test_registry_pristine_vs_edited_freshness_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator_edited: bool,
) -> None:
    root, service = _installed_context(tmp_path, f"registry-{operator_edited}")
    registry = root / Path(_REGISTRY_PATH)
    recorded = registry.read_bytes()
    packaged_now = recorded + b"# fictional packaged registry revision\n"
    if operator_edited:
        registry.write_bytes(recorded + b"# fictional operator registry revision\n")
    local_before = registry.read_bytes()
    _patch_packaged_kit(monkeypatch, registry=packaged_now)

    status = service.status(root, AgentClient.CODEX)
    plan = service.plan_install(root, AgentClient.CODEX)
    path_changes = [change for change in plan.changes if change.path == _REGISTRY_PATH]

    if not operator_edited:
        assert [change.operation for change in path_changes] == [FileOperation.REPLACE]
        service.install(plan)
        assert registry.read_bytes() == packaged_now
        return

    expected = (
        _REGISTRY_PATH,
        content_hash(recorded),
        content_hash(packaged_now),
        content_hash(local_before),
    )
    assert [change.operation for change in path_changes] == [FileOperation.PRESERVE]
    assert [
        (
            item.path,
            item.recorded_at_adoption_hash,
            item.packaged_now_hash,
            item.local_hash,
        )
        for item in status.merge_candidates
    ] == [expected]
    assert [
        (
            item.path,
            item.recorded_at_adoption_hash,
            item.packaged_now_hash,
            item.local_hash,
        )
        for item in plan.merge_candidates
    ] == [expected]
    service.install(plan)
    assert registry.read_bytes() == local_before


def test_new_packaged_skill_and_registry_appear_on_reinstall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, service = _installed_context(tmp_path, "new-packaged-skill")
    registry = root / Path(_REGISTRY_PATH)
    packaged_registry = registry.read_bytes() + (
        f"  - id: {_NEW_SKILL}\n    side_effect_class: read_only\n".encode()
    )
    packaged_skill = (
        "---\n"
        f"name: {_NEW_SKILL}\n"
        "description: Use when checking a fictional packaged freshness transition.\n"
        "---\n\n"
        "# Fictional freshness\n\nInspect only fictional local state.\n"
    ).encode()
    _patch_packaged_kit(
        monkeypatch,
        registry=packaged_registry,
        skills={_NEW_SKILL: packaged_skill},
    )

    plan = service.plan_install(root, AgentClient.CODEX)

    assert {
        (change.path, change.operation)
        for change in plan.changes
        if change.path
        in {
            _REGISTRY_PATH,
            f".agents/skills/{_NEW_SKILL}/SKILL.md",
        }
    } == {
        (_REGISTRY_PATH, FileOperation.REPLACE),
        (f".agents/skills/{_NEW_SKILL}/SKILL.md", FileOperation.CREATE),
    }
    service.install(plan)
    assert registry.read_bytes() == packaged_registry
    assert (root / ".agents" / "skills" / _NEW_SKILL / "SKILL.md").read_bytes() == (packaged_skill)
    manifest_path = root / "98_state" / "agent-adapters" / "codex" / "skill-manifest.json"
    manifest = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    assert _NEW_SKILL in {skill.name for skill in manifest.skills}


def test_edited_outdated_generated_bridge_surfaces_exact_three_way_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, service = _installed_context(tmp_path, "bridge-merge")
    bridge = root / "AGENTS.md"
    recorded = bridge.read_bytes()
    local = recorded + b"\nFictional operator bridge instruction.\n"
    packaged_now = recorded + b"\nFictional packaged bridge revision.\n"
    bridge.write_bytes(local)
    _patch_packaged_kit(monkeypatch, bridges={"AGENTS.md": packaged_now})

    status = service.status(root, AgentClient.CODEX)
    plan = service.plan_install(root, AgentClient.CODEX)
    expected = (
        "AGENTS.md",
        content_hash(recorded),
        content_hash(packaged_now),
        content_hash(local),
    )

    assert [
        (
            item.path,
            item.recorded_at_adoption_hash,
            item.packaged_now_hash,
            item.local_hash,
        )
        for item in status.merge_candidates
    ] == [expected]
    assert [
        (
            item.path,
            item.recorded_at_adoption_hash,
            item.packaged_now_hash,
            item.local_hash,
        )
        for item in plan.merge_candidates
    ] == [expected]
    assert plan.blocked_reason is not None
    assert bridge.read_bytes() == local
    expected_json = [
        {
            "path": "AGENTS.md",
            "recorded_at_adoption_hash": content_hash(recorded),
            "packaged_now_hash": content_hash(packaged_now),
            "local_hash": content_hash(local),
        }
    ]
    assert _adapter_status_payload(status)["merge_candidates"] == expected_json
    assert _adapter_plan_payload(plan)["merge_candidates"] == expected_json
