from __future__ import annotations

import json
from pathlib import Path

import pytest

from workctx.adapters.agents import (
    PERSONALIZATION_END_MARKER,
    PERSONALIZATION_LAYER_MAX_BYTES,
    PERSONALIZATION_START_MARKER,
    AdapterConflictError,
    AdapterState,
    AgentAdapterService,
    AgentClient,
    FileOperation,
    PersonalizationLayerName,
    PersonalizationLayerTooLargeError,
    PersonalizationSecretError,
    load_personalization_layers,
    render_personalized_bridge,
    user_personalization_path,
)
from workctx.adapters.filesystem import registry as registry_module
from workctx.services.contexts import initialize_context

_SKILL_NAME = "fictional-personalization"
_SKILL_DESCRIPTION = "Use when testing fictional personalization adapter behavior."


@pytest.fixture
def isolated_user_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    user_config = tmp_path / "user-config"
    monkeypatch.setattr(
        registry_module,
        "user_config_path",
        lambda *_args, **_kwargs: user_config,
    )
    return user_personalization_path()


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
        session_id_factory=lambda: "personalization-session",
    )


def _write_local_source(project: Path) -> None:
    skill = project / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    skill.parent.mkdir(parents=True)
    (project / ".agents" / "skills" / "registry.yaml").write_text(
        f"schema_version: 1\nskills:\n  - id: {_SKILL_NAME}\n    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    skill.write_text(
        f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n"
        "# Fictional personalization skill\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("user_content", "context_content", "expected_presence"),
    [
        (None, None, (False, False)),
        ("# User tone\n", None, (True, False)),
        (None, "# Context boundary\n", (False, True)),
        ("# User tone\n", "# Context boundary\n", (True, True)),
    ],
)
def test_layer_discovery_covers_none_one_and_both(
    tmp_path: Path,
    isolated_user_layer: Path,
    user_content: str | None,
    context_content: str | None,
    expected_presence: tuple[bool, bool],
) -> None:
    context = tmp_path / "context"
    context.mkdir()
    if user_content is not None:
        isolated_user_layer.parent.mkdir(parents=True)
        isolated_user_layer.write_text(user_content, encoding="utf-8")
    if context_content is not None:
        (context / "instructions.md").write_text(context_content, encoding="utf-8")

    layers = load_personalization_layers(context)

    assert tuple(layer.present for layer in layers.ordered) == expected_presence
    assert layers.user.path == isolated_user_layer
    assert layers.context.path == context.resolve() / "instructions.md"
    assert tuple(layer.layer for layer in layers.ordered) == (
        PersonalizationLayerName.USER,
        PersonalizationLayerName.CONTEXT,
    )


def test_merge_is_delimited_provenanced_and_context_after_user(
    tmp_path: Path,
    isolated_user_layer: Path,
) -> None:
    context = tmp_path / "fictional-context"
    context.mkdir()
    isolated_user_layer.parent.mkdir(parents=True)
    isolated_user_layer.write_text("Use a concise tone.\n", encoding="utf-8")
    context_layer = context / "instructions.md"
    context_layer.write_text("Do not modify the fictional portal.\n", encoding="utf-8")

    merged = render_personalized_bridge(
        b"# Fictional bridge\n",
        load_personalization_layers(context),
    ).decode("utf-8")

    assert PERSONALIZATION_START_MARKER in merged
    assert PERSONALIZATION_END_MARKER in merged
    assert "### User layer — instructions.md" in merged
    assert "### Context layer — instructions.md" in merged
    assert f"from <{isolated_user_layer}>" in merged
    assert f"from <{context_layer.resolve()}>" in merged
    assert merged.index("Use a concise tone.") < merged.index("Do not modify the fictional portal.")


def test_repository_only_install_scope_does_not_claim_a_context_layer(
    tmp_path: Path,
    isolated_user_layer: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "instructions.md").write_text(
        "This is not beside a context.yaml marker.\n",
        encoding="utf-8",
    )

    layers = load_personalization_layers(repository, include_context=False)

    assert not layers.context.present
    assert layers.context.path == repository.resolve() / "instructions.md"


def test_layer_size_cap_is_per_layer_and_typed(
    tmp_path: Path,
    isolated_user_layer: Path,
) -> None:
    context = tmp_path / "context"
    context.mkdir()
    isolated_user_layer.parent.mkdir(parents=True)
    isolated_user_layer.write_bytes(b"x" * (PERSONALIZATION_LAYER_MAX_BYTES + 1))

    with pytest.raises(PersonalizationLayerTooLargeError) as raised:
        load_personalization_layers(context)

    assert raised.value.layer is PersonalizationLayerName.USER
    assert raised.value.size_bytes == PERSONALIZATION_LAYER_MAX_BYTES + 1


def test_secret_refusal_names_only_layer_and_line(
    tmp_path: Path,
    isolated_user_layer: Path,
) -> None:
    context = tmp_path / "context"
    context.mkdir()
    secret_assignment = "api_" + 'key = "' + "sk-" + 'fictional-1234567890abcdef"'
    (context / "instructions.md").write_text(
        "# Fictional project\n" + secret_assignment + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PersonalizationSecretError) as raised:
        load_personalization_layers(context)

    assert raised.value.layer is PersonalizationLayerName.CONTEXT
    assert raised.value.line_number == 2
    assert str(raised.value) == "context layer, line 2"
    assert str(context) not in str(raised.value)
    assert secret_assignment not in str(raised.value)


