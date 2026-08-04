from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.cli import app
from workctx.suggestions import (
    SuggestionApprovalRequiredError,
    SuggestionStatus,
    SuggestionType,
    list_suggestions,
)
from workctx.transactions import verify_ledger
from workctx.usage import record, suggest_usage

from .support import REPO_URI, initialize_usage_context

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()


def _envelope(result: Any, *, exit_code: int, command: str) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == command
    assert payload["ok"] is (exit_code == 0)
    return payload


def _promotion_signal(root: Path, now: datetime) -> None:
    for index in range(5):
        record(root, "resolve", REPO_URI, now=now - timedelta(days=index))


def test_suggestion_creation_requires_approval_and_is_idempotent_while_open(
    tmp_path: Path,
) -> None:
    root = initialize_usage_context(tmp_path / "suggestions", enabled=True)
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    _promotion_signal(root, now)

    with pytest.raises(SuggestionApprovalRequiredError):
        suggest_usage(root, now=now, approved=False)
    assert list_suggestions(root) == ()
    assert verify_ledger(root).event_count == 0

    first = suggest_usage(root, now=now, approved=True)
    second = suggest_usage(root, now=now, approved=True)

    assert len(first.created) == 1
    assert first.skipped == ()
    assert second.created == ()
    assert len(second.skipped) == 1
    documents = list_suggestions(root, statuses={SuggestionStatus.OPEN})
    assert len(documents) == 1
    assert documents[0].record.type is SuggestionType.ENGINE_PROPOSAL
    assert documents[0].record.source_refs == [REPO_URI]
    assert documents[0].record.signal.startswith("usage:promotion:")
    assert verify_ledger(root).event_count == 1


def test_usage_cli_commands_emit_envelopes_and_suggest_requires_yes(tmp_path: Path) -> None:
    root = initialize_usage_context(tmp_path / "cli", enabled=True)
    now = datetime.now(UTC)
    _promotion_signal(root, now)

    status = _envelope(
        runner.invoke(app, ["usage", "status", "--context", str(root), "--json"]),
        exit_code=0,
        command="usage.status",
    )
    assert status["result"]["enabled"] is True
    assert status["result"]["summary"]["totals"]["uses_30d"] == 5

    evaluated = _envelope(
        runner.invoke(app, ["usage", "evaluate", "--context", str(root), "--json"]),
        exit_code=0,
        command="usage.evaluate",
    )
    assert evaluated["result"]["count"] == 1
    assert evaluated["result"]["candidates"][0]["kind"] == "promotion"

    denied = _envelope(
        runner.invoke(app, ["usage", "suggest", "--context", str(root), "--json"]),
        exit_code=2,
        command="usage.suggest",
    )
    assert denied["errors"][0]["code"] == "USAGE_APPROVAL_REQUIRED"
    assert verify_ledger(root).event_count == 0

    created = _envelope(
        runner.invoke(
            app,
            ["usage", "suggest", "--yes", "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="usage.suggest",
    )
    assert created["result"]["created_count"] == 1
    assert created["result"]["created"][0]["receipt"]["committed"] is True

    repeated = _envelope(
        runner.invoke(
            app,
            ["usage", "suggest", "--yes", "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="usage.suggest",
    )
    assert repeated["result"]["created_count"] == 0
    assert repeated["result"]["skipped_count"] == 1
    assert verify_ledger(root).event_count == 1
