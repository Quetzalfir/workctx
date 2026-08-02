from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from workctx.adapters.agents.manifest import load_manifest
from workctx.adapters.agents.models import (
    AdapterState,
    AgentClient,
    FeatureState,
)
from workctx.adapters.agents.service import AgentAdapterService

ROOT = Path(__file__).parents[2]
SCHEMA = json.loads(
    (ROOT / "schemas" / "skill-adapter-manifest.schema.json").read_text(encoding="utf-8")
)

_SKILL_NAME = "fixture-skill"
_SKILL_DESCRIPTION = "Use when exercising isolated agent adapter lifecycle behavior."


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    return {
        "fake-codex": "codex 0.5.0",
        "fake-claude": "Claude Code 2.0.0",
        "fake-gemini": "Gemini CLI 0.5.0",
    }[executable]


def _service() -> AgentAdapterService:
    return AgentAdapterService(
        executable_finder=_finder,
        version_probe=_probe,
        session_id_factory=lambda: "test-session",
    )


def _install(
    service: AgentAdapterService,
    project: Path,
    client: AgentClient,
):
    return service.install(service.plan_install(project, client))


def _uninstall(
    service: AgentAdapterService,
    project: Path,
    client: AgentClient,
):
    return service.uninstall(service.plan_uninstall(project, client))


def _skill_bytes(body: str = "# Fixture skill\n") -> bytes:
    return (f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n{body}").encode()


def _write_local_source(project: Path, *, body: str = "# Fixture skill\n") -> Path:
    skill_path = project / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    (project / ".agents" / "skills" / "registry.yaml").write_text(
        f"schema_version: 1\nskills:\n  - id: {_SKILL_NAME}\n    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    skill_path.write_bytes(_skill_bytes(body))
    return skill_path


def _manifest_path(project: Path, client: AgentClient) -> Path:
    return project / ".workctx" / "agent-adapters" / client.value / "skill-manifest.json"


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    ("client", "generated_path", "bridge"),
    [
        (AgentClient.CLAUDE, ".claude/skills/fixture-skill/SKILL.md", "CLAUDE.md"),
        (AgentClient.GEMINI, ".gemini/skills/fixture-skill/SKILL.md", "GEMINI.md"),
    ],
)
def test_install_emits_schema_valid_client_manifest_and_deferred_mcp_seam(
    tmp_path: Path,
    client: AgentClient,
    generated_path: str,
    bridge: str,
) -> None:
    project = tmp_path / client.value
    project.mkdir()
    _write_local_source(project)

    service = _service()
    result = _install(service, project, client)

    manifest_path = _manifest_path(project, client)
    manifest_data = _load_json(manifest_path)
    jsonschema.Draft202012Validator(SCHEMA).validate(manifest_data)
    manifest = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    assert result.no_op is False
    assert manifest.adapter == client.value
    assert manifest.skills[0].mode == "generated"
    assert manifest.skills[0].generated is not None
    assert manifest.skills[0].generated[0].path == generated_path
    assert manifest.components is not None
    assert manifest.components.mcp_configuration.model_dump() == {"state": "not_implemented"}
    assert manifest.components.instruction_bridge.target.path == bridge
    assert (project / Path(generated_path)).is_file()
    assert (project / bridge).is_file()

    status = _service().status(project, client)
    assert status.state is AdapterState.CURRENT
    assert status.mcp_configuration.state is FeatureState.NOT_IMPLEMENTED
    assert status.mcp_configuration.path is None
    assert "WP-330" in (status.mcp_configuration.detail or "")


def test_clients_install_independently_without_cross_client_mutation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project)
    service = _service()
    _install(service, project, AgentClient.CLAUDE)
    claude_manifest = _manifest_path(project, AgentClient.CLAUDE)
    claude_target = project / ".claude" / "skills" / _SKILL_NAME / "SKILL.md"
    before = (claude_manifest.read_bytes(), claude_target.read_bytes())

    _install(service, project, AgentClient.GEMINI)

    assert (claude_manifest.read_bytes(), claude_target.read_bytes()) == before
    assert _manifest_path(project, AgentClient.GEMINI).is_file()
    assert (project / ".gemini" / "skills" / _SKILL_NAME / "SKILL.md").is_file()
    assert service.status(project, AgentClient.CLAUDE).state is AdapterState.CURRENT
    assert service.status(project, AgentClient.GEMINI).state is AdapterState.CURRENT


