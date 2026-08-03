from __future__ import annotations

from pathlib import Path

import pytest

from workctx.adapters.agents._safe_fs import SafeRoot
from workctx.adapters.agents._transaction import inspect_transactions
from workctx.adapters.agents.detection import VersionProbe
from workctx.adapters.agents.errors import (
    AdapterConflictError,
    RecoveryConflictError,
    UnsupportedClientVersionError,
)
from workctx.adapters.agents.layout import derive_layout
from workctx.adapters.agents.models import AgentClient
from workctx.adapters.agents.service import AgentAdapterService

_SKILL_NAME = "fixture-skill"
_LOCK_PATH = Path(".workctx/agent-adapters/lock.json")
_MANIFEST_PATH = Path(".workctx/agent-adapters/claude/skill-manifest.json")
_TARGET_PATH = Path(".claude/skills/fixture-skill/SKILL.md")


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    skill = project / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    skill.parent.mkdir(parents=True)
    (project / ".agents" / "skills" / "registry.yaml").write_text(
        f"schema_version: 1\nskills:\n  - id: {_SKILL_NAME}\n    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    skill.write_text(
        "---\n"
        f"name: {_SKILL_NAME}\n"
        "description: Use when testing exact dry-run target revalidation.\n"
        "---\n\n"
        "# Fixture skill\n",
        encoding="utf-8",
    )
    return project


def _add_second_skill(project: Path) -> None:
    name = "second-skill"
    (project / ".agents" / "skills" / "registry.yaml").write_text(
        "schema_version: 1\n"
        "skills:\n"
        f"  - id: {_SKILL_NAME}\n"
        "    side_effect_class: read_only\n"
        f"  - id: {name}\n"
        "    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    skill = project / ".agents" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Use when testing a skipped managed adapter target.\n"
        "---\n\n"
        "# Second fixture skill\n",
        encoding="utf-8",
    )


def _service(*, version_probe: VersionProbe | None = None) -> AgentAdapterService:
    probe = version_probe or (lambda _executable, _root: "Claude Code 2.0.0")
    return AgentAdapterService(
        executable_finder=lambda name: f"fake-{name}",
        version_probe=probe,
        session_id_factory=lambda: "plan-revalidation",
    )


def test_changed_user_owned_bridge_skipped_by_plan_aborts_apply(tmp_path: Path) -> None:
    project = _project(tmp_path)
    bridge = project / "CLAUDE.md"
    bridge.write_text("# User instructions\n", encoding="utf-8")
    service = _service()

    plan = service.plan_install(project, AgentClient.CLAUDE)
    assert "CLAUDE.md" not in {change.path for change in plan.changes}
    bridge.write_text("# Revised user instructions\n", encoding="utf-8")

    with pytest.raises(AdapterConflictError, match=r"CLAUDE\.md"):
        service.install(plan)

    assert bridge.read_text(encoding="utf-8") == "# Revised user instructions\n"
    assert not (project / _TARGET_PATH).exists()
    assert not (project / _MANIFEST_PATH).exists()
    assert not (project / _LOCK_PATH).exists()


def test_changed_user_owned_mcp_config_skipped_by_plan_aborts_apply(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / ".mcp.json"
    config.write_text('{"mcpServers": {"operator": {"command": "other"}}}\n', encoding="utf-8")
    service = _service()

    plan = service.plan_install(project, AgentClient.CLAUDE)
    assert ".mcp.json" not in {change.path for change in plan.changes}
    config.write_text('{"mcpServers": {"operator": {"command": "revised"}}}\n', encoding="utf-8")

    with pytest.raises(AdapterConflictError, match=r"\.mcp\.json"):
        service.install(plan)

    assert "revised" in config.read_text(encoding="utf-8")
    assert not (project / _TARGET_PATH).exists()
    assert not (project / _MANIFEST_PATH).exists()
    assert not (project / _LOCK_PATH).exists()


def test_changed_managed_target_aborts_noop_apply_under_lock(tmp_path: Path) -> None:
    project = _project(tmp_path)
    require_lock = False

    def probe(_executable: str, root: Path) -> str:
        if require_lock:
            assert (root / _LOCK_PATH).is_file()
        return "Claude Code 2.0.0"

    service = _service(version_probe=probe)
    service.install(service.plan_install(project, AgentClient.CLAUDE))
    plan = service.plan_install(project, AgentClient.CLAUDE)
    assert plan.is_noop
    target = project / _TARGET_PATH
    target.write_bytes(target.read_bytes() + b"\nuser edit\n")
    manifest_before = (project / _MANIFEST_PATH).read_bytes()
    require_lock = True

    with pytest.raises(AdapterConflictError, match="fixture-skill"):
        service.install(plan)

    assert target.read_bytes().endswith(b"\nuser edit\n")
    assert (project / _MANIFEST_PATH).read_bytes() == manifest_before
    assert not (project / _LOCK_PATH).exists()


def test_capability_change_after_plan_aborts_before_install_mutation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    supported = True

    def probe(_executable: str, root: Path) -> str:
        if not supported:
            assert (root / _LOCK_PATH).is_file()
            return "Claude Code 3.0.0"
        return "Claude Code 2.0.0"

    service = _service(version_probe=probe)
    plan = service.plan_install(project, AgentClient.CLAUDE)
    supported = False

    with pytest.raises(UnsupportedClientVersionError):
        service.install(plan)

    assert not (project / _TARGET_PATH).exists()
    assert not (project / "CLAUDE.md").exists()
    assert not (project / _MANIFEST_PATH).exists()
    assert not (project / _LOCK_PATH).exists()


def test_skipped_managed_target_changed_after_intent_fsync_aborts_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    _add_second_skill(project)
    service = _service()
    service.install(service.plan_install(project, AgentClient.CLAUDE))

    canonical = project / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    canonical.write_bytes(canonical.read_bytes() + b"\nRevised canonical source.\n")
    changed_target = project / _TARGET_PATH
    skipped_target = project / ".claude" / "skills" / "second-skill" / "SKILL.md"
    changed_before = changed_target.read_bytes()
    manifest_before = (project / _MANIFEST_PATH).read_bytes()
    plan = service.plan_repair(project, AgentClient.CLAUDE)
    assert str(skipped_target.relative_to(project)).replace("\\", "/") not in {
        change.path for change in plan.changes
    }

    original_fsync = SafeRoot.fsync_directory
    intent_flushes = 0

    def alter_skipped_target_after_intent_fsync(self: SafeRoot, path: str = ".") -> None:
        nonlocal intent_flushes
        original_fsync(self, path)
        parts = path.split("/")
        if parts[:3] != [".workctx", "agent-adapters", "staging"] or len(parts) != 4:
            return
        if not self.inspect_file(f"{path}/intent.json").exists:
            return
        intent_flushes += 1
        if intent_flushes == 2:
            skipped_target.write_bytes(b"concurrent skipped-target edit\n")

    monkeypatch.setattr(SafeRoot, "fsync_directory", alter_skipped_target_after_intent_fsync)

    with pytest.raises(RecoveryConflictError, match="second-skill"):
        service.repair(plan)

    assert intent_flushes >= 2
    assert changed_target.read_bytes() == changed_before
    assert skipped_target.read_bytes() == b"concurrent skipped-target edit\n"
    assert (project / _MANIFEST_PATH).read_bytes() == manifest_before
    assert inspect_transactions(derive_layout(project, AgentClient.CLAUDE)).intents == ()
