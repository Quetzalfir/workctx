from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from workctx.adapters.agents.manifest import (
    load_manifest,
    source_set_aggregate_hash,
)
from workctx.adapters.agents.models import (
    AdapterState,
    AgentClient,
    DriftReason,
    FileOperation,
)
from workctx.adapters.agents.renderers import content_hash
from workctx.adapters.agents.service import AgentAdapterService
from workctx.services.contexts import initialize_context

_SKILL_NAME = "fixture-skill"
_SKILL_DESCRIPTION = "Use when exercising native source-set service behavior."


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
        session_id_factory=lambda: "service-lifecycle-session",
    )


def _install(service: AgentAdapterService, root: Path, client: AgentClient) -> None:
    service.install(service.plan_install(root, client))


def _write_native_skill(root: Path) -> tuple[Path, Path]:
    skill_root = root / ".agents" / "skills" / _SKILL_NAME
    skill_root.mkdir(parents=True)
    (root / ".agents" / "skills" / "registry.yaml").write_text(
        f"schema_version: 1\nskills:\n  - id: {_SKILL_NAME}\n    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    primary = skill_root / "SKILL.md"
    primary.write_text(
        f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n"
        "See [guide](references/guide.md).\n",
        encoding="utf-8",
    )
    guide = skill_root / "references" / "guide.md"
    guide.parent.mkdir()
    guide.write_bytes(b"# Native guide\n")
    return primary, guide


def _repository_manifest(root: Path, client: AgentClient) -> Path:
    return root / ".workctx" / "agent-adapters" / client.value / "skill-manifest.json"


def test_codex_native_source_set_resource_drift_repairs_only_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    primary, guide = _write_native_skill(project)
    service = _service()

    _install(service, project, AgentClient.CODEX)

    manifest_path = _repository_manifest(project, AgentClient.CODEX)
    manifest = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    entry = manifest.skills[0]
    assert entry.mode == "native-verified"
    assert entry.generated is None
    assert entry.source_set is not None
    recorded_files = {source.path: source.content_hash for source in entry.source_set.files}
    expected_files = {
        f".agents/skills/{_SKILL_NAME}/SKILL.md": content_hash(primary.read_bytes()),
        f".agents/skills/{_SKILL_NAME}/references/guide.md": content_hash(guide.read_bytes()),
    }
    assert recorded_files == expected_files
    assert entry.source_set.aggregate_hash == source_set_aggregate_hash(expected_files.items())
    assert not (project / ".codex").exists()

    guide.write_bytes(b"# Operator-revised native guide\n")
    native_files = (primary, guide)
    native_after_edit = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in native_files
    }

    status = service.status(project, AgentClient.CODEX)
    plan = service.plan_repair(project, AgentClient.CODEX)

    assert status.state is AdapterState.STALE
    assert [
        (detail.reason, detail.path, detail.skill)
        for detail in status.drift
        if detail.reason in {DriftReason.SOURCE_CHANGED, DriftReason.SOURCE_MISSING}
    ] == [
        (
            DriftReason.SOURCE_CHANGED,
            f".agents/skills/{_SKILL_NAME}/references/guide.md",
            _SKILL_NAME,
        )
    ]
    assert [(change.path, change.operation) for change in plan.changes] == [
        (
            ".workctx/agent-adapters/codex/skill-manifest.json",
            FileOperation.REPLACE,
        )
    ]

    result = service.repair(plan)

    assert result.changed_paths == (".workctx/agent-adapters/codex/skill-manifest.json",)
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in native_files
    } == native_after_edit
    assert not (project / ".codex").exists()
    repaired = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    assert repaired.skills[0].source_set is not None
    assert {source.path: source.content_hash for source in repaired.skills[0].source_set.files}[
        f".agents/skills/{_SKILL_NAME}/references/guide.md"
    ] == content_hash(guide.read_bytes())
    assert service.status(project, AgentClient.CODEX).state is AdapterState.CURRENT


