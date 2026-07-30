from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from workctx.domain.entities import EntityFrontmatter
from workctx.domain.references import WorkctxUri

PARENT_TASK_ID_PATTERN = r"^TASK-[0-9]{4}-[0-9]{3}$"
SUBTASK_ID_PATTERN = r"^TASK-[0-9]{4}-[0-9]{3}-ST[0-9]{2}$"
TASK_ID_PATTERN = r"^TASK-[0-9]{4}-[0-9]{3}(?:-ST[0-9]{2})?$"

ParentTaskId = Annotated[str, Field(pattern=PARENT_TASK_ID_PATTERN)]

_PARENT_TASK_ID = re.compile(PARENT_TASK_ID_PATTERN)
_SUBTASK_ID = re.compile(SUBTASK_ID_PATTERN)


class TaskType(StrEnum):
    PARENT = "parent"
    SUBTASK = "subtask"


class TaskPriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    WAITING = "waiting"
    DONE = "done"
    CANCELLED = "cancelled"


class Task(EntityFrontmatter):
    """Canonical task frontmatter with single-level parent/subtask invariants."""

    id: str = Field(pattern=TASK_ID_PATTERN)
    entity_type: Literal["task"]
    task_type: TaskType
    parent_task: ParentTaskId | None = None
    root_task: ParentTaskId
    priority: TaskPriority
    status: TaskStatus
    owner: str | None = None
    requester: str | None = None
    waiting_on: list[str] = Field(default_factory=list)
    due_at: AwareDatetime | None = None
    next_action: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    source_observations: list[str] = Field(default_factory=list)

    @field_validator("waiting_on", "dependencies", "blockers", "source_observations")
    @classmethod
    def _require_unique_task_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("list values must be unique")
        return value

    @field_validator("due_at", mode="before")
    @classmethod
    def _reject_numeric_due_at(cls, value: object) -> object:
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("due_at must be an RFC 3339 string or datetime value")
        return value

    @model_validator(mode="after")
    def _require_consistent_hierarchy(self) -> Self:
        if self.task_type is TaskType.PARENT:
            if _PARENT_TASK_ID.fullmatch(self.id) is None:
                raise ValueError("parent task IDs must use TASK-YYYY-NNN")
            if self.parent_task is not None:
                raise ValueError("parent tasks cannot declare parent_task")
            if self.root_task != self.id:
                raise ValueError("parent tasks must be self-rooted")
            return self

        if _SUBTASK_ID.fullmatch(self.id) is None:
            raise ValueError("subtask IDs must use TASK-YYYY-NNN-STNN")
        expected_parent = self.id.rsplit("-ST", maxsplit=1)[0]
        if self.parent_task != expected_parent:
            raise ValueError("subtask parent_task must match its TASK-YYYY-NNN prefix")
        if self.root_task != expected_parent:
            raise ValueError("subtask root_task must match its parent task")
        return self


class TaskHierarchyError(ValueError):
    """Raised when a set of individually valid tasks has an invalid hierarchy."""


def validate_task_hierarchy(tasks: Iterable[Task]) -> None:
    """Validate task-parent existence and consistency across a task collection."""

    tasks_by_id: dict[str, Task] = {}
    for task in tasks:
        if task.id in tasks_by_id:
            raise TaskHierarchyError(f"Duplicate task ID: {task.id}")
        tasks_by_id[task.id] = task

    context_ids = {WorkctxUri.parse(task.uri).context_id for task in tasks_by_id.values()}
    if len(context_ids) > 1:
        raise TaskHierarchyError("Task hierarchy cannot span multiple context IDs")

    for task in tasks_by_id.values():
        if task.task_type is not TaskType.SUBTASK:
            continue
        if task.parent_task is None:
            raise TaskHierarchyError(f"Subtask {task.id} has no parent_task")
        parent = tasks_by_id.get(task.parent_task)
        if parent is None:
            raise TaskHierarchyError(
                f"Subtask {task.id} points to missing parent {task.parent_task}"
            )
        if parent.task_type is not TaskType.PARENT:
            raise TaskHierarchyError(f"Subtask {task.id} parent {parent.id} is not a parent task")
        if parent.root_task != task.root_task:
            raise TaskHierarchyError(
                f"Subtask {task.id} and parent {parent.id} have different roots"
            )
