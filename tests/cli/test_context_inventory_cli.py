"""Acceptance coverage for advisory context registration and inventory."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

import workctx.adapters.filesystem.registry as registry_module
import workctx.services.contexts as contexts_module
from workctx.adapters.filesystem import ContextLock
from workctx.adapters.filesystem.registry import ContextRegistry
from workctx.adapters.sqlite import SQLiteProjection
from workctx.cli import app
from workctx.domain.transactions import (
    ZERO_REVISION,
    AuditCreateOperation,
    AuditEvent,
    AuditEventContent,
    HumanActor,
)
from workctx.services.contexts import initialize_context
from workctx.transactions.ledger import LEDGER_RELATIVE_PATH, encode_event_line

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()


@pytest.fixture
def context_cli_root() -> Iterator[Path]:
    """Use an ordinary temp root because pytest's private root is sandbox-hostile."""

    parent = Path(tempfile.gettempdir()) / "workctx-context-inventory-cli-tests"
    parent.mkdir(mode=0o755, exist_ok=True)
    root = parent / f"case-{uuid4().hex}"
    root.mkdir(mode=0o755)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def registry_file(
    isolated_user_config_dir: Path,
) -> Path:
    return isolated_user_config_dir / "contexts.json"


def _envelope(result: Any, *, exit_code: int, command: str) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == command
    assert payload["ok"] is (exit_code == 0)
    return payload


def _context(parent: Path, context_id: str) -> Path:
    root = parent / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    return root


def _manifest(artifact_id: str, *, status: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": artifact_id,
        "content_hash": f"sha256:{'0' * 64}",
        "original_name": f"{artifact_id}.txt",
        "media_type": "text/plain",
        "source_type": "note",
        "source_origin": "fixture://context-inventory",
        "event_at": "2026-08-05T10:00:00Z",
        "event_at_inferred": False,
        "ingested_at": "2026-08-05T10:01:00Z",
        "language": "en",
        "participants": [],
        "classification": "internal",
        "status": status,
        "preserved_path": f"00_inbox/raw/{artifact_id}.txt",
        "sidecars": [],
        "duplicate_of": None,
        "notes": "Fictional inventory fixture.",
    }


def _event(sequence: int, prev_hash: str, *, context_id: str) -> AuditEvent:
    timestamp = datetime(2026, 8, 5, 12, 0, sequence, tzinfo=UTC)
    suffix = f"20260805T1200{sequence:02d}Z-inventory-{sequence}"
    content = AuditEventContent.model_validate(
        {
            "schema_version": 1,
            "id": f"AUD-{suffix}",
            "proposal_id": f"TXP-{suffix}",
            "context_id": context_id,
            "timestamp": timestamp,
            "actor": HumanActor(
                type="human",
                id="fictional-operator",
                agent=None,
                model=None,
            ),
            "action": "apply",
            "result": "committed",
            "base_revision": prev_hash,
            "source_refs": [],
            "operations": [
                AuditCreateOperation(
                    op="create",
                    target=f"03_work/tasks/TASK-2026-{sequence:03d}.md",
                    postimage_hash=f"sha256:{sequence:064x}",
                )
            ],
            "prev_hash": prev_hash,
        }
    )
    return AuditEvent.seal(content)


