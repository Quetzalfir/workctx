"""`agent install --agent all` targets detected clients only (lead fix)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from workctx.adapters.agents import (
    AgentAdapterService,
    AgentClient,
    ClientAvailability,
)
from workctx.cli import app
from workctx.services.contexts import initialize_context

runner = CliRunner()


class _Capability:
    def __init__(self, client: AgentClient, availability: ClientAvailability) -> None:
        self.client = client
        self.availability = availability


def _fake_detect(availabilities: dict[AgentClient, ClientAvailability]):
    def detect(self: AgentAdapterService, project_root: Path) -> tuple[_Capability, ...]:
        return tuple(_Capability(client, state) for client, state in availabilities.items())

    return detect


def test_all_plans_only_available_clients_and_warns_about_the_rest(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Install All", context_id="cli-install-all")
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
