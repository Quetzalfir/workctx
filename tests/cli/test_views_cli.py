"""Acceptance coverage for operational-view CLI commands."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.adapters.filesystem import CanonicalStore
from workctx.adapters.sqlite import SQLiteProjection
from workctx.cli import app
from workctx.domain import Task
from workctx.services.contexts import initialize_context
from workctx.views import ViewName

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
FIXED_NOW = datetime(2026, 8, 2, 18, tzinfo=UTC)
ZERO_REVISION = "0" * 64
runner = CliRunner()


def _initialize(root: Path) -> Path:
    initialize_context(root, name="Fictional Views CLI", context_id="views-cli")
    task = Task.model_validate(
        {
            "schema_version": 1,
            "id": "TASK-2026-040",
            "entity_type": "task",
            "title": "Review fictional view output",
            "uri": "workctx://views-cli/task/TASK-2026-040",
            "aliases": [],
            "status": "blocked",
            "confidence": "high",
            "tags": ["fictional"],
            "references": [],
            "created_at": "2026-08-01T12:00:00Z",
            "updated_at": "2026-08-01T12:00:00Z",
            "task_type": "parent",
            "parent_task": None,
            "root_task": "TASK-2026-040",
            "priority": "P0",
            "owner": "Alex",
            "requester": None,
            "waiting_on": ["Jordan"],
            "due_at": None,
            "next_action": "Review the fictional generated brief.",
            "dependencies": [],
            "blockers": ["Fictional dependency"],
            "source_observations": [],
        }
    )
    CanonicalStore(root).write_task(
        "03_work/TASK-2026-040.md",
        task,
        "Fictional CLI view fixture.\n",
    )
    SQLiteProjection(root).rebuild()
    return root


def _fix_view_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("workctx.views.service._utc_now", lambda: FIXED_NOW)


def _envelope(
    result: Any,
    *,
    exit_code: int,
    command: str,
) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    assert result.stdout.strip().startswith("{")
    assert result.stdout.strip().endswith("}")
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == command
    if exit_code == 0:
        assert payload["ok"] is True
        assert result.stderr == ""
    else:
        assert payload["ok"] is False
        assert result.stderr.startswith("Error:")
    return payload


def test_brief_emits_structured_payload_and_compact_human_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _initialize(tmp_path / "context")
    _fix_view_clock(monkeypatch)

    payload = _envelope(
        runner.invoke(app, ["brief", "--context", str(root), "--json"]),
        exit_code=0,
        command="brief",
    )
    brief = payload["result"]
    assert set(brief) == {
        "schema_version",
        "context_id",
        "generated_at",
        "source_revision",
        "today_focus",
        "blockers",
        "waiting_on",
        "stale_claims",
        "recent_ledger_activity",
    }
    assert payload["context_id"] == "views-cli"
    assert brief["context_id"] == "views-cli"
    assert brief["generated_at"] == "2026-08-02T18:00:00Z"
    assert brief["source_revision"] == ZERO_REVISION
    assert [task["id"] for task in brief["today_focus"]] == ["TASK-2026-040"]
    assert [task["id"] for task in brief["blockers"]] == ["TASK-2026-040"]
    assert brief["waiting_on"][0]["display_name"] == "Jordan"
    assert brief["stale_claims"] == []
    assert brief["recent_ledger_activity"]["event_count"] == 0
    assert not (root / "04_views" / "brief.md").exists()

    human = runner.invoke(app, ["brief", "--context", str(root)])
    assert human.exit_code == 0, human.output
    assert human.stderr == ""
    assert "Daily brief — views-cli" in human.stdout
    assert "Today focus" in human.stdout
    assert "TASK-2026-040" in human.stdout
    assert "Blockers" in human.stdout
    assert "Waiting on" in human.stdout
    assert "Recent ledger" in human.stdout
    assert not (root / "04_views" / "brief.md").exists()


def test_two_full_rebuilds_are_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _initialize(tmp_path / "context")
    _fix_view_clock(monkeypatch)

    first = _envelope(
        runner.invoke(app, ["view", "rebuild", "--context", str(root), "--json"]),
        exit_code=0,
        command="view.rebuild",
    )["result"]
    first_bytes = {
        view["path"]: (root / Path(view["path"])).read_bytes() for view in first["views"]
    }
    second = _envelope(
        runner.invoke(app, ["view", "rebuild", "--context", str(root), "--json"]),
        exit_code=0,
        command="view.rebuild",
    )["result"]
    second_bytes = {
        view["path"]: (root / Path(view["path"])).read_bytes() for view in second["views"]
    }

    assert first == second
    assert first_bytes == second_bytes
    assert first["generated_at"] == "2026-08-02T18:00:00Z"
    assert first["source_revision"] == ZERO_REVISION
    assert [view["name"] for view in first["views"]] == [view.value for view in ViewName]
    for view in first["views"]:
        digest = hashlib.sha256(first_bytes[view["path"]]).hexdigest()
        assert view["content_hash"] == f"sha256:{digest}"


def test_only_filter_rebuilds_one_named_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _initialize(tmp_path / "context")
    _fix_view_clock(monkeypatch)

    rebuilt = _envelope(
        runner.invoke(
            app,
            [
                "view",
                "rebuild",
                "--only",
                "waiting-on",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="view.rebuild",
    )["result"]

    assert rebuilt["views"] == [
        {
            "name": "waiting-on",
            "path": "04_views/waiting-on.md",
            "content_hash": rebuilt["views"][0]["content_hash"],
        }
    ]
    assert (root / "04_views" / "waiting-on.md").is_file()
    for view in ViewName:
        if view is not ViewName.WAITING_ON:
            assert not (root / Path(view.relative_path)).exists()


def test_invalid_only_filter_is_user_correctable_and_preserves_split_streams(
    tmp_path: Path,
) -> None:
    root = _initialize(tmp_path / "context")

    payload = _envelope(
        runner.invoke(
            app,
            [
                "view",
                "rebuild",
                "--only",
                "unknown",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=1,
        command="view.rebuild",
    )

    assert payload["context_id"] == "views-cli"
    assert payload["result"] == {"only": "unknown"}
    assert payload["errors"][0]["code"] == "VIEW_NAME_INVALID"
