from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain.transactions import ZERO_REVISION, TransactionProposal
from workctx.services.contexts import initialize_context

TIMESTAMP = "2026-08-01T12:00:00Z"


def initialize_transaction_context(root: Path, *, build_projection: bool = True) -> Path:
    initialize_context(root, name="Fictional Transaction Lab", context_id="transaction-lab")
    if build_projection:
        SQLiteProjection(root).rebuild()
    return root


def entity_document(
    identifier: str,
    entity_type: str = "project",
    *,
    references: list[dict[str, Any]] | None = None,
    body: str = "Fictional transaction fixture.\n",
) -> dict[str, Any]:
    return {
        "kind": "entity",
        "document": {
            "schema_version": 1,
            "id": identifier,
            "entity_type": entity_type,
            "title": f"Fictional {identifier}",
            "uri": f"workctx://transaction-lab/{entity_type}/{identifier}",
            "aliases": [],
            "status": "active",
            "confidence": "high",
            "tags": ["fictional"],
            "references": references or [],
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
        },
        "body": body,
    }


def create_operation(
    identifier: str,
    entity_type: str = "project",
    *,
    references: list[dict[str, Any]] | None = None,
    body: str = "Fictional transaction fixture.\n",
) -> dict[str, Any]:
    return {
        "op": "create",
        "target": f"02_knowledge/{identifier}.md",
        "payload": entity_document(
            identifier,
            entity_type,
            references=references,
            body=body,
        ),
    }


def proposal(
    slug: str,
    operations: list[dict[str, Any]],
    *,
    base_revision: str = ZERO_REVISION,
    source_refs: list[str] | None = None,
    preconditions: list[dict[str, Any]] | None = None,
    postconditions: list[dict[str, Any]] | None = None,
    approval: str = "required",
) -> TransactionProposal:
    return TransactionProposal.model_validate(
        {
            "schema_version": 1,
            "id": f"TXP-20260801T120000Z-{slug}",
            "context_id": "transaction-lab",
            "base_revision": base_revision,
            "actor": {
                "type": "human",
                "id": "fictional-operator",
                "agent": None,
                "model": None,
            },
            "created_at": TIMESTAMP,
            "source_refs": source_refs or [],
            "operations": operations,
            "preconditions": preconditions or [],
            "postconditions": postconditions or [],
            "expected_views": ["sqlite"],
            "approval": approval,
        }
    )


def workspace_snapshot(root: Path, *, include_state: bool) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if not include_state and relative.startswith("98_state/"):
            continue
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def content_hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