def test_reinstall_is_content_and_mtime_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project)
    service = _service()
    _install(service, project, AgentClient.CLAUDE)
    tracked = (
        _manifest_path(project, AgentClient.CLAUDE),
        project / ".claude" / "skills" / _SKILL_NAME / "SKILL.md",
        project / "CLAUDE.md",
    )
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tracked}

    result = _install(service, project, AgentClient.CLAUDE)

    assert result.no_op
    assert result.changed_paths == ()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tracked} == before


def test_canonical_change_is_stale_and_repair_is_targeted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    canonical = _write_local_source(project)
    service = _service()
    _install(service, project, AgentClient.CLAUDE)
    bridge = project / "CLAUDE.md"
    bridge_before = (bridge.read_bytes(), bridge.stat().st_mtime_ns)
    target = project / ".claude" / "skills" / _SKILL_NAME / "SKILL.md"
    old_target = target.read_bytes()
    canonical.write_bytes(_skill_bytes("# Revised fixture skill\n"))

    status = service.status(project, AgentClient.CLAUDE)
    plan = service.plan_repair(project, AgentClient.CLAUDE)

    assert status.state is AdapterState.STALE
    assert {item.reason.value for item in status.drift} == {"source_changed"}
    assert {change.path for change in plan.changes} == {
        ".claude/skills/fixture-skill/SKILL.md",
        ".workctx/agent-adapters/claude/skill-manifest.json",
    }
    result = service.repair(plan)
    assert set(result.changed_paths) == {change.path for change in plan.changes}
    assert target.read_bytes() != old_target
    assert b"# Revised fixture skill" in target.read_bytes()
    assert (bridge.read_bytes(), bridge.stat().st_mtime_ns) == bridge_before
    assert service.status(project, AgentClient.CLAUDE).state is AdapterState.CURRENT


