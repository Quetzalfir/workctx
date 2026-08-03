from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.adapters.sqlite import SQLiteProjection
from workctx.cli import app
from workctx.domain import Task
from workctx.domain.transactions import HumanActor, TransactionProposal
from workctx.drafting import DraftPayload
from workctx.evidence import (
    begin_processing,
    build_evidence_proposal,
    complete_processing,
    stage_observations,
)
from workctx.ingestion import IngestionService, RegisterRequest
from workctx.services.contexts import initialize_context
from workctx.tasks import TaskService
from workctx.transactions import apply, verify_ledger

REPOSITORY_ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads(
        (REPOSITORY_ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8")
    )
)
RUNNER = CliRunner()

CONTEXT_ID = "alpha-acceptance"
INGESTED_AT = datetime(2026, 8, 2, 19, tzinfo=UTC)
TASK_TIME = datetime(2026, 8, 2, 21, tzinfo=UTC)
DRAFT_TIME = datetime(2026, 8, 2, 22, tzinfo=UTC)
VIEW_TIME = datetime(2026, 8, 2, 23, tzinfo=UTC)
TIMESTAMP = "2026-08-02T20:00:00Z"

RAW_NOTE = "Fictional readiness review: Alex Rivera must confirm the launch date.\n"
EVIDENCE_ID = "EVD-20260802-launch-readiness-01"
OBSERVATION_ID = f"{EVIDENCE_ID}#OBS-001"
OBSERVATION_URI = f"workctx://{CONTEXT_ID}/observation/{EVIDENCE_ID}%23OBS-001"
PERSON_ID = "PER-alex-rivera"
PERSON_URI = f"workctx://{CONTEXT_ID}/person/{PERSON_ID}"
TASK_ID = "TASK-2026-001"
TASK_URI = f"workctx://{CONTEXT_ID}/task/{TASK_ID}"
DRAFT_ID = "DRAFT-20260802-launch-readiness-01"
QUESTION = "launch readiness"
UNCERTAIN_MARKER = "UNCERTAIN: The fictional launch date is not yet confirmed."


@dataclass(frozen=True, slots=True)
class OperationalContext:
    root: Path
    artifact_id: str
    artifact_ref: str
    archive_path: str


def initialize_acceptance_context(root: Path) -> Path:
    initialize_context(root, name="Fictional Alpha Acceptance", context_id=CONTEXT_ID)
    SQLiteProjection(root).rebuild()
    return root


def write_raw_note(
    root: Path, *, content: str = RAW_NOTE, name: str = "readiness-note.txt"
) -> Path:
    path = root / "00_inbox" / "raw" / name
    path.write_text(content, encoding="utf-8")
    return path


def evidence_payload(artifact_ref: str) -> dict[str, Any]:
    """Return the canonical agent-authored evidence staging payload shape."""

    return {
        "schema_version": 1,
        "actor": {
            "type": "agent",
            "id": "fictional-acceptance-agent",
            "agent": "codex",
            "model": "fixture-model",
        },
        "source_refs": [artifact_ref],
        "evidence_note": {
            "id": EVIDENCE_ID,
            "title": "Fictional launch readiness evidence",
            "body": "# Summary\n\nThe launch readiness review needs a confirmation.\n",
            "aliases": [],
            "status": "active",
            "confidence": "high",
            "tags": ["fictional", "acceptance"],
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
        },
        "observations": [
            {
                "id": OBSERVATION_ID,
                "kind": "fact",
                "statement": "Fictional launch readiness is waiting for Alex Rivera.",
                "confidence": "high",
                "source": {
                    "ref": artifact_ref,
                    "locator": {
                        "type": "line_range",
                        "start_line": 1,
                        "end_line": 1,
                    },
                },
            }
        ],
        "new_entities": [
            {
                "document": {
                    "schema_version": 1,
                    "id": PERSON_ID,
                    "entity_type": "person",
                    "title": "Alex Rivera",
                    "uri": PERSON_URI,
                    "aliases": ["Alex"],
                    "status": "active",
                    "confidence": "high",
                    "tags": ["fictional"],
                    "references": [],
                    "created_at": TIMESTAMP,
                    "updated_at": TIMESTAMP,
                },
                "body": "Fictional contact for the launch readiness review.\n",
            }
        ],
        "tasks": [],
        "claims": [],
        "relations": [],
    }


def human_actor() -> HumanActor:
    return HumanActor(
        type="human",
        id="fictional-operator",
        agent=None,
        model=None,
    )


def acceptance_task(
    *,
    task_id: str = TASK_ID,
    source_observations: tuple[str, ...] = (OBSERVATION_URI,),
) -> Task:
    task_uri = f"workctx://{CONTEXT_ID}/task/{task_id}"
    return Task.model_validate(
        {
            "schema_version": 1,
            "id": task_id,
            "entity_type": "task",
            "title": "Confirm fictional launch readiness",
            "uri": task_uri,
            "aliases": [],
            "status": "waiting",
            "confidence": "high",
            "tags": ["fictional", "acceptance"],
            "references": [],
            "created_at": TASK_TIME,
            "updated_at": TASK_TIME,
            "task_type": "parent",
            "parent_task": None,
            "root_task": task_id,
            "priority": "P0",
            "owner": PERSON_URI,
            "requester": None,
            "waiting_on": [PERSON_URI],
            "due_at": None,
            "next_action": "Ask Alex Rivera to confirm the fictional launch date.",
            "dependencies": [],
            "blockers": [],
            "source_observations": list(source_observations),
        }
    )