@pytest.mark.parametrize("change", ("add", "remove", "rename"))
def test_codex_native_source_set_path_changes_are_stale_not_missing(
    tmp_path: Path,
    change: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    primary, guide = _write_native_skill(project)
    service = _service()
    _install(service, project, AgentClient.CODEX)

    if change == "add":
        added = guide.parent / "notes.md"
        added.write_bytes(b"# Native notes\n")
        expected_paths = {f".agents/skills/{_SKILL_NAME}/references/notes.md"}
    elif change == "remove":
        guide.unlink()
        primary.write_text(
            f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n"
            "No auxiliary guide is required.\n",
            encoding="utf-8",
        )
        expected_paths = {
            f".agents/skills/{_SKILL_NAME}/SKILL.md",
            f".agents/skills/{_SKILL_NAME}/references/guide.md",
        }
    else:
        renamed = guide.with_name("runbook.md")
        guide.rename(renamed)
        primary.write_text(
            f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n"
            "See [runbook](references/runbook.md).\n",
            encoding="utf-8",
        )
        expected_paths = {
            f".agents/skills/{_SKILL_NAME}/SKILL.md",
            f".agents/skills/{_SKILL_NAME}/references/guide.md",
            f".agents/skills/{_SKILL_NAME}/references/runbook.md",
        }

    status = service.status(project, AgentClient.CODEX)
    relevant = {
        detail.path: detail.reason for detail in status.drift if detail.path in expected_paths
    }
    plan = service.plan_repair(project, AgentClient.CODEX)

    assert status.state is AdapterState.STALE
    assert not status.repair_blocked
    assert relevant == {path: DriftReason.SOURCE_CHANGED for path in expected_paths}
    assert [(item.path, item.operation) for item in plan.changes] == [
        (
            ".workctx/agent-adapters/codex/skill-manifest.json",
            FileOperation.REPLACE,
        )
    ]

    service.repair(plan)

    assert service.status(project, AgentClient.CODEX).state is AdapterState.CURRENT
    assert not (project / ".codex").exists()


@pytest.mark.parametrize(
    ("client", "bridge_name", "skill_root", "other_skill_root"),
    [
        (AgentClient.CLAUDE, "CLAUDE.md", ".claude/skills/", ".gemini/skills/"),
        (AgentClient.GEMINI, "GEMINI.md", ".gemini/skills/", ".claude/skills/"),
    ],
)
def test_context_install_uses_target_flavored_self_contained_bridge_and_preserves_agents(
    tmp_path: Path,
    client: AgentClient,
    bridge_name: str,
    skill_root: str,
    other_skill_root: str,
) -> None:
    context = tmp_path / f"{client.value}-context"
    initialize_context(
        context,
        name=f"Fictional {client.value.title()} context",
        context_id=f"fictional-{client.value}-context",
    )
    agents = context / "AGENTS.md"
    agents_before = (agents.read_bytes(), agents.stat().st_mtime_ns)
    service = _service()

    _install(service, context, client)

    bridge = context / bridge_name
    packaged_bridge = (
        resources.files("workctx.resources.agent_kit").joinpath("bridges", bridge_name).read_bytes()
    )
    bridge_text = bridge.read_text(encoding="utf-8")
    assert bridge.read_bytes() == packaged_bridge
    assert skill_root in bridge_text
    assert other_skill_root not in bridge_text
    assert "When `AGENTS.md` exists" in bridge_text
    assert "START-HERE.md" not in bridge_text
    assert ".agents/plan/" not in bridge_text
    assert not bridge_text.startswith("@AGENTS.md")
    assert (agents.read_bytes(), agents.stat().st_mtime_ns) == agents_before

    manifest_path = context / "98_state" / "agent-adapters" / client.value / "skill-manifest.json"
    manifest = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    assert manifest.components is not None
    bridge_record = manifest.components.instruction_bridge
    assert bridge_record.ownership == "generated"
    assert bridge_record.source.path == bridge_name
    assert bridge_record.target.path == bridge_name
    assert bridge_record.source.content_hash == content_hash(packaged_bridge)
    assert bridge_record.target.content_hash == content_hash(packaged_bridge)
    assert service.status(context, client).state is AdapterState.CURRENT
