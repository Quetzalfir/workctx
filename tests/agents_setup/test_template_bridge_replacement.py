"""A pristine context-template AGENTS.md must not block the codex bridge."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from workctx.adapters.agents import AgentAdapterService, AgentClient
from workctx.services.contexts import initialize_context


def _template_agents_bytes() -> bytes:
    return resources.files("workctx.resources.context_template").joinpath("AGENTS.md").read_bytes()


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    return {
        "fake-codex": "codex 0.5.0",
        "fake-claude": "Claude Code 2.0.0",
        "fake-gemini": "Gemini CLI 0.5.0",
    }[executable]


def _service() -> AgentAdapterService:
    return AgentAdapterService(executable_finder=_finder, version_probe=_probe)


def test_pristine_template_agents_md_is_replaced_by_the_codex_bridge(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Template Bridge", context_id="template-bridge")
    assert (root / "AGENTS.md").read_bytes() == _template_agents_bytes()

    plan = _service().plan_install(root, AgentClient.CODEX)

    bridge_changes = [change for change in plan.changes if change.path == "AGENTS.md"]
    assert bridge_changes, [change.path for change in plan.changes]
    assert bridge_changes[0].operation.value == "replace"
    assert "pristine context-template" in bridge_changes[0].reason


def test_previously_misrecorded_template_bridge_heals_on_reinstall(tmp_path: Path) -> None:
    import workctx.adapters.agents.service as service_module

    root = tmp_path / "context"
    initialize_context(root, name="Fictional Healing Bridge", context_id="healing-bridge")

    # Reproduce a pre-fix install, which recorded the template bridge as
    # user-owned. The patch lives in its own context so it never unwinds the
    # package's autouse isolation fixtures.
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(service_module, "_pristine_template_bridge_hash", lambda _path: None)
        stale_service = _service()
        stale_service.install(stale_service.plan_install(root, AgentClient.CODEX))
    assert (root / "AGENTS.md").read_bytes() == _template_agents_bytes()

    heal_plan = _service().plan_install(root, AgentClient.CODEX)
    bridge_changes = [change for change in heal_plan.changes if change.path == "AGENTS.md"]
    assert bridge_changes, [change.path for change in heal_plan.changes]
    assert bridge_changes[0].operation.value == "replace"
    assert "previously recorded as user-owned" in bridge_changes[0].reason


def test_operator_edited_agents_md_stays_user_owned(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Edited Bridge", context_id="edited-bridge")
    agents = root / "AGENTS.md"
    agents.write_bytes(_template_agents_bytes() + b"\n## Operator note\n\nFictional local rule.\n")

    plan = _service().plan_install(root, AgentClient.CODEX)

    assert not any(
        change.path == "AGENTS.md" and change.operation.value in {"replace", "create"}
        for change in plan.changes
    )
