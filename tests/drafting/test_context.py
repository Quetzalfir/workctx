from __future__ import annotations

from pathlib import Path

from workctx.drafting import gather_reply_context

from .support import PERSON_URI, TASK_URI, initialize_drafting_context


def test_gather_reply_context_is_deterministic_and_complete(tmp_path: Path) -> None:
    root = initialize_drafting_context(tmp_path / "reply-context")

    first = gather_reply_context(root, PERSON_URI, task_uri=TASK_URI)
    second = gather_reply_context(root, PERSON_URI, task_uri=TASK_URI)

    assert first == second
    assert first.context_pack.focal_uri == TASK_URI
    assert [(claim.predicate, claim.object) for claim in first.person_claims] == [
        ("preferred_language", "en")
    ]
    assert [task.id for task in first.waiting_on_tasks] == ["TASK-2026-001"]
    assert first.selected_task is not None
    assert first.selected_task.id == "TASK-2026-001"
    assert first.selected_task.status.value == "waiting"
    assert first.recent_ledger_activity.event_count == 0
    assert first.recent_ledger_activity.head_revision == "0" * 64
