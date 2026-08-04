"""Acceptance coverage for the connector CLI commands (lead wiring)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.cli import app
from workctx.services.contexts import initialize_context

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()

MANIFEST = """schema_version: 1
name: fictional-tracker
base_url: https://tracker.example.test
secret_ref: fictional-tracker-token
auth_style: bearer
snapshots:
  - id: open-items
    path: /api/items
"""


def _envelope(result: Any, *, exit_code: int, command: str) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == command
    assert payload["ok"] is (exit_code == 0)
    return payload


def test_list_reports_manifests_and_sync_refuses_unknown_names(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Connector CLI", context_id="cli-connectors")

    empty = _envelope(
        runner.invoke(app, ["connector", "list", "--context", str(root), "--json"]),
        exit_code=0,
        command="connector.list",
    )
    assert empty["result"] == {"count": 0, "connectors": []}

    manifest_dir = root / "07_connectors"
    manifest_dir.mkdir()
    (manifest_dir / "fictional-tracker.yaml").write_text(MANIFEST, encoding="utf-8")

    listed = _envelope(
        runner.invoke(app, ["connector", "list", "--context", str(root), "--json"]),
        exit_code=0,
        command="connector.list",
    )
    assert listed["result"]["count"] == 1
    connector = listed["result"]["connectors"][0]
    assert connector["name"] == "fictional-tracker"
    assert connector["secret_ref"] == "fictional-tracker-token"
    assert connector["snapshots"] == ["open-items"]

    missing = _envelope(
        runner.invoke(
            app,
            ["connector", "sync", "absent-connector", "--context", str(root), "--json"],
        ),
        exit_code=1,
        command="connector.sync",
    )
    assert any("absent-connector" in error["message"] for error in missing["errors"])
    assert "fictional-tracker-token" not in json.dumps(missing)
