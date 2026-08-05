from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.cli import app
from workctx.suggestions import SuggestionService, SuggestionStatus, get_suggestion
from workctx.transactions import verify_ledger

from .support import MutableClock, initialize_suggestion_context, suggestion_payload

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
    if exit_code == 0:
        assert result.stderr == ""
    else:
        assert result.stderr.startswith("Error:")
    return payload


def test_suggestion_list_and_show_emit_canonical_envelopes(tmp_path: Path) -> None:
    root = initialize_suggestion_context(tmp_path / "list-show")
    clock = MutableClock(datetime(2026, 8, 3, 11, tzinfo=UTC))
    service = SuggestionService(root, clock=clock)
    first_id = "SUG-20260803-first-engine-proposal-01"
    second_id = "SUG-20260803-second-skill-review-01"
    service.create(
        suggestion_payload(
            root,
            suggestion_id=first_id,
            suggestion_type="engine_proposal",
            rationale="Review the first fictional engine proposal",
        ),
        approved=True,
    )
    service.create(
        suggestion_payload(
            root,
            suggestion_id=second_id,
            suggestion_type="skill_override",
            rationale="Review the second fictional skill change",
        ),
        approved=True,
    )

    listed = _envelope(
        runner.invoke(
            app,
            ["suggestion", "list", "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="suggestion.list",
    )
    assert listed["context_id"] == "fictional-suggestions"
    assert listed["result"]["count"] == 2
    assert [item["id"] for item in listed["result"]["suggestions"]] == [
        first_id,
        second_id,
    ]

    shown = _envelope(
        runner.invoke(
            app,
            ["suggestion", "show", first_id, "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="suggestion.show",
    )
    suggestion = shown["result"]["suggestion"]
    assert suggestion["id"] == first_id
    assert suggestion["status"] == "open"
    assert suggestion["body"].startswith("## Proposed outcome")
    assert suggestion["path"].endswith(f"/{first_id}.md")

    missing = _envelope(
        runner.invoke(
            app,
            [
                "suggestion",
                "show",
                "SUG-20260803-missing-record-01",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=1,
        command="suggestion.show",
    )
    assert missing["errors"][0]["code"] == "USER_CORRECTABLE"


def test_suggestion_adopt_and_reject_require_yes_and_report_receipts(tmp_path: Path) -> None:
    root = initialize_suggestion_context(tmp_path / "mutations")
    clock = MutableClock(datetime(2026, 8, 3, 11, tzinfo=UTC))
    service = SuggestionService(root, clock=clock)
    adopt_id = "SUG-20260803-adopt-engine-proposal-01"
    reject_id = "SUG-20260803-reject-skill-review-01"
    service.create(
        suggestion_payload(
            root,
            suggestion_id=adopt_id,
            suggestion_type="engine_proposal",
            rationale="Adopt the fictional engine proposal",
        ),
        approved=True,
    )
    service.create(
        suggestion_payload(
            root,
            suggestion_id=reject_id,
            suggestion_type="skill_override",
            rationale="Reject the fictional skill review",
        ),
        approved=True,
    )
    assert verify_ledger(root).event_count == 2

    denied = _envelope(
        runner.invoke(
            app,
            ["suggestion", "adopt", adopt_id, "--context", str(root), "--json"],
        ),
        exit_code=2,
        command="suggestion.adopt",
    )
    assert denied["errors"] == [
        {
            "code": "SUGGESTION_APPROVAL_REQUIRED",
            "message": "Suggestion adoption and rejection require explicit --yes approval.",
            "path": "$.yes",
            "repair_action": None,
        }
    ]
    assert verify_ledger(root).event_count == 2

    adopted = _envelope(
        runner.invoke(
            app,
            [
                "suggestion",
                "adopt",
                adopt_id,
                "--yes",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="suggestion.adopt",
    )
    assert adopted["result"]["operation"] == "adopted"
    assert adopted["result"]["suggestion"]["status"] == "adopted"
    assert adopted["result"]["receipt"]["committed"] is True

    conflict = _envelope(
        runner.invoke(
            app,
            [
                "suggestion",
                "adopt",
                adopt_id,
                "--yes",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=4,
        command="suggestion.adopt",
    )
    assert conflict["errors"][0]["code"] == "CONFLICT"
    assert verify_ledger(root).event_count == 3

    rejected = _envelope(
        runner.invoke(
            app,
            [
                "suggestion",
                "reject",
                reject_id,
                "--yes",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="suggestion.reject",
    )
    assert rejected["result"]["operation"] == "rejected"
    assert rejected["result"]["suggestion"]["status"] == "rejected"
    assert verify_ledger(root).event_count == 4
    assert get_suggestion(root, adopt_id).record.status is SuggestionStatus.ADOPTED
    assert get_suggestion(root, reject_id).record.status is SuggestionStatus.REJECTED

    filtered = _envelope(
        runner.invoke(
            app,
            [
                "suggestion",
                "list",
                "--status",
                "open",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="suggestion.list",
    )
    assert filtered["result"] == {"count": 0, "suggestions": []}
