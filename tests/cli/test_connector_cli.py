"""Acceptance coverage for the connector CLI commands (lead wiring)."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

import workctx.connectors as connector_module
from workctx.cli import app
from workctx.connectors import (
    ConnectorSyncError,
    ConnectorSyncFailureKind,
    ConnectorSyncOutcome,
    SyncAllResult,
    SyncResult,
)
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
    schedule: hourly
"""


@pytest.fixture
def connector_cli_tmp_path() -> Iterator[Path]:
    """Use ordinary permissions instead of pytest's sandbox-hostile 0700 temp root."""

    parent = Path(tempfile.gettempdir()) / "workctx-connector-cli-tests"
    parent.mkdir(mode=0o755, exist_ok=True)
    root = parent / f"case-{uuid4().hex}"
    root.mkdir(mode=0o755)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _envelope(result: Any, *, exit_code: int, command: str) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == command
    assert payload["ok"] is (exit_code == 0)
    return payload


def test_list_reports_manifests_and_sync_refuses_unknown_names(
    connector_cli_tmp_path: Path,
) -> None:
    root = connector_cli_tmp_path / "context"
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


def test_status_envelope_reports_schedule_last_success_and_due_now(
    connector_cli_tmp_path: Path,
) -> None:
    root = connector_cli_tmp_path / "context"
    initialize_context(root, name="Fictional Connector CLI", context_id="cli-connectors")
    manifest_dir = root / "07_connectors"
    manifest_dir.mkdir()
    (manifest_dir / "fictional-tracker.yaml").write_text(MANIFEST, encoding="utf-8")
    state_dir = root / "98_state" / "connectors"
    state_dir.mkdir()
    (state_dir / "last-sync.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "connectors": {"fictional-tracker": {"open-items": "2000-01-01T00:00:00Z"}},
            }
        ),
        encoding="utf-8",
    )

    payload = _envelope(
        runner.invoke(app, ["connector", "status", "--context", str(root), "--json"]),
        exit_code=0,
        command="connector.status",
    )

    assert payload["result"]["count"] == 1
    assert payload["result"]["snapshots"] == [
        {
            "connector_name": "fictional-tracker",
            "snapshot_id": "open-items",
            "schedule": "hourly",
            "last_success": "2000-01-01T00:00:00Z",
            "due_now": True,
        }
    ]


def test_sync_name_and_all_are_mutually_exclusive_usage_error(
    connector_cli_tmp_path: Path,
) -> None:
    root = connector_cli_tmp_path / "context"
    initialize_context(root, name="Fictional Connector CLI", context_id="cli-connectors")

    payload = _envelope(
        runner.invoke(
            app,
            [
                "connector",
                "sync",
                "fictional-tracker",
                "--all",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=2,
        command="connector.sync",
    )

    assert payload["result"] == {}
    assert payload["errors"][0]["code"] == "CONNECTOR_SYNC_SELECTION"
    assert "mutually exclusive" in payload["errors"][0]["message"]


def test_sync_all_reports_partial_results_and_aggregate_exit_semantics(
    connector_cli_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = connector_cli_tmp_path / "context"
    initialize_context(root, name="Fictional Connector CLI", context_id="cli-connectors")
    successful = ConnectorSyncOutcome(
        connector_name="b-successful",
        snapshot_ids=("good-items",),
        attempted=True,
        result=SyncResult(connector_name="b-successful", snapshots=(), duration_ms=0),
    )
    success_batch = SyncAllResult(outcomes=(successful,), duration_ms=0)
    failed = ConnectorSyncOutcome(
        connector_name="a-failing",
        snapshot_ids=("failed-items",),
        attempted=True,
        error=ConnectorSyncError(
            kind=ConnectorSyncFailureKind.STATUS,
            snapshot_id="failed-items",
            message="Connector 'a-failing' snapshot 'failed-items' returned HTTP 503.",
        ),
    )
    partial_batch = SyncAllResult(outcomes=(failed, successful), duration_ms=0)
    returned = success_batch
    due_only_calls: list[bool] = []

    def fake_sync_all(_root: Path, *, due_only: bool = False) -> SyncAllResult:
        due_only_calls.append(due_only)
        return returned

    monkeypatch.setattr(connector_module, "sync_all", fake_sync_all)

    succeeded = _envelope(
        runner.invoke(
            app,
            ["connector", "sync", "--all", "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="connector.sync",
    )
    assert succeeded["result"]["outcomes"][0]["connector_name"] == "b-successful"

    returned = partial_batch
    partial = _envelope(
        runner.invoke(
            app,
            [
                "connector",
                "sync",
                "--all",
                "--due",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=1,
        command="connector.sync",
    )

    assert due_only_calls == [False, True]
    assert [item["connector_name"] for item in partial["result"]["outcomes"]] == [
        "a-failing",
        "b-successful",
    ]
    assert partial["result"]["outcomes"][1]["result"]["connector_name"] == "b-successful"
    assert partial["errors"] == [
        {
            "code": "CONNECTOR_STATUS",
            "message": "Connector 'a-failing' snapshot 'failed-items' returned HTTP 503.",
            "path": "$.outcomes[0].error",
        }
    ]