def test_list_reports_zero_one_and_several_contexts_in_stable_order(
    context_cli_root: Path,
    registry_file: Path,
) -> None:
    empty = _envelope(
        runner.invoke(app, ["context", "list", "--json"]),
        exit_code=0,
        command="context.list",
    )
    assert empty["result"] == {"count": 0, "contexts": []}

    _context(context_cli_root, "middle-context")
    single = _envelope(
        runner.invoke(app, ["context", "list", "--json"]),
        exit_code=0,
        command="context.list",
    )
    assert single["result"]["count"] == 1
    row = single["result"]["contexts"][0]
    assert row["id"] == "middle-context"
    assert row["name"] == "Middle Context"
    assert row["kind"] == "project"
    assert row["profile"] == "hybrid"
    assert row["language"] == "en"
    assert row["stats"] == {
        "tasks": 0,
        "entities": 0,
        "evidence_notes": 0,
        "pending_inbox_artifacts": 0,
        "ledger_events": 0,
        "last_ledger_activity": None,
    }

    _context(context_cli_root, "zulu-context")
    _context(context_cli_root, "alpha-context")
    several = _envelope(
        runner.invoke(app, ["context", "list", "--json"]),
        exit_code=0,
        command="context.list",
    )
    assert [row["id"] for row in several["result"]["contexts"]] == [
        "alpha-context",
        "middle-context",
        "zulu-context",
    ]
    assert registry_file.is_file()

    human = runner.invoke(app, ["context", "list"])
    assert human.exit_code == 0, human.output
    assert "Registered contexts" in human.stdout
    assert "alpha-context" in human.stdout
    assert "Activity / state" in human.stdout


def test_register_rebinds_and_unregister_is_idempotent_without_touching_context(
    context_cli_root: Path,
    registry_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contexts_module, "register_context", lambda *_args, **_kwargs: None)
    first_parent = context_cli_root / "first"
    second_parent = context_cli_root / "second"
    first_parent.mkdir()
    second_parent.mkdir()
    first = _context(first_parent, "shared-context")
    second = _context(second_parent, "shared-context")

    with monkeypatch.context() as cwd_patch:
        cwd_patch.chdir(first)
        registered = _envelope(
            runner.invoke(app, ["context", "register", "--json"]),
            exit_code=0,
            command="context.register",
        )
    assert registered["result"] == {
        "id": "shared-context",
        "path": str(first.resolve()),
        "active": False,
    }
    before = registry_file.read_bytes()
    repeated = _envelope(
        runner.invoke(app, ["context", "register", str(first), "--json"]),
        exit_code=0,
        command="context.register",
    )
    assert repeated["result"] == registered["result"]
    assert registry_file.read_bytes() == before

    _envelope(
        runner.invoke(app, ["context", "register", str(second), "--json"]),
        exit_code=0,
        command="context.register",
    )
    assert ContextRegistry().get("shared-context") == second.resolve()

    removed = _envelope(
        runner.invoke(app, ["context", "unregister", "shared-context", "--json"]),
        exit_code=0,
        command="context.unregister",
    )
    repeated_remove = _envelope(
        runner.invoke(app, ["context", "unregister", "shared-context", "--json"]),
        exit_code=0,
        command="context.unregister",
    )
    assert removed["result"] == {"id": "shared-context", "removed": True}
    assert repeated_remove["result"] == {"id": "shared-context", "removed": False}
    assert (second / "context.yaml").is_file()


