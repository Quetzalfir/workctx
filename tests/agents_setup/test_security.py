from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workctx.adapters.agents.detection import detect_client, detect_clients
from workctx.adapters.agents.models import AgentClient
from workctx.adapters.agents.renderers import render_skill
from workctx.adapters.agents.service import AgentAdapterService
from workctx.adapters.agents.session import open_context

ROOT = Path(__file__).parents[2]
ADAPTER_SOURCE = ROOT / "src" / "workctx" / "adapters" / "agents"
_FORBIDDEN_API_PARTS = ("auth", "credential", "token", "home", "environment", "env")
_FORBIDDEN_GLOBAL_FILES = {
    "auth.json",
    "credentials.json",
    ".credentials.json",
    "oauth_creds.json",
}


@dataclass(frozen=True)
class _FakeProcess:
    pid: int = 7


def test_public_runtime_api_has_no_credential_or_home_parameters() -> None:
    public_functions = (
        detect_client,
        detect_clients,
        render_skill,
        open_context,
        AgentAdapterService,
        AgentAdapterService.detect,
        AgentAdapterService.open_context,
        AgentAdapterService.status,
        AgentAdapterService.plan_install,
        AgentAdapterService.plan_repair,
        AgentAdapterService.plan_uninstall,
        AgentAdapterService.apply_plan,
        AgentAdapterService.install,
        AgentAdapterService.repair,
        AgentAdapterService.uninstall,
        AgentAdapterService.recover,
    )

    for function in public_functions:
        parameter_names = inspect.signature(function).parameters
        assert not {
            name
            for name in parameter_names
            if any(part in name.casefold() for part in _FORBIDDEN_API_PARTS)
        }, function.__qualname__


def test_adapter_source_has_no_user_global_auth_file_or_home_lookup() -> None:
    findings: list[str] = []
    for source_path in sorted(ADAPTER_SOURCE.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.casefold() in _FORBIDDEN_GLOBAL_FILES
            ):
                findings.append(f"{source_path.name}: forbidden global auth filename")
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr in {"home", "expanduser"}:
                findings.append(f"{source_path.name}: {function.attr}()")
            elif isinstance(function, ast.Name) and function.id in {
                "user_config_dir",
                "user_data_dir",
                "user_state_dir",
            }:
                findings.append(f"{source_path.name}: {function.id}()")

    assert not findings


def test_detection_rendering_and_open_never_consult_fake_global_auth(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = tmp_path / "project"
    fake_home = tmp_path / "home"
    project.mkdir()
    for relative in (
        ".codex/auth.json",
        ".claude/.credentials.json",
        ".gemini/oauth_creds.json",
    ):
        auth_file = fake_home / relative
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.write_text("GLOBAL-AUTH-CANARY", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    def reject_home(_cls: type[Path]) -> Path:
        raise AssertionError("agent adapter attempted to resolve the user home")

    monkeypatch.setattr(Path, "home", classmethod(reject_home))

    capabilities = detect_clients(
        project,
        executable_finder=lambda name: f"fake-{name}",
        version_probe=lambda executable, _root: {
            "fake-codex": "0.5.0",
            "fake-claude": "2.0.0",
            "fake-gemini": "0.5.0",
        }[executable],
    )
    rendered = render_skill(
        AgentClient.CLAUDE,
        name="fixture-skill",
        canonical_content=(
            b"---\n"
            b"name: fixture-skill\n"
            b"description: Use when exercising isolated adapter tests.\n"
            b"---\n"
        ),
        side_effect_class="read_only",
    )
    spawned: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(arguments: list[str], **kwargs: Any) -> _FakeProcess:
        spawned.append((arguments, kwargs))
        return _FakeProcess()

    opened = open_context(
        project,
        AgentClient.CODEX,
        executable_finder=lambda _name: "fake-codex",
        version_probe=lambda _executable, _root: "0.5.0",
        spawner=spawn,
    )

    assert all(capability.can_open for capability in capabilities)
    assert rendered.content is not None
    assert b"GLOBAL-AUTH-CANARY" not in rendered.content
    assert opened.pid == 7
    assert spawned == [(["fake-codex"], {"cwd": project.resolve(), "shell": False})]
    assert "env" not in spawned[0][1]
