"""Typed structured records for generated operational views."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from workctx.domain import TaskPriority, TaskStatus


class _ViewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ViewName(StrEnum):
    CURRENT_FOCUS = "current-focus"
    NEXT_ACTIONS = "next-actions"
    WAITING_ON = "waiting-on"
    STALE_KNOWLEDGE = "stale-knowledge"
    BRIEF = "brief"

    @property
    def relative_path(self) -> str:
        return f"04_views/{self.value}.md"


class TaskViewItem(_ViewRecord):
    id: str
    uri: str
    title: str
    priority: TaskPriority
    status: TaskStatus
    owner: str | None
    due_at: AwareDatetime | None
    next_action: str
    blockers: tuple[str, ...]
    waiting_on: tuple[str, ...]


class WaitingOnGroup(_ViewRecord):
    value: str
    display_name: str
    person_uri: str | None
    tasks: tuple[TaskViewItem, ...]


class StaleClaimItem(_ViewRecord):
    id: str
    uri: str
    subject: str
    predicate: str
    object: JsonValue
    observed_at: AwareDatetime
    age_days: int = Field(ge=0)


class LedgerActivitySummary(_ViewRecord):
    event_count: int = Field(ge=0)
    head_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    last_event_id: str | None
    last_proposal_id: str | None
    last_timestamp: AwareDatetime | None


class BriefPayload(_ViewRecord):
    """Structured payload consumed by the doc-04 ``brief`` command and MCP."""

    schema_version: Literal[1] = 1
    context_id: str
    generated_at: AwareDatetime
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    today_focus: tuple[TaskViewItem, ...]
    blockers: tuple[TaskViewItem, ...]
    waiting_on: tuple[WaitingOnGroup, ...]
    stale_claims: tuple[StaleClaimItem, ...]
    recent_ledger_activity: LedgerActivitySummary


class GeneratedView(_ViewRecord):
    name: ViewName
    path: str
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ViewRebuildResult(_ViewRecord):
    schema_version: Literal[1] = 1
    context_id: str
    generated_at: AwareDatetime
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    views: tuple[GeneratedView, ...]


__all__ = [
    "BriefPayload",
    "GeneratedView",
    "LedgerActivitySummary",
    "StaleClaimItem",
    "TaskViewItem",
    "ViewName",
    "ViewRebuildResult",
    "WaitingOnGroup",
]
