from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workctx.adapters.filesystem import CanonicalStore, render_markdown_bytes
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import EntityFrontmatter
from workctx.domain.transactions import (
    EntityDocumentPayload,
    HumanActor,
    PathHashCondition,
    TransactionProposal,
    UpdateOperation,
)
from workctx.services.contexts import initialize_context
from workctx.suggestions import SuggestionPayload
from workctx.transactions import verify_ledger

CONTEXT_ID = "fictional-suggestions"
PROJECT_ID = "PRJ-orion"
PROJECT_PATH = f"02_knowledge/projects/{PROJECT_ID}.md"
PROJECT_URI = f"workctx://{CONTEXT_ID}/project/{PROJECT_ID}"
ACTOR = HumanActor(
    type="human",
    id="fictional-operator",
    agent=None,
    model=None,
)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


def initialize_suggestion_context(root: Path) -> Path:
    initialize_context(
        root,
        name="Fictional Suggestion Lab",
        context_id=CONTEXT_ID,
    )
    created_at = datetime(2026, 8, 3, 9, tzinfo=UTC)
    CanonicalStore(root).write_entity(
        PROJECT_PATH,
        project_entity(title="Project Orion", timestamp=created_at),
        "A deterministic fictional project.\n",
    )
    SQLiteProjection(root).rebuild()
    return root


def project_entity(*, title: str, timestamp: datetime) -> EntityFrontmatter:
    return EntityFrontmatter.model_validate(
        {
            "schema_version": 1,
            "id": PROJECT_ID,
            "entity_type": "project",
            "title": title,
            "uri": PROJECT_URI,
            "aliases": [],
            "status": "active",
            "confidence": "high",
            "tags": ["fictional"],
            "references": [],
            "created_at": datetime(2026, 8, 3, 9, tzinfo=UTC),
            "updated_at": timestamp,
        }
    )


def data_fix_proposal(
    root: Path,
    *,
    created_at: datetime | None = None,
    expected_hash: str | None = None,
) -> TransactionProposal:
    proposal_time = created_at or datetime(2026, 8, 3, 10, tzinfo=UTC)
    current = (root / PROJECT_PATH).read_bytes()
    current_hash = expected_hash or _hash_bytes(current)
    updated = project_entity(title="Project Orion Review", timestamp=proposal_time)
    body = "A deterministic fictional project with its reviewed title.\n"
    postimage = render_markdown_bytes(updated, body)
    return TransactionProposal(
        schema_version=1,
        id=f"TXP-{proposal_time:%Y%m%dT%H%M%SZ}-fix-project-title",
        context_id=CONTEXT_ID,
        base_revision=verify_ledger(root).head_hash,
        actor=ACTOR,
        created_at=proposal_time,
        source_refs=[PROJECT_URI],
        operations=[
            UpdateOperation(
                op="update",
                target=PROJECT_PATH,
                payload=EntityDocumentPayload(
                    kind="entity",
                    document=updated,
                    body=body,
                ),
                expected_hash=current_hash,
            )
        ],
        preconditions=[
            PathHashCondition(
                kind="path_hash",
                path=PROJECT_PATH,
                content_hash=current_hash,
            )
        ],
        postconditions=[
            PathHashCondition(
                kind="path_hash",
                path=PROJECT_PATH,
                content_hash=_hash_bytes(postimage),
            )
        ],
        expected_views=["sqlite"],
        approval="required",
    )


def suggestion_payload(
    root: Path,
    *,
    suggestion_id: str = "SUG-20260803-fix-project-title-01",
    suggestion_type: str = "data_fix",
    rationale: str = "Correct the fictional project title",
    signal: str = "A reviewed source uses a different fictional title.",
    supersedes: str | None = None,
    body: str = "## Proposed outcome\n\nUse the reviewed fictional project title.\n",
    proposal: TransactionProposal | None = None,
) -> SuggestionPayload:
    embedded = proposal
    if suggestion_type == "data_fix" and embedded is None:
        embedded = data_fix_proposal(root)
    return SuggestionPayload(
        id=suggestion_id,
        type=suggestion_type,
        rationale=rationale,
        signal=signal,
        source_refs=(PROJECT_URI,),
        proposal=embedded,
        actor=ACTOR,
        supersedes=supersedes,
        body=body,
    )


def _hash_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