def test_list_preserves_missing_mismatched_and_unreadable_rows(
    context_cli_root: Path,
    registry_file: Path,
) -> None:
    missing = _context(context_cli_root, "missing-context")
    mismatched = _context(context_cli_root, "mismatched-context")
    unreadable = _context(context_cli_root, "unreadable-context")

    (missing / "context.yaml").unlink()
    raw = yaml.safe_load((mismatched / "context.yaml").read_text(encoding="utf-8"))
    raw["id"] = "configured-elsewhere"
    (mismatched / "context.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )
    (unreadable / "context.yaml").write_text(": invalid", encoding="utf-8")

    payload = _envelope(
        runner.invoke(app, ["context", "list", "--json"]),
        exit_code=0,
        command="context.list",
    )
    rows = {row["id"]: row for row in payload["result"]["contexts"]}
    assert rows["missing-context"]["missing"] is True
    assert rows["missing-context"]["error"] is None
    assert rows["missing-context"]["stats"] is None
    assert rows["mismatched-context"]["mismatched"] is True
    assert rows["mismatched-context"]["configured_id"] == "configured-elsewhere"
    assert rows["mismatched-context"]["error"] is None
    assert rows["unreadable-context"]["missing"] is False
    assert rows["unreadable-context"]["error"] == "Unable to read context configuration."
    assert registry_file.is_file()


def test_context_init_envelope_survives_registry_failure(
    context_cli_root: Path,
    registry_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_cli_root / "created-without-registry"

    def fail_registration(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected registry failure")

    monkeypatch.setattr(contexts_module, "register_context", fail_registration)
    with pytest.warns(RuntimeWarning, match="advisory user registry"):
        payload = _envelope(
            runner.invoke(
                app,
                [
                    "context",
                    "init",
                    str(target),
                    "--name",
                    "Created Without Registry",
                    "--json",
                ],
            ),
            exit_code=0,
            command="context.init",
        )

    assert payload["context_id"] == "created-without-registry"
    assert payload["result"]["root"] == str(target.resolve())
    assert (target / "context.yaml").is_file()
    assert not registry_file.exists()


def test_list_stats_are_canonical_verified_and_take_no_write_path(
    context_cli_root: Path,
    registry_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(context_cli_root, "stats-context")
    for relative in (
        "03_work/tasks/TASK-2026-001.md",
        "03_work/tasks/TASK-2026-002.md",
        "02_knowledge/people/PER-fictional.md",
        "02_knowledge/systems/SYS-fictional.md",
        "02_knowledge/evidence/EVD-fictional.md",
    ):
        path = root / relative
        path.write_text("Fictional canonical fixture.\n", encoding="utf-8")

    manifests = root / "00_inbox" / "manifests"
    (manifests / "pending.json").write_text(
        json.dumps(_manifest("ART-20260805-pending-note-01", status="pending")),
        encoding="utf-8",
    )
    (manifests / "processed.json").write_text(
        json.dumps(_manifest("ART-20260805-processed-note-01", status="processed")),
        encoding="utf-8",
    )

    first = _event(1, ZERO_REVISION, context_id="stats-context")
    second = _event(2, first.event_hash, context_id="stats-context")
    ledger = root / Path(LEDGER_RELATIVE_PATH)
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(encode_event_line(first) + encode_event_line(second))

    def forbidden_write_path(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("context list entered a forbidden write path")

    monkeypatch.setattr(ContextLock, "acquire", forbidden_write_path)
    monkeypatch.setattr(SQLiteProjection, "rebuild", forbidden_write_path)
    monkeypatch.setattr(registry_module, "_registry_mutation_guard", forbidden_write_path)

    payload = _envelope(
        runner.invoke(app, ["context", "list", "--json"]),
        exit_code=0,
        command="context.list",
    )
    stats = payload["result"]["contexts"][0]["stats"]
    assert stats == {
        "tasks": 2,
        "entities": 3,
        "evidence_notes": 1,
        "pending_inbox_artifacts": 1,
        "ledger_events": 2,
        "last_ledger_activity": "2026-08-05T12:00:02Z",
    }
    assert registry_file.is_file()


def test_registry_failure_does_not_break_list_and_command_failures_keep_envelopes(
    context_cli_root: Path,
    registry_file: Path,
) -> None:
    registry_file.parent.mkdir(parents=True)
    registry_file.write_text("not-json\n", encoding="utf-8")

    listed = _envelope(
        runner.invoke(app, ["context", "list", "--json"]),
        exit_code=0,
        command="context.list",
    )
    assert listed["result"] == {"count": 0, "contexts": []}
    assert listed["warnings"] == [
        {
            "code": "CONTEXT_REGISTRY_UNAVAILABLE",
            "message": (
                "The advisory user registry could not be read; no registrations are shown."
            ),
            "path": None,
            "repair_action": None,
        }
    ]

    unregister_failed = _envelope(
        runner.invoke(app, ["context", "unregister", "fictional-context", "--json"]),
        exit_code=1,
        command="context.unregister",
    )
    assert unregister_failed["errors"][0]["code"] == "USER_CORRECTABLE"

    missing_path = context_cli_root / "not-a-context"
    register_failed = _envelope(
        runner.invoke(app, ["context", "register", str(missing_path), "--json"]),
        exit_code=1,
        command="context.register",
    )
    assert register_failed["errors"][0]["code"] == "CONTEXT_NOT_FOUND"
