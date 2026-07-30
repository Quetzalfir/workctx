import json
from pathlib import Path

from typer.testing import CliRunner

from workctx.cli import app
from workctx.doctor import DoctorCheck

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_context_init_and_validate_json(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    created = runner.invoke(
        app,
        ["context", "init", str(target), "--name", "Demo", "--id", "demo"],
    )
    assert created.exit_code == 0, created.stdout

    validated = runner.invoke(app, ["context", "validate", str(target), "--json"])
    assert validated.exit_code == 0, validated.stdout
    payload = json.loads(validated.stdout)
    assert payload["ok"] is True
    assert payload["context_id"] == "demo"


def test_doctor_json_serializes_slot_dataclasses(monkeypatch) -> None:
    monkeypatch.setattr(
        "workctx.cli.run_doctor",
        lambda: [DoctorCheck("python", "ok", "3.13.0", True)],
    )

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["result"][0]["name"] == "python"