def acceptance_draft_payload() -> DraftPayload:
    return DraftPayload(
        draft_id=DRAFT_ID,
        title="Launch readiness confirmation request",
        recipient_uri=PERSON_URI,
        purpose="Request confirmation without inventing a launch commitment.",
        format="email",
        body=(
            "Hello Alex,\n\n"
            "The fictional launch review is waiting for your confirmation.\n\n"
            f"## Uncertainty\n\n{UNCERTAIN_MARKER}\n"
        ),
        task_uri=TASK_URI,
        source_refs=(OBSERVATION_URI,),
        author_id="fictional-drafting-agent",
        agent="codex",
        model="fixture-model",
    )


def build_entity_create_proposal(root: Path) -> TransactionProposal:
    identifier = "SYS-approval-gate"
    target = f"02_knowledge/systems/{identifier}.md"
    return TransactionProposal.model_validate(
        {
            "schema_version": 1,
            "id": "TXP-20260802T220000Z-approval-gate",
            "context_id": CONTEXT_ID,
            "base_revision": verify_ledger(root).head_hash,
            "actor": human_actor().model_dump(mode="python"),
            "created_at": DRAFT_TIME,
            "source_refs": [OBSERVATION_URI],
            "operations": [
                {
                    "op": "create",
                    "target": target,
                    "payload": {
                        "kind": "entity",
                        "document": {
                            "schema_version": 1,
                            "id": identifier,
                            "entity_type": "system",
                            "title": "Fictional approval gate",
                            "uri": f"workctx://{CONTEXT_ID}/system/{identifier}",
                            "aliases": [],
                            "status": "active",
                            "confidence": "high",
                            "tags": ["fictional"],
                            "references": [],
                            "created_at": DRAFT_TIME,
                            "updated_at": DRAFT_TIME,
                        },
                        "body": "Fictional entity used to verify approval enforcement.\n",
                    },
                }
            ],
            "preconditions": [{"kind": "path_absent", "path": target}],
            "postconditions": [{"kind": "path_exists", "path": target}],
            "expected_views": ["sqlite"],
            "approval": "required",
        }
    )


def create_operational_context(root: Path) -> OperationalContext:
    initialize_acceptance_context(root)
    write_raw_note(root)
    registration = IngestionService(root, clock=lambda: INGESTED_AT).register(
        RegisterRequest(
            path="00_inbox/raw/readiness-note.txt",
            source_type="note",
            source_origin="fictional acceptance fixture",
            event_at=INGESTED_AT,
            participants=("Alex Rivera",),
        ),
        session_id="e2e-register",
    )
    artifact = registration.artifact
    begin_processing(root, artifact.manifest.id)
    staging = stage_observations(root, artifact.manifest.id, evidence_payload(artifact.reference))
    proposal = build_evidence_proposal(staging)
    receipt = apply(root, proposal, approved=True, session_id="e2e-evidence")
    archived = complete_processing(root, artifact.manifest.id, receipt)
    TaskService(root, actor=human_actor(), clock=lambda: TASK_TIME).create_task(
        acceptance_task(),
        body="Evidence-backed fictional readiness task.\n",
        approved=True,
        session_id="e2e-task",
    )
    return OperationalContext(
        root=root,
        artifact_id=artifact.manifest.id,
        artifact_ref=artifact.reference,
        archive_path=archived.destination_path,
    )


def invoke_envelope(
    arguments: list[str],
    *,
    exit_code: int = 0,
    command: str,
) -> dict[str, Any]:
    result = RUNNER.invoke(app, arguments)
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


def invoke_mcp(
    root: Path,
    calls: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    return anyio.run(_invoke_mcp, root, calls)


async def _invoke_mcp(
    root: Path,
    calls: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    ClientSession, create_client_server_memory_streams, create_server = _mcp_runtime()
    server = create_server(root)
    discovered: dict[str, dict[str, Any]] = {}
    responses: dict[str, dict[str, Any]] = {}
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as task_group,
    ):
        task_group.start_soon(
            server.run,
            server_streams[0],
            server_streams[1],
            server.create_initialization_options(),
        )
        async with ClientSession(client_streams[0], client_streams[1]) as session:
            await session.initialize()
            listing = await session.list_tools()
            for tool in listing.tools:
                discovered[tool.name] = {
                    "input_schema": tool.input_schema,
                    "read_only_hint": (
                        None if tool.annotations is None else tool.annotations.read_only_hint
                    ),
                }
            for name, arguments in calls:
                response = await session.call_tool(name, arguments)
                responses[name] = {
                    "is_error": response.is_error,
                    "structured_content": response.structured_content,
                }
        task_group.cancel_scope.cancel()
    return discovered, responses


def _mcp_runtime() -> tuple[Any, Any, Any]:
    """Import the SDK without resolving pytest's local ``tests/mcp`` package."""

    local_test_package = sys.modules.pop("mcp", None)
    original_sys_path = sys.path[:]
    tests_directory = Path(__file__).resolve().parents[1]
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != tests_directory]
    try:
        from mcp import ClientSession
        from mcp.shared.memory import create_client_server_memory_streams

        from workctx.mcp.server import create_server
    finally:
        sys.path[:] = original_sys_path
        if local_test_package is not None:
            sys.modules["mcp"] = local_test_package
    return ClientSession, create_client_server_memory_streams, create_server
