from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from workctx.adapters.filesystem import CanonicalStore, render_markdown_bytes
from workctx.domain import Claim, ClaimStatus, Task, TaskPriority, TaskStatus, TaskType
from workctx.services.contexts import initialize_context

CONTEXT_ID = "fictional-usage"
REPO_URI = "repo://fictional.example@abcdef1/src/reference.md#L1-L4"


def initialize_usage_context(root: Path, *, enabled: bool = False) -> Path:
    initialize_context(root, name="Fictional Usage Lab", context_id=CONTEXT_ID)
    if enabled:
        set_usage(root, enabled=True)
    return root


def set_usage(
    root: Path,
    *,
    enabled: bool,
    promotion_uses: int = 5,
    decay_days: int = 60,
) -> None:
    store = CanonicalStore(root)
    config = store.read_context_config()
    telemetry = config.telemetry.model_copy(
        update={
            "usage": enabled,
            "promotion_uses": promotion_uses,
            "decay_days": decay_days,
        }
    )
    store.write_context_config(config.model_copy(update={"telemetry": telemetry}))


def write_task(
    root: Path,
    task_id: str,
    *,
    updated_at: datetime,
    status: TaskStatus = TaskStatus.ACTIVE,
) -> str:
    uri = f"workctx://{CONTEXT_ID}/task/{task_id}"
    task = Task(
        schema_version=1,
        id=task_id,
        entity_type="task",
        title=f"Fictional task {task_id}",
        uri=uri,
        aliases=[],
        status=status,
        confidence="high",
        tags=["fictional"],
        references=[],
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=updated_at,
        task_type=TaskType.PARENT,
        parent_task=None,
        root_task=task_id,
        priority=TaskPriority.P2,
        owner=None,
        requester=None,
        waiting_on=[],
        due_at=None,
        next_action="Review the fictional task.",
        dependencies=[],
        blockers=[],
        source_observations=[],
    )
    CanonicalStore(root).write_task(f"03_work/{task_id}.md", task, "Fictional body.\n")
    return uri


def write_claim(
    root: Path,
    claim_id: str,
    *,
    observed_at: datetime,
    status: ClaimStatus = ClaimStatus.CURRENT,
) -> str:
    observation_uri = (
        f"workctx://{CONTEXT_ID}/observation/EVD-20260801-fictional-usage-01%23OBS-001"
    )
    claim = Claim(
        schema_version=1,
        id=claim_id,
        subject=f"workctx://{CONTEXT_ID}/system/SYS-fictional",
        predicate="has fictional state",
        object="active",
        observed_at=observed_at,
        valid_from=None,
        valid_to=None,
        status=status,
        supersedes=None,
        superseded_by=None,
        confidence="high",
        source_observations=[observation_uri],
    )
    path = root / "02_knowledge" / f"{claim_id}.md"
    path.write_bytes(render_markdown_bytes(claim, "Fictional claim body.\n"))
    return f"workctx://{CONTEXT_ID}/claim/{claim_id}"
