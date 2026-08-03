from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workctx.adapters.sqlite import ClaimRecord, SQLiteProjection
from workctx.domain import ClaimStatus, TaskHierarchyError, TaskStatus, TaskType
from workctx.domain.transactions import ZERO_REVISION
from workctx.tasks import TaskService
from workctx.transactions import StaleRevisionError, verify_ledger

from .support import (
    ALICE_URI,
    JORDAN_URI,
    OBSERVATION_URI,
    MutableClock,
    human_actor,
    initialize_tasks_context,
    task,
)


def test_create_transition_reassign_and_due_history_use_real_transactions(
    tmp_path: Path,
) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    clock = MutableClock(datetime(2026, 6, 1, 13, tzinfo=UTC))
    service = TaskService(root, actor=human_actor(), clock=clock)
    due = datetime(2026, 6, 10, 17, tzinfo=UTC)

    created = service.create_task(
        task("TASK-2026-001", clock.value, due_at=due),
        body="Fictional task body.\n",
        approved=True,
    )
    clock.value += timedelta(minutes=1)
    transitioned = service.transition_status(
        "TASK-2026-001",
        TaskStatus.BLOCKED,
        source_observations=[OBSERVATION_URI],
        approved=True,
    )
    clock.value += timedelta(minutes=1)
    reassigned = service.update_owner(
        "TASK-2026-001",
        JORDAN_URI,
        source_observations=[OBSERVATION_URI],
        approved=True,
    )
    clock.value += timedelta(minutes=1)
    new_due = datetime(2026, 6, 12, 17, tzinfo=UTC)
    rescheduled = service.update_due(
        "TASK-2026-001",
        new_due,
        source_observations=[OBSERVATION_URI],
        approved=True,
    )

    assert created.claim_ids == (
        "CLM-2026-00001",
        "CLM-2026-00002",
        "CLM-2026-00003",
    )
    assert transitioned.claim_ids == ("CLM-2026-00004",)
    assert reassigned.claim_ids == ("CLM-2026-00005",)
    assert rescheduled.claim_ids == ("CLM-2026-00006",)

    projection = SQLiteProjection(root)
    projected = projection.get_task("TASK-2026-001")
    assert projected is not None
    assert projected.status is TaskStatus.BLOCKED
    assert projected.owner == JORDAN_URI
    assert projected.due_at == new_due
    assert projected.body.strip() == "Fictional task body."
    claims = projection.claims_for_subject(projected.uri)

    _assert_claim_chain(
        claims,
        predicate="status",
        old_id="CLM-2026-00001",
        new_id="CLM-2026-00004",
        new_value="blocked",
    )
    _assert_claim_chain(
        claims,
        predicate="owner",
        old_id="CLM-2026-00002",
        new_id="CLM-2026-00005",
        new_value=JORDAN_URI,
    )
    _assert_claim_chain(
        claims,
        predicate="due",
        old_id="CLM-2026-00003",
        new_id="CLM-2026-00006",
        new_value="2026-06-12T17:00:00Z",
    )

    verification = verify_ledger(root)
    assert verification.event_count == 4
    ledger = root / "99_meta" / "audit" / "ledger.jsonl"
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [len(event["operations"]) for event in events] == [4, 3, 3, 3]
    assert events[-1]["event_hash"] == verification.head_hash
    assert all(
        any(
            operation.get("target") == "03_work/tasks/TASK-2026-001.md"
            for operation in event["operations"]
        )
        for event in events
    )
    assert projected.owner != ALICE_URI


def test_waiting_on_and_next_action_updates_are_transactional(tmp_path: Path) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    clock = MutableClock(datetime(2026, 6, 1, 14, tzinfo=UTC))
    service = TaskService(root, actor=human_actor(), clock=clock)
    service.create_task(task("TASK-2026-002", clock.value), approved=True)

    clock.value += timedelta(minutes=1)
    waiting = service.update_waiting_on(
        "TASK-2026-002",
        [JORDAN_URI],
        source_observations=[OBSERVATION_URI],
        approved=True,
    )
    clock.value += timedelta(minutes=1)
    next_action = service.update_next_action(
        "TASK-2026-002",
        "Ask Jordan for the fictional review.",
        source_observations=[OBSERVATION_URI],
        approved=True,
    )

    assert waiting.claim_ids == ()
    assert next_action.claim_ids == ()
    projected = SQLiteProjection(root).get_task("TASK-2026-002")
    assert projected is not None
    assert projected.waiting_on == (JORDAN_URI,)
    assert projected.next_action == "Ask Jordan for the fictional review."
    assert verify_ledger(root).event_count == 3


def test_missing_subtask_parent_is_refused_before_a_proposal(tmp_path: Path) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    clock = MutableClock(datetime(2026, 6, 1, 15, tzinfo=UTC))
    service = TaskService(root, actor=human_actor(), clock=clock)
    orphan = task(
        "TASK-2026-900-ST01",
        clock.value,
        task_type=TaskType.SUBTASK,
    )

    with pytest.raises(TaskHierarchyError, match="missing parent"):
        service.create_subtask(orphan, approved=True)

    assert verify_ledger(root).event_count == 0
    assert not (root / "03_work" / "tasks" / f"{orphan.id}.md").exists()


def test_stale_revision_conflict_surfaces_unchanged(tmp_path: Path) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    clock = MutableClock(datetime(2026, 6, 1, 16, tzinfo=UTC))
    service = TaskService(root, actor=human_actor(), clock=clock)
    service.create_task(task("TASK-2026-010", clock.value), approved=True)
    clock.value += timedelta(minutes=1)

    with pytest.raises(StaleRevisionError) as captured:
        service.create_task(
            task("TASK-2026-011", clock.value),
            approved=True,
            base_revision=ZERO_REVISION,
        )

    assert captured.value.code == "TXN-STALE-REVISION"
    assert verify_ledger(root).event_count == 1
    assert not (root / "03_work" / "tasks" / "TASK-2026-011.md").exists()


def _assert_claim_chain(
    claims: tuple[ClaimRecord, ...],
    *,
    predicate: str,
    old_id: str,
    new_id: str,
    new_value: object,
) -> None:
    selected = {claim.id: claim for claim in claims if claim.predicate == predicate}
    old = selected[old_id]
    new = selected[new_id]
    assert old.status is ClaimStatus.SUPERSEDED
    assert old.superseded_by == new_id
    assert new.status is ClaimStatus.CURRENT
    assert new.supersedes == old_id
    assert new.object == new_value