def test_uninstall_removes_only_manifest_owned_files_and_preserves_siblings(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    canonical = _write_local_source(project)
    service = _service()
    _install(service, project, AgentClient.CLAUDE)
    sibling = project / ".claude" / "skills" / "operator-notes.md"
    sibling.write_text("keep me\n", encoding="utf-8")

    installed_status = service.status(project, AgentClient.CLAUDE)
    assert installed_status.state is AdapterState.CURRENT
    assert installed_status.warnings == (
        "Unmanaged adapter file: .claude/skills/operator-notes.md",
    )

    result = _uninstall(service, project, AgentClient.CLAUDE)

    assert not result.no_op
    assert not (project / ".claude" / "skills" / _SKILL_NAME / "SKILL.md").exists()
    assert sibling.read_text(encoding="utf-8") == "keep me\n"
    assert canonical.is_file()
    assert not (project / "CLAUDE.md").exists()
    assert not _manifest_path(project, AgentClient.CLAUDE).exists()
    uninstalled_status = service.status(project, AgentClient.CLAUDE)
    assert uninstalled_status.state is AdapterState.NOT_INSTALLED
    assert uninstalled_status.warnings == (
        "Unmanaged adapter file: .claude/skills/operator-notes.md",
    )


@pytest.mark.parametrize("client", (AgentClient.CLAUDE, AgentClient.GEMINI))
def test_linked_resource_inventory_parses_any_order_but_authority_binds_raw_bytes(
    tmp_path: Path,
    client: AgentClient,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    canonical = _write_local_source(project)
    canonical.write_bytes(_skill_bytes("See [guide](references/guide.md).\n"))
    guide = canonical.parent / "references" / "guide.md"
    guide.parent.mkdir()
    guide.write_bytes(b"# Guide\n")
    service = _service()
    _install(service, project, client)
    target_root = project / f".{client.value}" / "skills" / _SKILL_NAME
    manifest_path = _manifest_path(project, client)
    authenticated_manifest = manifest_path.read_bytes()
    manifest_value = json.loads(authenticated_manifest)
    generated = manifest_value["skills"][0]["generated"]

    assert {entry["path"] for entry in generated} == {
        f".{client.value}/skills/{_SKILL_NAME}/SKILL.md",
        f".{client.value}/skills/{_SKILL_NAME}/references/guide.md",
    }
    assert (target_root / "references" / "guide.md").read_bytes() == b"# Guide\n"
    assert b"workctx-resource-sha256" in (target_root / "SKILL.md").read_bytes()

    manifest_value["skills"][0]["generated"] = list(reversed(generated))
    manifest_path.write_text(json.dumps(manifest_value, indent=2) + "\n", encoding="utf-8")
    reordered_status = service.status(project, client)
    assert reordered_status.state is AdapterState.CONFLICT
    assert reordered_status.repair_blocked
    assert service.plan_uninstall(project, client).blocked_reason is not None
    manifest_path.write_bytes(authenticated_manifest)

    sibling = target_root / "references" / "operator-notes.md"
    sibling.write_bytes(b"keep\n")
    _uninstall(service, project, client)

    assert not (target_root / "SKILL.md").exists()
    assert not (target_root / "references" / "guide.md").exists()
    assert sibling.read_bytes() == b"keep\n"


def test_existing_user_bridge_is_manifest_recorded_preserved_and_never_deleted(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project)
    bridge = project / "CLAUDE.md"
    bridge.write_bytes(b"# Operator-owned Claude instructions\n")
    original = (bridge.read_bytes(), bridge.stat().st_mtime_ns)
    service = _service()

    _install(service, project, AgentClient.CLAUDE)

    manifest = load_manifest(_manifest_path(project, AgentClient.CLAUDE).read_bytes())
    assert manifest.components is not None
    assert manifest.components.instruction_bridge.ownership == "user-owned"
    assert (bridge.read_bytes(), bridge.stat().st_mtime_ns) == original
    status = service.status(project, AgentClient.CLAUDE)
    assert status.state is AdapterState.CURRENT
    assert status.instruction_bridge.state is FeatureState.DIVERGED
    assert [item.reason.value for item in status.drift] == ["bridge_diverged"]
    assert status.warnings == ("User-owned instruction bridge differs from its source: CLAUDE.md",)

    _uninstall(service, project, AgentClient.CLAUDE)
    assert (bridge.read_bytes(), bridge.stat().st_mtime_ns) == original


def test_missing_recorded_user_bridge_is_never_recreated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project)
    bridge = project / "CLAUDE.md"
    bridge.write_bytes(b"# Operator-owned instructions\n")
    service = _service()
    _install(service, project, AgentClient.CLAUDE)
    bridge.unlink()

    status = service.status(project, AgentClient.CLAUDE)
    plan = service.plan_repair(project, AgentClient.CLAUDE)
    result = service.repair(plan)

    assert status.state is AdapterState.CURRENT
    assert status.instruction_bridge.state is FeatureState.DIVERGED
    assert [item.reason.value for item in status.drift] == ["bridge_diverged"]
    assert all(change.path != "CLAUDE.md" for change in plan.changes)
    assert result.no_op
    assert not bridge.exists()


def test_codex_packaged_sources_are_seeded_native_verified_and_retained_on_uninstall(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    service = _service()

    _install(service, project, AgentClient.CODEX)

    manifest_path = _manifest_path(project, AgentClient.CODEX)
    manifest = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    assert manifest.skills
    assert {skill.mode for skill in manifest.skills} == {"native-verified"}
    assert all(skill.generated is None for skill in manifest.skills)
    registry = project / ".agents" / "skills" / "registry.yaml"
    seeded = tuple(project / Path(skill.canonical.path) for skill in manifest.skills)
    assert registry.is_file()
    assert all(path.is_file() for path in seeded)
    before = {path: path.read_bytes() for path in (registry, *seeded)}
    assert service.status(project, AgentClient.CODEX).state is AdapterState.CURRENT

    _uninstall(service, project, AgentClient.CODEX)

    assert {path: path.read_bytes() for path in (registry, *seeded)} == before
    assert not (project / "AGENTS.md").exists()
    assert not manifest_path.exists()
