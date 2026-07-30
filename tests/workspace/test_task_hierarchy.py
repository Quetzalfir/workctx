from __future__ import annotations

import pytest
from pydantic import ValidationError

from workctx.domain.tasks import Task, TaskHierarchyError, validate_task_hierarchy

from .schema_support import load_fixture


def _parent_payload() -> dict[str, object]:
    return load_fixture("positive", "task")


def _subtask_payload() -> dict[str, object]:
    payload = _parent_payload()
    payload.update(
        {
            "id": "TASK-2026-001-ST01",
            "uri": "workctx://fictional-context/task/TASK-2026-001-ST01",
            "title": "Run the fictional schema checks",
            "task_type": "subtask",
            "parent_task": "TASK-2026-001",
            "root_task": "TASK-2026-001",
        }
    )
    return payload


def test_valid_parent_and_subtask_hierarchy() -> None:
    parent = Task.model_validate(_parent_payload())
    subtask = Task.model_validate(_subtask_payload())

    validate_task_hierarchy([parent, subtask])


def test_parent_task_must_be_self_rooted() -> None:
    payload = _parent_payload()
    payload["root_task"] = "TASK-2026-999"

    with pytest.raises(ValidationError, match="self-rooted"):
        Task.model_validate(payload)


def test_subtask_must_match_parent_prefix_and_root() -> None:
    payload = _subtask_payload()
    payload["parent_task"] = "TASK-2026-999"

    with pytest.raises(ValidationError, match="parent_task"):
        Task.model_validate(payload)

    payload = _subtask_payload()
    payload["root_task"] = "TASK-2026-999"

    with pytest.raises(ValidationError, match="root_task"):
        Task.model_validate(payload)


def test_subtask_with_missing_parent_is_rejected() -> None:
    subtask = Task.model_validate(_subtask_payload())

    with pytest.raises(TaskHierarchyError, match="missing parent"):
        validate_task_hierarchy([subtask])


def test_task_hierarchy_cannot_cross_context_boundaries() -> None:
    parent = Task.model_validate(_parent_payload())
    subtask_payload = _subtask_payload()
    subtask_payload["uri"] = "workctx://other-context/task/TASK-2026-001-ST01"
    subtask = Task.model_validate(subtask_payload)

    with pytest.raises(TaskHierarchyError, match="multiple context IDs"):
        validate_task_hierarchy([parent, subtask])


def test_duplicate_task_ids_are_rejected() -> None:
    parent = Task.model_validate(_parent_payload())

    with pytest.raises(TaskHierarchyError, match="Duplicate task ID"):
        validate_task_hierarchy([parent, parent])
