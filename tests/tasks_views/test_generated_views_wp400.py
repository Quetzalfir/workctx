from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workctx.domain import TaskPriority, TaskStatus
from workctx.tasks import TaskService
from workctx.transactions import verify_ledger
from workctx.validation import validate_workspace
from workctx.views import ViewName, ViewService, brief

from .support import (
    JORDAN_URI,
    MutableClock,
    human_actor,
    initialize_tasks_context,
    task,
)


def test_all_views_are_deterministic_and_have_verified_generated_headers(
    tmp_path: Path,
) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    mutation_clock = MutableClock(datetime(2026, 6, 1, 10, tzinfo=UTC))
    tasks = TaskService(root, actor=human_actor(), clock=mutation_clock)
    tasks.create_task(
        task(
            "TASK-2026-020",
            mutation_clock.value,
            status=TaskStatus.BLOCKED,
            priority=TaskPriority.P0,
            waiting_on=(JORDAN_URI,),
            next_action="Ask Jordan for the fictional review.",
        ),
        approved=True,
    )
    mutation_clock.value += timedelta(minutes=1)
    tasks.create_task(
        task(
            "TASK-2026-021",
            mutation_clock.value,
            status=TaskStatus.READY,
            priority=TaskPriority.P2,
            owner=None,
            next_action="Start the fictional implementation.",
        ),
        approved=True,
    )
    revision = verify_ledger(root).head_hash
    generated_at = datetime(2026, 8, 2, 18, tzinfo=UTC)
    views = ViewService(root, clock=lambda: generated_at)

    first = views.rebuild_views()
    first_bytes = {view.path: (root / Path(view.path)).read_bytes() for view in first.views}
    second = views.rebuild_views()
    second_bytes = {view.path: (root / Path(view.path)).read_bytes() for view in second.views}

    assert first == second
    assert first_bytes == second_bytes
    assert {view.name for view in first.views} == set(ViewName)
    assert first.source_revision == revision

    shutil.rmtree(root / "04_views")
    recreated = views.rebuild_views()
    recreated_bytes = {view.path: (root / Path(view.path)).read_bytes() for view in recreated.views}

    assert recreated == first
    assert recreated_bytes == first_bytes
    expected_header = (
        "---\n"
        "generated_by: workctx.views\n"
        f"source_revision: {revision}\n"
        "generated_at: 2026-08-02T18:00:00Z\n"
        "---\n\n"
    ).encode()
    for content in first_bytes.values():
        assert content.startswith(expected_header)
    assert verify_ledger(root).head_hash == revision
    assert verify_ledger(root).event_count == 2

    current_focus = first_bytes["04_views/current-focus.md"].decode()
    waiting_on = first_bytes["04_views/waiting-on.md"].decode()
    stale = first_bytes["04_views/stale-knowledge.md"].decode()
    assert current_focus.index("TASK-2026-020") < current_focus.index("TASK-2026-021")
    assert "Jordan Lee" in waiting_on
    assert "CLM-2026-00001" in stale


def test_brief_payload_per_view_rebuild_and_validation_exclusion(tmp_path: Path) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    mutation_clock = MutableClock(datetime(2026, 6, 1, 11, tzinfo=UTC))
    tasks = TaskService(root, actor=human_actor(), clock=mutation_clock)
    tasks.create_task(
        task(
            "TASK-2026-030",
            mutation_clock.value,
            status=TaskStatus.BLOCKED,
            waiting_on=(JORDAN_URI,),
        ),
        approved=True,
    )
    generated_at = datetime(2026, 8, 2, 19, tzinfo=UTC)
    service = ViewService(root, clock=lambda: generated_at)

    payload = service.brief()
    public_payload = brief(root, clock=lambda: generated_at)
    rebuilt = service.rebuild_view(ViewName.BRIEF)

    assert payload == public_payload
    assert payload.schema_version == 1
    assert payload.context_id == "tasks-lab"
    assert payload.generated_at == generated_at
    assert [item.id for item in payload.today_focus] == ["TASK-2026-030"]
    assert [item.id for item in payload.blockers] == ["TASK-2026-030"]
    assert payload.waiting_on[0].display_name == "Jordan Lee"
    assert payload.waiting_on[0].person_uri == JORDAN_URI
    assert payload.stale_claims
    assert payload.recent_ledger_activity.event_count == 1
    assert payload.recent_ledger_activity.head_revision == payload.source_revision
    assert [view.name for view in rebuilt.views] == [ViewName.BRIEF]
    assert rebuilt.views[0].path == "04_views/brief.md"

    report = validate_workspace(root)
    assert not any(issue.path.startswith("04_views/") for issue in report.issues)
    assert "generated_by: workctx.views" in (root / "04_views" / "brief.md").read_text(
        encoding="utf-8"
    )
