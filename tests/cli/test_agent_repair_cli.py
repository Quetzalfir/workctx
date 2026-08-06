"""The CLI must expose a recovery path for drifted or untrusted adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workctx.adapters.agents import AgentAdapterService, AgentClient
from workctx.cli import app
from workctx.services.contexts import initialize_context

runner = CliRunner()
LOCAL_EDIT = "Fictional local edit of generated output.\n"


@pytest.fixture(autouse=True)
def _isolate_user_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workctx.adapters.agents import _install_records, personalization

    fake_home = tmp_path_factory.mktemp("cli-repair-home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))
    user_config = fake_home / "AppData" / "Local" / "workctx"
    monkeypatch.setattr(_install_records, "user_config_path", lambda *_a, **_k: user_config)
    monkeypatch.setattr(
        personalization,
        "user_personalization_path",
        lambda: user_config / personalization.PERSONALIZATION_FILENAME,
    )


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    return {"fake-codex": "codex 0.5.0", "fake-claude": "Claude Code 2.0.0"}[executable]


def _stub_service(monkeypatch: pytest.MonkeyPatch) -> None:
    import workctx.adapters.agents as agents_module

    real = agents_module.AgentAdapterService

    def factory(*_args: object, **_kwargs: object) -> AgentAdapterService:
        return real(executable_finder=_finder, version_probe=_probe)

    monkeypatch.setattr(agents_module, "AgentAdapterService", factory)


def _install(root: Path) -> AgentAdapterService:
    service = AgentAdapterService(executable_finder=_finder, version_probe=_probe)
    service.install(service.plan_install(root, AgentClient.CLAUDE))
    return service


def test_repair_restores_deleted_generated_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Repair", context_id="cli-repair")
    _install(root)
    generated = root / ".claude" / "skills" / "manage-tasks" / "SKILL.md"
    original = generated.read_bytes()
    generated.unlink()
    _stub_service(monkeypatch)

    preview = runner.invoke(
        app, ["agent", "repair", "--agent", "claude", "--context", str(root), "--json"]
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["result"]["applied"] is False
    assert not generated.exists(), "planning must not write anything"

    applied = runner.invoke(
        app,
        ["agent", "repair", "--agent", "claude", "--yes", "--context", str(root), "--json"],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["result"]["applied"] is True
    assert generated.read_bytes() == original


def test_repair_refuses_to_clobber_edited_generated_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Drift", context_id="cli-drift")
    _install(root)
    edited = root / ".claude" / "skills" / "manage-tasks" / "SKILL.md"
    edited.write_text(LOCAL_EDIT, encoding="utf-8")
    _stub_service(monkeypatch)

    applied = runner.invoke(
        app,
        ["agent", "repair", "--agent", "claude", "--yes", "--context", str(root), "--json"],
    )

    assert applied.exit_code == 4, applied.output
    payload = json.loads(applied.stdout)
    assert payload["ok"] is False
    assert any("three-factor authority" in error["message"] for error in payload["errors"])
    assert edited.read_text(encoding="utf-8") == LOCAL_EDIT


def test_uninstall_plans_then_removes_manifest_owned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Uninstall", context_id="cli-uninstall")
    _install(root)
    assert (root / "CLAUDE.md").is_file()
    _stub_service(monkeypatch)

    preview = runner.invoke(
        app, ["agent", "uninstall", "--agent", "claude", "--context", str(root), "--json"]
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["result"]["applied"] is False
    assert (root / "CLAUDE.md").is_file()

    applied = runner.invoke(
        app,
        ["agent", "uninstall", "--agent", "claude", "--yes", "--context", str(root), "--json"],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["result"]["applied"] is True
    assert not (root / "CLAUDE.md").exists()
    assert (root / "context.yaml").is_file(), "uninstall must never touch canonical files"
