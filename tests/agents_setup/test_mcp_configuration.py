from __future__ import annotations

import json
from pathlib import Path

import pytest

from workctx.adapters.agents._safe_fs import FileSnapshot
from workctx.adapters.agents._transaction import FileMutation
from workctx.adapters.agents.manifest import load_manifest
from workctx.adapters.agents.models import (
    AdapterState,
    AgentClient,
    DriftReason,
    FeatureState,
    FileOperation,
)
from workctx.adapters.agents.renderers import content_hash, render_mcp_configuration
from workctx.adapters.agents.service import AgentAdapterService

_SKILL_NAME = "fixture-skill"
_JSON_MCP = (
    json.dumps(
        {
            "mcpServers": {
                "workctx": {
                    "command": "workctx",
                    "args": ["mcp", "serve", "--context", "."],
                }
            }
        },
        indent=2,
    )
    + "\n"
).encode()
_CODEX_MCP = (
    b'[mcp_servers.workctx]\ncommand = "workctx"\nargs = ["mcp", "serve", "--context", "."]\n'
)


def _service() -> AgentAdapterService:
    return AgentAdapterService(
        executable_finder=lambda name: f"fake-{name}",
        version_probe=lambda executable, _root: {
            "fake-codex": "codex 0.5.0",
            "fake-claude": "Claude Code 2.0.0",
            "fake-gemini": "Gemini CLI 0.5.0",
        }[executable],
        session_id_factory=lambda: "mcp-configuration-test",
    )


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
        "description: Use when testing project-scoped MCP configuration ownership.\n"
        "---\n\n"
        "# Fixture skill\n",
        encoding="utf-8",
    )
    return project


def _manifest_path(project: Path, client: AgentClient) -> Path:
    return project / ".workctx" / "agent-adapters" / client.value / "skill-manifest.json"


def _snapshot(content: bytes | None) -> FileSnapshot:
    return FileSnapshot(
        exists=content is not None,
        identity=None,
        size=None if content is None else len(content),
        content_hash=None if content is None else content_hash(content),
        content=content,
    )


@pytest.mark.parametrize("client", tuple(AgentClient))
def test_mcp_install_planner_generates_only_an_absent_config(client: AgentClient) -> None:
    desired = render_mcp_configuration(client)
    mutations: list[FileMutation] = []
    changes = []

    component = AgentAdapterService._plan_mcp_configuration(
        None,
        client,
        desired,
        _snapshot(None),
        mutations,
        changes,
    )

    assert component.state == "generated"
    assert component.path == desired.path
    assert component.content_hash == desired.target_hash
    assert [(item.path, item.desired) for item in mutations] == [(desired.path, desired.content)]
    assert [(item.path, item.operation) for item in changes] == [
        (desired.path, FileOperation.CREATE)
    ]


@pytest.mark.parametrize("client", tuple(AgentClient))
def test_mcp_install_planner_classifies_existing_config_without_mutation(
    client: AgentClient,
) -> None:
    desired = render_mcp_configuration(client)
    for content, expected_state in ((desired.content, "native"), (b"{}\n", "divergent")):
        mutations: list[FileMutation] = []
        changes = []

        component = AgentAdapterService._plan_mcp_configuration(
            None,
            client,
            desired,
            _snapshot(content),
            mutations,
            changes,
        )

        assert component.state == expected_state
        assert component.path == desired.path
        assert component.content_hash == content_hash(content)
        assert mutations == []
        assert changes == []


@pytest.mark.parametrize(
    ("client", "relative_path", "expected"),
    [
        (AgentClient.CODEX, ".codex/config.toml", _CODEX_MCP),
        (AgentClient.CLAUDE, ".mcp.json", _JSON_MCP),
        (AgentClient.GEMINI, ".gemini/settings.json", _JSON_MCP),
    ],
)
def test_install_generates_each_client_mcp_config_and_records_manifest_ownership(
    tmp_path: Path,
    client: AgentClient,
    relative_path: str,
    expected: bytes,
) -> None:
    project = _project(tmp_path)
    config = project / Path(relative_path)
    service = _service()

    plan = service.plan_install(project, client)
    assert (relative_path, FileOperation.CREATE) in {
        (change.path, change.operation) for change in plan.changes
    }
    service.install(plan)

    assert config.read_bytes() == expected
    manifest = load_manifest(_manifest_path(project, client).read_bytes())
    assert manifest.components is not None
    component = manifest.components.mcp_configuration
    assert component.state == "generated"
    assert component.path == relative_path
    assert component.content_hash == content_hash(expected)
    status = service.status(project, client)
    assert status.state is AdapterState.CURRENT
    assert status.mcp_configuration.state is FeatureState.GENERATED
    assert status.mcp_configuration.path == relative_path

    service.uninstall(service.plan_uninstall(project, client))

    assert not config.exists()
    assert not _manifest_path(project, client).exists()


