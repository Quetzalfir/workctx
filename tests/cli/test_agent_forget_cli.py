from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workctx.adapters.agents import AgentAdapterService, AgentClient, _install_records
from workctx.cli import app
from workctx.services.contexts import initialize_context

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_install_records(
    isolated_user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _install_records,
        "user_config_path",
        lambda *_args, **_kwargs: isolated_user_config_dir,
    )


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    return {"fake-codex": "codex 0.5.0"}[executable]


def _installed_context(tmp_path: Path, context_id: str) -> Path:
    root = tmp_path / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    service = AgentAdapterService(executable_finder=_finder, version_probe=_probe)
    service.install(service.plan_install(root, AgentClient.CODEX))
    return root


def _context_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_forget_json_is_idempotent_and_changes_only_machine_local_trust(
    tmp_path: Path,
) -> None:
    root = _installed_context(tmp_path, "forget-json")
    before = _context_files(root)

    first = runner.invoke(app, ["agent", "forget", str(root), "--json"])
    second = runner.invoke(app, ["agent", "forget", str(root), "--json"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["command"] == "agent.forget"
    assert first_payload["result"] == {
        "root": str(root.resolve()),
        "removed": True,
        "adapters": ["codex"],
        "install_treatment": "untracked",
        "message": ("A subsequent agent install will treat existing adapter state as untracked."),
    }
    assert second_payload["result"]["removed"] is False
    assert second_payload["result"]["adapters"] == []
    assert _context_files(root) == before


def test_forget_human_output_defaults_to_the_resolved_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _installed_context(tmp_path, "forget-human")
    monkeypatch.chdir(root)

    result = runner.invoke(app, ["agent", "forget"])

    assert result.exit_code == 0, result.output
    assert "Forgot trusted adapter records for codex" in result.stdout
    assert "subsequent agent install" in result.stdout
    assert "untracked" in result.stdout
