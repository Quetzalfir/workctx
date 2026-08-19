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
        patcher.setattr(
            service_module, "_pristine_template_bridge_hashes", lambda _path: frozenset()
        )
        stale_service = _service()
        stale_service.install(stale_service.plan_install(root, AgentClient.CODEX))
    assert (root / "AGENTS.md").read_bytes() == _template_agents_bytes()

    heal_plan = _service().plan_install(root, AgentClient.CODEX)
    bridge_changes = [change for change in heal_plan.changes if change.path == "AGENTS.md"]
    assert bridge_changes, [change.path for change in heal_plan.changes]
    assert bridge_changes[0].operation.value == "replace"
    assert "previously recorded as user-owned" in bridge_changes[0].reason


def test_historical_template_generation_heals_like_the_current_one(tmp_path: Path) -> None:
    import hashlib

    import workctx.adapters.agents.service as service_module

    root = tmp_path / "context"
    initialize_context(root, name="Fictional Old Template", context_id="old-template")
    old_generation = b"# Context agent contract\n\nFictional earlier template generation.\n"
    (root / "AGENTS.md").write_bytes(old_generation)
    old_hash = "sha256:" + hashlib.sha256(old_generation).hexdigest()

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setitem(
            service_module._HISTORICAL_TEMPLATE_BRIDGE_HASHES,
            "AGENTS.md",
            frozenset({old_hash}),
        )
        plan = _service().plan_install(root, AgentClient.CODEX)

    bridge_changes = [change for change in plan.changes if change.path == "AGENTS.md"]
    assert bridge_changes, [change.path for change in plan.changes]
    assert bridge_changes[0].operation.value == "replace"
    assert "pristine context-template" in bridge_changes[0].reason


def test_template_hash_history_is_well_formed_and_contains_the_current_template() -> None:
    import hashlib
    import re

    from workctx.adapters.agents.service import _HISTORICAL_TEMPLATE_BRIDGE_HASHES

    pattern = re.compile(r"^sha256:[0-9a-f]{64}$")
    for name, hashes in _HISTORICAL_TEMPLATE_BRIDGE_HASHES.items():
        assert hashes, name
        assert all(pattern.fullmatch(item) for item in hashes), name

    current = "sha256:" + hashlib.sha256(_template_agents_bytes()).hexdigest()
    assert current in _HISTORICAL_TEMPLATE_BRIDGE_HASHES["AGENTS.md"], (
        "The context template AGENTS.md changed; append its new hash to "
        "_HISTORICAL_TEMPLATE_BRIDGE_HASHES so every shipped generation keeps healing."
    )


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