def test_plan_revalidates_layer_bytes_before_any_install_write(
    tmp_path: Path,
    isolated_user_layer: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project)
    isolated_user_layer.parent.mkdir(parents=True)
    isolated_user_layer.write_text("Use the fictional reviewer role.\n", encoding="utf-8")
    service = _service()
    plan = service.plan_install(project, AgentClient.CLAUDE)

    isolated_user_layer.write_text("Use the fictional operator role.\n", encoding="utf-8")

    with pytest.raises(AdapterConflictError, match="sources changed"):
        service.install(plan)
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".workctx" / "agent-adapters" / "claude").exists()


def test_user_owned_bridge_is_preserved_and_layers_report_not_merged(
    tmp_path: Path,
    isolated_user_layer: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project)
    isolated_user_layer.parent.mkdir(parents=True)
    isolated_user_layer.write_text("Use a calm fictional tone.\n", encoding="utf-8")
    bridge = project / "CLAUDE.md"
    original = b"# Operator-owned Claude bridge\n"
    bridge.write_bytes(original)
    service = _service()

    plan = service.plan_install(project, AgentClient.CLAUDE)
    user_plan_status = plan.personalization_layers[0]
    service.install(plan)
    status = service.status(project, AgentClient.CLAUDE)

    assert user_plan_status.present
    assert not user_plan_status.merged
    assert bridge.read_bytes() == original
    assert status.personalization_layers[0].present
    assert not status.personalization_layers[0].merged


def test_install_and_upgrade_all_bridges_refresh_without_tracking_layers(
    tmp_path: Path,
    isolated_user_layer: Path,
) -> None:
    context = tmp_path / "jala-fictional-context"
    initialize_context(
        context,
        name="Jala Fictional",
        context_id="jala-fictional",
    )
    # The context template owns AGENTS.md. Removing it makes this test exercise a
    # generated Codex bridge under the same three-factor ownership rules as the others.
    (context / "AGENTS.md").unlink()
    isolated_user_layer.parent.mkdir(parents=True)
    isolated_user_layer.write_text(
        "Speak as a concise engineering collaborator.\n",
        encoding="utf-8",
    )
    context_layer = context / "instructions.md"
    context_layer.write_text(
        "For Project Aurora, propose changes before applying them.\n",
        encoding="utf-8",
    )
    initial_layer_snapshot = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (isolated_user_layer, context_layer)
    }
    service = _service()
    bridge_names = {
        AgentClient.CODEX: "AGENTS.md",
        AgentClient.CLAUDE: "CLAUDE.md",
        AgentClient.GEMINI: "GEMINI.md",
    }

    for client in AgentClient:
        plan = service.plan_install(context, client)
        assert [status.present for status in plan.personalization_layers] == [True, True]
        assert [status.merged for status in plan.personalization_layers] == [True, True]
        verification_paths = {
            change.path for change in plan.changes if change.operation is FileOperation.VERIFY
        }
        assert verification_paths == {
            str(isolated_user_layer),
            str(context_layer.resolve()),
        }
        assert all(
            change.reason is not None and "size=" in change.reason and "merged=yes" in change.reason
            for change in plan.changes
            if change.operation is FileOperation.VERIFY
        )
        service.install(plan)

    for client, bridge_name in bridge_names.items():
        bridge_text = (context / bridge_name).read_text(encoding="utf-8")
        assert bridge_text.index("Speak as a concise engineering collaborator.") < (
            bridge_text.index("For Project Aurora, propose changes before applying them.")
        )
        status = service.status(context, client)
        assert status.state is AdapterState.CURRENT
        assert [layer.merged for layer in status.personalization_layers] == [True, True]
        assert [layer.path for layer in status.personalization_layers] == [
            str(isolated_user_layer),
            str(context_layer.resolve()),
        ]
        assert [layer.size_bytes for layer in status.personalization_layers] == [
            len(isolated_user_layer.read_bytes()),
            len(context_layer.read_bytes()),
        ]
        assert any("size=" in warning and "merged=yes" in warning for warning in status.warnings)
        manifest = context / "98_state" / "agent-adapters" / client.value / "skill-manifest.json"
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert "instructions.md" not in json.dumps(manifest_payload, sort_keys=True)
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in initial_layer_snapshot
    } == initial_layer_snapshot

    isolated_user_layer.write_text(
        "Speak as a direct engineering reviewer.\n",
        encoding="utf-8",
    )
    context_layer.write_text(
        "For Project Aurora, never apply a change without approval.\n",
        encoding="utf-8",
    )
    upgraded_layer_snapshot = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (isolated_user_layer, context_layer)
    }

    for client, bridge_name in bridge_names.items():
        stale = service.status(context, client)
        assert stale.state is AdapterState.STALE
        assert [layer.merged for layer in stale.personalization_layers] == [False, False]
        plan = service.plan_install(context, client)
        mutation_paths = {
            change.path
            for change in plan.changes
            if change.operation
            in {FileOperation.CREATE, FileOperation.REPLACE, FileOperation.DELETE}
        }
        assert bridge_name in mutation_paths
        service.install(plan)
        bridge_text = (context / bridge_name).read_text(encoding="utf-8")
        assert "Speak as a direct engineering reviewer." in bridge_text
        assert "never apply a change without approval" in bridge_text
        assert "Speak as a concise engineering collaborator." not in bridge_text
        assert service.status(context, client).state is AdapterState.CURRENT

    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in upgraded_layer_snapshot
    } == upgraded_layer_snapshot

    for client in AgentClient:
        service.uninstall(service.plan_uninstall(context, client))
    assert isolated_user_layer.read_text(encoding="utf-8") == (
        "Speak as a direct engineering reviewer.\n"
    )
    assert context_layer.read_text(encoding="utf-8") == (
        "For Project Aurora, never apply a change without approval.\n"
    )