@pytest.mark.parametrize(
    ("client", "relative_path", "content"),
    [
        (
            AgentClient.CODEX,
            ".codex/config.toml",
            b'[mcp_servers.other]\ncommand = "other"\nargs = []\n',
        ),
        (
            AgentClient.CLAUDE,
            ".mcp.json",
            b'{"mcpServers":{"other":{"command":"other","args":[]}}}\n',
        ),
        (
            AgentClient.GEMINI,
            ".gemini/settings.json",
            b'{"mcpServers":{"other":{"command":"other","args":[]}}}\n',
        ),
    ],
)
def test_existing_user_mcp_config_is_preserved_and_reported_divergent(
    tmp_path: Path,
    client: AgentClient,
    relative_path: str,
    content: bytes,
) -> None:
    project = _project(tmp_path)
    config = project / Path(relative_path)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(content)
    before = (config.read_bytes(), config.stat().st_mtime_ns)
    service = _service()

    service.install(service.plan_install(project, client))

    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    manifest = load_manifest(_manifest_path(project, client).read_bytes())
    assert manifest.components is not None
    component = manifest.components.mcp_configuration
    assert component.state == "divergent"
    assert component.path == relative_path
    assert component.content_hash == content_hash(content)
    status = service.status(project, client)
    assert status.state is AdapterState.CURRENT
    assert status.mcp_configuration.state is FeatureState.DIVERGENT
    assert [item.reason for item in status.drift] == [DriftReason.MCP_DIVERGENT]

    service.uninstall(service.plan_uninstall(project, client))

    assert (config.read_bytes(), config.stat().st_mtime_ns) == before


@pytest.mark.parametrize(
    ("client", "relative_path", "content"),
    [
        (
            AgentClient.CODEX,
            ".codex/config.toml",
            b'[mcp_servers.other]\ncommand = "other"\nargs = []\n\n' + _CODEX_MCP,
        ),
        (
            AgentClient.CLAUDE,
            ".mcp.json",
            json.dumps(
                {
                    "mcpServers": {
                        "other": {"command": "other", "args": []},
                        "workctx": {
                            "command": "workctx",
                            "args": ["mcp", "serve", "--context", "."],
                        },
                    }
                }
            ).encode(),
        ),
        (
            AgentClient.GEMINI,
            ".gemini/settings.json",
            json.dumps(
                {
                    "mcpServers": {
                        "other": {"command": "other", "args": []},
                        "workctx": {
                            "command": "workctx",
                            "args": ["mcp", "serve", "--context", "."],
                        },
                    }
                }
            ).encode(),
        ),
    ],
)
def test_existing_equivalent_user_mcp_config_is_native_and_never_deleted(
    tmp_path: Path,
    client: AgentClient,
    relative_path: str,
    content: bytes,
) -> None:
    project = _project(tmp_path)
    config = project / Path(relative_path)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(content)
    before = (config.read_bytes(), config.stat().st_mtime_ns)
    service = _service()

    service.install(service.plan_install(project, client))

    manifest = load_manifest(_manifest_path(project, client).read_bytes())
    assert manifest.components is not None
    assert manifest.components.mcp_configuration.state == "native"
    status = service.status(project, client)
    assert status.state is AdapterState.CURRENT
    assert status.mcp_configuration.state is FeatureState.NATIVE
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before

    service.uninstall(service.plan_uninstall(project, client))

    assert (config.read_bytes(), config.stat().st_mtime_ns) == before


def test_modified_generated_mcp_config_makes_repair_and_uninstall_report_only(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    config = project / ".mcp.json"
    service = _service()
    service.install(service.plan_install(project, AgentClient.CLAUDE))
    config.write_bytes(b"operator-modified MCP config\n")

    repair = service.plan_repair(project, AgentClient.CLAUDE)
    uninstall = service.plan_uninstall(project, AgentClient.CLAUDE)

    for plan in (repair, uninstall):
        assert plan.blocked_reason is not None
        assert {change.operation for change in plan.changes} == {FileOperation.PRESERVE}
        assert ".mcp.json" in {change.path for change in plan.changes}
    assert config.read_bytes() == b"operator-modified MCP config\n"
