from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from topics.test_filters_cli import _context, _entity, _write_markdown
from typer.testing import CliRunner

from workctx.adapters.sqlite import SQLiteProjection
from workctx.checkpoints import (
    CheckpointReason,
    CheckpointService,
    CheckpointStatus,
    CheckpointValidationError,
)
from workctx.cli import app
from workctx.mcp.application import McpToolService
from workctx.retrieval import build_pack
from workctx.transactions import verify_ledger
from workctx.validation import validate_workspace

ROOT = Path(__file__).parents[2]
SAVED_AT = datetime(2026, 10, 14, 13, tzinfo=UTC)
CHECKED_AT = datetime(2026, 10, 14, 15, tzinfo=UTC)
runner = CliRunner()

COMMON_URI = "workctx://topic-filter/project/PRJ-common"
ALPHA_URI = "workctx://topic-filter/project/PRJ-alpha"
BETA_URI = "workctx://topic-filter/project/PRJ-beta"
COMMON_TASK_URI = "workctx://topic-filter/task/TASK-2026-001"
ALPHA_TASK_URI = "workctx://topic-filter/task/TASK-2026-002"


def test_checkpoint_save_is_audited_and_check_reports_full_staleness_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path / "context").context_root
    service = CheckpointService(root, clock=lambda: SAVED_AT)
    references = (
        COMMON_URI,
        ALPHA_URI,
        BETA_URI,
        COMMON_TASK_URI,
        ALPHA_TASK_URI,
    )

    before = verify_ledger(root)
    preview = service.save(
        "Fictional handoff",
        references=references,
        topic="alpha-team",
    )
    assert preview.applied is False
    assert preview.preview is not None and preview.preview.valid
    assert not (root / preview.checkpoint.relative_path).exists()
    assert verify_ledger(root) == before

    saved = service.save(
        "Fictional handoff",
        references=references,
        topic="alpha-team",
        approved=True,
    )
    assert saved.applied is True
    assert saved.receipt is not None and saved.receipt.committed
    assert saved.receipt.applied_targets == (saved.checkpoint.relative_path,)
    checkpoint_path = root / saved.checkpoint.relative_path
    assert checkpoint_path.is_file()
    assert verify_ledger(root).event_count == before.event_count + 1
    checkpoint_text = checkpoint_path.read_text(encoding="utf-8")
    assert "Needle Alpha" not in checkpoint_text
    assert "Alpha needle material" not in checkpoint_text
    assert "sha256:" in checkpoint_text

    _write_markdown(
        root / "02_knowledge" / "PRJ-alpha.md",
        {
            **_entity("PRJ-alpha", "Needle Alpha", topics=["alpha"]),
            "updated_at": "2026-10-14T14:00:00Z",
        },
        "Changed fictional alpha material.",
    )
    (root / "02_knowledge" / "PRJ-beta.md").rename(root / "02_knowledge" / "PRJ-beta-moved.md")
    (root / "03_work" / "TASK-2026-001.md").unlink()
    _write_markdown(
        root / "02_knowledge" / "PRJ-successor.md",
        {
            **_entity(
                "PRJ-successor",
                "Task successor",
                references=[
                    {
                        "relation": "supersedes",
                        "target": ALPHA_TASK_URI,
                    }
                ],
            ),
            "created_at": "2026-10-14T14:00:00Z",
            "updated_at": "2026-10-14T14:00:00Z",
        },
        "Fictional successor.",
    )
    _write_markdown(
        root / "02_knowledge" / "PRJ-alpha-new.md",
        {
            **_entity("PRJ-alpha-new", "New alpha item", topics=["alpha"]),
            "created_at": "2026-10-14T14:30:00Z",
            "updated_at": "2026-10-14T14:30:00Z",
        },
        "Fictional new topic item.",
    )
    SQLiteProjection(root).rebuild()

    checked = CheckpointService(root, clock=lambda: CHECKED_AT).check(saved.checkpoint.id)
    by_uri = {item.uri: item for item in checked.items}
    assert (by_uri[COMMON_URI].status, by_uri[COMMON_URI].reason) == (
        CheckpointStatus.GREEN,
        CheckpointReason.UNCHANGED,
    )
    assert (by_uri[ALPHA_URI].status, by_uri[ALPHA_URI].reason) == (
        CheckpointStatus.YELLOW,
        CheckpointReason.CHANGED,
    )
    assert by_uri[ALPHA_URI].changes == ("content", "revision")
    assert by_uri[ALPHA_URI].current_revision == datetime(2026, 10, 14, 14, tzinfo=UTC)
    assert (by_uri[BETA_URI].status, by_uri[BETA_URI].reason) == (
        CheckpointStatus.RED,
        CheckpointReason.MOVED,
    )
    assert by_uri[BETA_URI].successors[0].source_path == ("02_knowledge/PRJ-beta-moved.md")
    assert (by_uri[COMMON_TASK_URI].status, by_uri[COMMON_TASK_URI].reason) == (
        CheckpointStatus.RED,
        CheckpointReason.DELETED,
    )
    assert (by_uri[ALPHA_TASK_URI].status, by_uri[ALPHA_TASK_URI].reason) == (
        CheckpointStatus.RED,
        CheckpointReason.SUPERSEDED,
    )
    assert [location.model_dump(mode="json") for location in by_uri[ALPHA_TASK_URI].successors] == [
        {
            "uri": "workctx://topic-filter/project/PRJ-successor",
            "source_path": "02_knowledge/PRJ-successor.md",
            "revision": "2026-10-14T14:00:00Z",
            "content_hash": by_uri[ALPHA_TASK_URI].successors[0].content_hash,
        }
    ]
    assert checked.counts.model_dump() == {"green": 1, "yellow": 1, "red": 3, "new": 1}
    assert [item.model_dump(mode="json") for item in checked.new_items] == [
        {
            "uri": "workctx://topic-filter/project/PRJ-alpha-new",
            "source_path": "02_knowledge/PRJ-alpha-new.md",
            "created_at": "2026-10-14T14:30:00Z",
        }
    ]

    human = runner.invoke(
        app,
        ["checkpoint", "check", saved.checkpoint.id, "--context", str(root)],
        terminal_width=200,
    )
    assert human.exit_code == 0, human.output
    assert "changes=content,revision" in human.stdout
    assert "current_revision=2026-10-14T14:00:00+00:00" in human.stdout
    assert "successor: workctx://topic-filter/project/PRJ-successor" in human.stdout

    monkeypatch.setattr("workctx.checkpoints.service._utc_now", lambda: CHECKED_AT)
    mcp = McpToolService(root).invoke(
        "checkpoint_check",
        {"schema_version": 1, "checkpoint_id": saved.checkpoint.id},
    )
    assert mcp.envelope.ok
    assert mcp.envelope.result["checkpoint"] == checked.model_dump(mode="json")


