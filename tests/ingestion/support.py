from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain.transactions import TransactionProposal
from workctx.services.contexts import initialize_context
from workctx.transactions import ApplyResult, apply, verify_ledger

FIXED_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def initialize_ingestion_context(root: Path) -> Path:
    initialize_context(root, name="Fictional Ingestion Lab", context_id="ingestion-test")
    SQLiteProjection(root).rebuild()
    return root


def write_raw(root: Path, relative_path: str, content: bytes) -> Path:
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def processing_receipt(
    root: Path,
    artifact_reference: str | None,
    *,
    slug: str = "processing-proof",
) -> ApplyResult:
    identifier = f"PRJ-{slug}"
    proposal = TransactionProposal.model_validate(
        {
            "schema_version": 1,
            "id": f"TXP-20260802T120100Z-{slug}",
            "context_id": "ingestion-test",
            "base_revision": verify_ledger(root).head_hash,
            "actor": {
                "type": "human",
                "id": "fictional-operator",
                "agent": None,
                "model": None,
            },
            "created_at": "2026-08-02T12:01:00Z",
            "source_refs": [] if artifact_reference is None else [artifact_reference],
            "operations": [
                {
                    "op": "create",
                    "target": f"02_knowledge/{identifier}.md",
                    "payload": {
                        "kind": "entity",
                        "document": {
                            "schema_version": 1,
                            "id": identifier,
                            "entity_type": "project",
                            "title": f"Fictional {slug}",
                            "uri": f"workctx://ingestion-test/project/{identifier}",
                            "aliases": [],
                            "status": "active",
                            "confidence": "high",
                            "tags": ["fictional"],
                            "references": [],
                            "created_at": "2026-08-02T12:01:00Z",
                            "updated_at": "2026-08-02T12:01:00Z",
                        },
                        "body": "Fictional processing proof.\n",
                    },
                }
            ],
            "preconditions": [],
            "postconditions": [],
            "expected_views": ["sqlite"],
            "approval": "required",
        }
    )
    return apply(root, proposal, approved=True, session_id=f"test-{slug}")
