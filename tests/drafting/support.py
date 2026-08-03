from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tasks_views.support import (
    ALICE_URI,
    CONTEXT_ID,
    OBSERVATION_URI,
    initialize_tasks_context,
    task,
)

from workctx.adapters.filesystem import CanonicalStore, render_markdown_bytes
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import Claim, ClaimStatus, Confidence, TaskStatus
from workctx.drafting import DraftPayload

PERSON_URI = ALICE_URI
TASK_URI = f"workctx://{CONTEXT_ID}/task/TASK-2026-001"
DRAFT_ID = "DRAFT-20260802-rollout-reply-01"
UNCERTAINTY = "## Uncertainty\n\nThe vendor test date is still unconfirmed.\n"


def initialize_drafting_context(root: Path) -> Path:
    initialize_tasks_context(root)
    store = CanonicalStore(root)
    timestamp = datetime(2026, 7, 31, 12, tzinfo=UTC)
    store.write_task(
        "03_work/tasks/TASK-2026-001.md",
        task(
            "TASK-2026-001",
            timestamp,
            status=TaskStatus.WAITING,
            waiting_on=(PERSON_URI,),
        ),
        "Fictional task waiting on Alice.\n",
    )
    claim = Claim(
        schema_version=1,
        id="CLM-2026-00001",
        subject=PERSON_URI,
        predicate="preferred_language",
        object="en",
        observed_at=timestamp,
        valid_from=timestamp,
        valid_to=None,
        status=ClaimStatus.CURRENT,
        supersedes=None,
        superseded_by=None,
        confidence=Confidence.HIGH,
        source_observations=[OBSERVATION_URI],
    )
    (root / "02_knowledge" / "CLM-2026-00001.md").write_bytes(
        render_markdown_bytes(claim, "Alice prefers English for this fictional exchange.\n")
    )
    SQLiteProjection(root).rebuild()
    return root


def draft_payload(*, body: str | None = None, draft_id: str | None = DRAFT_ID) -> DraftPayload:
    return DraftPayload(
        draft_id=draft_id,
        title="Rollout readiness reply",
        recipient_uri=PERSON_URI,
        purpose="Clarify the rollout state without inventing a commitment.",
        format="email",
        body=(
            f"Hello Alex,\n\nThe authentication review is ready for discussion.\n\n{UNCERTAINTY}"
            if body is None
            else body
        ),
        task_uri=TASK_URI,
        source_refs=(OBSERVATION_URI,),
        author_id="fictional-drafting-agent",
        agent="codex",
        model="gpt-fictional",
    )