def test_checkpoint_schema_pack_capture_secret_rejection_and_validation(
    tmp_path: Path,
) -> None:
    root = _context(tmp_path / "context").context_root
    projection = SQLiteProjection(root)
    outcome = build_pack(projection, COMMON_URI)
    assert outcome.built and outcome.pack is not None
    pack_path = tmp_path / "last-context-pack.json"
    pack_path.write_text(
        json.dumps({"result": {"pack": outcome.pack.model_dump(mode="json")}}),
        encoding="utf-8",
    )
    service = CheckpointService(root, clock=lambda: SAVED_AT)
    captured = service.save("Pack handoff", from_pack=pack_path)
    assert {item.uri for item in captured.checkpoint.items} >= {
        COMMON_URI,
        ALPHA_URI,
        BETA_URI,
    }
    assert not (root / captured.checkpoint.relative_path).exists()

    schema = json.loads((ROOT / "schemas" / "checkpoint.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        captured.checkpoint.model_dump(mode="json", exclude_none=True)
    )

    before = verify_ledger(root)
    with pytest.raises(CheckpointValidationError, match="secret"):
        service.save(
            "api_key=FICTIONAL_CHECKPOINT_SECRET_123456",
            references=[COMMON_URI],
            approved=True,
        )
    assert verify_ledger(root) == before
    assert not list((root / "99_meta").glob("CHK-*.yaml"))

    malformed = root / "99_meta" / "CHK-bad.yaml"
    malformed.write_text("[]\n", encoding="utf-8")
    issues = [
        issue for issue in validate_workspace(root).issues if issue.code == "CHECKPOINT-INVALID"
    ]
    assert [issue.path for issue in issues] == ["99_meta/CHK-bad.yaml"]


def test_checkpoint_cli_previews_applies_lists_and_checks(tmp_path: Path) -> None:
    root = _context(tmp_path / "context").context_root
    base = ["--context", str(root), "--json"]

    preview = runner.invoke(
        app,
        ["checkpoint", "save", "CLI handoff", "--ref", COMMON_URI, *base],
    )
    assert preview.exit_code == 0, preview.output
    preview_payload = json.loads(preview.stdout)
    assert preview_payload["command"] == "checkpoint.save"
    assert preview_payload["result"]["applied"] is False
    preview_id = preview_payload["result"]["checkpoint"]["id"]
    assert not (root / "99_meta" / f"{preview_id}.yaml").exists()

    applied = runner.invoke(
        app,
        [
            "checkpoint",
            "save",
            "CLI handoff",
            "--ref",
            COMMON_URI,
            "--yes",
            *base,
        ],
    )
    assert applied.exit_code == 0, applied.output
    applied_payload = json.loads(applied.stdout)
    checkpoint_id = applied_payload["result"]["checkpoint"]["id"]
    assert applied_payload["result"]["applied"] is True
    assert (root / "99_meta" / f"{checkpoint_id}.yaml").is_file()

    listed = runner.invoke(app, ["checkpoint", "list", *base])
    assert listed.exit_code == 0, listed.output
    list_payload = json.loads(listed.stdout)
    assert list_payload["command"] == "checkpoint.list"
    assert [item["id"] for item in list_payload["result"]["checkpoints"]] == [checkpoint_id]

    checked = runner.invoke(app, ["checkpoint", "check", checkpoint_id, *base])
    assert checked.exit_code == 0, checked.output
    check_payload = json.loads(checked.stdout)
    assert check_payload["command"] == "checkpoint.check"
    assert check_payload["result"]["counts"] == {
        "green": 1,
        "yellow": 0,
        "red": 0,
        "new": 0,
    }
