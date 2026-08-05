"""`agent install --agent all` targets detected clients only (lead fix)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workctx.adapters.agents import (
    AgentAdapterService,
    AgentClient,
    ClientAvailability,
)
from workctx.cli import app
from workctx.services.contexts import initialize_context

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_user_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never read the operator's real global configuration from these tests."""

    fake_home = tmp_path_factory.mktemp("cli-agent-home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("APPDATA", str(fake_home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))


class _Capability:
    def __init__(self, client: AgentClient, availability: ClientAvailability) -> None:
        self.client = client
        self.availability = availability


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    return {
        "fake-codex": "codex 0.5.0",
        "fake-claude": "Claude Code 2.0.0",
        "fake-gemini": "Gemini CLI 0.5.0",
    }[executable]


def _fake_detect(availabilities: dict[AgentClient, ClientAvailability]):
    def detect(self: AgentAdapterService, project_root: Path) -> tuple[_Capability, ...]:
        return tuple(_Capability(client, state) for client, state in availabilities.items())

    return detect


def test_all_plans_only_available_clients_and_warns_about_the_rest(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Install All", context_id="cli-install-all")
    import workctx.adapters.agents as agents_module

    real_service = agents_module.AgentAdapterService

    def _stubbed_service(*args: object, **kwargs: object) -> AgentAdapterService:
        return real_service(executable_finder=_finder, version_probe=_probe)

    monkeypatch.setattr(agents_module, "AgentAdapterService", _stubbed_service)
    monkeypatch.setattr(
        AgentAdapterService,
        "detect",
        _fake_detect(
            {
                AgentClient.CLAUDE: ClientAvailability.AVAILABLE,
                AgentClient.CODEX: ClientAvailability.AVAILABLE,
                AgentClient.GEMINI: ClientAvailability.MISSING,
            }
        ),
    )

    result = runner.invoke(
        app,
        ["agent", "install", "--agent", "all", "--context", str(root), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["result"]["skipped_clients"] == ["gemini"]
    planned = {plan["client"] for plan in payload["result"]["plans"]}
    assert planned == {"claude", "codex"}
    assert {warning["code"] for warning in payload["warnings"]} == {"AGENT_CLIENT_UNAVAILABLE"}


def test_all_with_no_available_clients_is_user_correctable(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Install None", context_id="cli-install-none")
    import workctx.adapters.agents as agents_module

    real_service = agents_module.AgentAdapterService

    def _stubbed_service(*args: object, **kwargs: object) -> AgentAdapterService:
        return real_service(executable_finder=_finder, version_probe=_probe)

    monkeypatch.setattr(agents_module, "AgentAdapterService", _stubbed_service)
    monkeypatch.setattr(
        AgentAdapterService,
        "detect",
        _fake_detect(
            {
                AgentClient.CLAUDE: ClientAvailability.MISSING,
                AgentClient.CODEX: ClientAvailability.MISSING,
                AgentClient.GEMINI: ClientAvailability.MISSING,
            }
        ),
    )

    result = runner.invoke(
        app,
        ["agent", "install", "--agent", "all", "--context", str(root), "--json"],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("available" in error["message"] for error in payload["errors"])
