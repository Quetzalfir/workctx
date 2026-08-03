"""Typed payloads and records for deterministic local drafting."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from workctx.domain import (
    ClaimStatus,
    Confidence,
    EntityFrontmatter,
    PersonId,
    SubtaskId,
    TaskId,
    TaskPriority,
    TaskStatus,
)
from workctx.domain.references import WorkctxUri, validate_durable_reference
from workctx.retrieval import ContextPack
from workctx.transactions import ApplyResult

DRAFT_ID_PATTERN = r"^DRAFT-[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9]{2}$"


class DraftFormat(StrEnum):
    """Supported communication shapes; none implies a delivery transport."""

    CHAT = "chat"
    EMAIL = "email"
    STATUS_UPDATE = "status_update"
    DOCUMENTATION = "documentation"


def _single_line(value: str, label: str) -> str:
    if any(character in value for character in "\r\n") or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(f"{label} must be a printable single-line value")
    return value


def _canonical_typed_uri(value: str, entity_type: str) -> str:
    try:
        parsed = WorkctxUri.parse(value)
    except ValueError as exc:
        raise ValueError(f"value must be a canonical {entity_type} URI") from exc
    if parsed.entity_type != entity_type or str(parsed) != value:
        raise ValueError(f"value must be a canonical {entity_type} URI")
    try:
        if entity_type == "person":
            PersonId.parse(parsed.entity_id)
        elif entity_type == "task":
            try:
                TaskId.parse(parsed.entity_id)
            except ValueError:
                SubtaskId.parse(parsed.entity_id)
    except ValueError as exc:
        raise ValueError(f"value must be a canonical {entity_type} URI") from exc
    return value


class _DraftRecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DraftPayload(_DraftRecordModel):
    """Closed agent-authored payload accepted by :func:`save_draft`."""

    schema_version: Literal[1] = 1
    draft_id: str | None = Field(default=None, pattern=DRAFT_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    recipient_uri: str = Field(min_length=1, max_length=2000)
    purpose: str = Field(min_length=1, max_length=2000)
    format: DraftFormat
    body: str = Field(min_length=1, max_length=100_000)
    task_uri: str | None = Field(default=None, max_length=2000)
    source_refs: tuple[str, ...] = Field(default=(), max_length=100)
    author_id: str = Field(min_length=1, max_length=200)
    agent: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("title", "purpose", "author_id", "agent", "model")
    @classmethod
    def validate_single_line_fields(cls, value: str) -> str:
        return _single_line(value, "draft metadata")

    @field_validator("recipient_uri")
    @classmethod
    def validate_recipient_uri(cls, value: str) -> str:
        return _canonical_typed_uri(value, "person")

    @field_validator("task_uri")
    @classmethod
    def validate_task_uri(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_typed_uri(value, "task")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(validate_durable_reference(value) for value in values)
        if len(canonical) != len(set(canonical)):
            raise ValueError("source_refs must contain unique durable references")
        return canonical


class DraftRecord(_DraftRecordModel):
    """One validated canonical outbox document."""

    schema_version: Literal[1] = 1
    id: str = Field(pattern=DRAFT_ID_PATTERN)
    uri: str
    title: str
    status: Literal["draft"]
    delivery_state: Literal["unsent"]
    recipient_uri: str
    purpose: str
    format: DraftFormat
    task_uri: str | None
    source_refs: tuple[str, ...]
    body: str
    path: str
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        parsed = WorkctxUri.parse(self.uri)
        if parsed.entity_type != "draft" or parsed.entity_id != self.id:
            raise ValueError("draft URI identity must match its ID")
        return self


class DraftSaveResult(_DraftRecordModel):
    """Committed local persistence result; it is never a delivery receipt."""

    schema_version: Literal[1] = 1
    operation: Literal["created", "updated"]
    draft: DraftRecord
    receipt: ApplyResult


class PersonClaimSummary(_DraftRecordModel):
    id: str
    uri: str
    predicate: str
    object: JsonValue
    observed_at: AwareDatetime
    status: ClaimStatus
    confidence: Confidence
    source_observations: tuple[str, ...]


class WaitingOnTask(_DraftRecordModel):
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


class RecentLedgerActivity(_DraftRecordModel):
    event_count: int = Field(ge=0)
    head_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    last_event_id: str | None
    last_proposal_id: str | None
    last_timestamp: AwareDatetime | None


class ReplyContext(_DraftRecordModel):
    """Stable deterministic inputs for an agent that will author a reply."""

    schema_version: Literal[1] = 1
    context_id: str
    person_uri: str
    task_uri: str | None
    context_pack: ContextPack
    person_claims: tuple[PersonClaimSummary, ...]
    waiting_on_tasks: tuple[WaitingOnTask, ...]
    selected_task: WaitingOnTask | None
    recent_ledger_activity: RecentLedgerActivity


class _DraftFrontmatter(EntityFrontmatter):
    """Draft-specific refinement of the existing open entity contract."""

    entity_type: Literal["draft"]
    status: Literal["draft"]
    delivery_state: Literal["unsent"]
    recipient_uri: str
    purpose: str
    draft_format: DraftFormat
    task_uri: str | None
    source_refs: list[str]

    @field_validator("recipient_uri")
    @classmethod
    def validate_recipient_uri(cls, value: str) -> str:
        return _canonical_typed_uri(value, "person")

    @field_validator("task_uri")
    @classmethod
    def validate_task_uri(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_typed_uri(value, "task")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: list[str]) -> list[str]:
        canonical = [validate_durable_reference(value) for value in values]
        if len(canonical) != len(set(canonical)):
            raise ValueError("source_refs must contain unique durable references")
        return canonical


def draft_frontmatter_record(
    frontmatter: EntityFrontmatter,
    *,
    body: str,
    path: str,
) -> DraftRecord:
    """Refine generic entity metadata and expose one closed draft record."""

    refined = _DraftFrontmatter.model_validate(frontmatter.model_dump(mode="python"))
    return DraftRecord(
        id=refined.id,
        uri=refined.uri,
        title=refined.title,
        status=refined.status,
        delivery_state=refined.delivery_state,
        recipient_uri=refined.recipient_uri,
        purpose=refined.purpose,
        format=refined.draft_format,
        task_uri=refined.task_uri,
        source_refs=tuple(refined.source_refs),
        body=body,
        path=path,
        created_at=refined.created_at,
        updated_at=refined.updated_at,
    )


__all__ = [
    "DRAFT_ID_PATTERN",
    "DraftFormat",
    "DraftPayload",
    "DraftRecord",
    "DraftSaveResult",
    "PersonClaimSummary",
    "RecentLedgerActivity",
    "ReplyContext",
    "WaitingOnTask",
]
