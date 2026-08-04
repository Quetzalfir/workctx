"""Typed contracts for canonical suggestion records and lifecycle results."""

from __future__ import annotations

import re
from datetime import UTC
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from workctx.domain import EntityFrontmatter, WorkctxUri
from workctx.domain.references import validate_durable_reference
from workctx.domain.transactions import Actor, TransactionProposal
from workctx.transactions import ApplyResult

SUGGESTION_ID_PATTERN = r"^SUG-[0-9]{8}-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9]{2}$"
_SUGGESTION_ID = re.compile(SUGGESTION_ID_PATTERN)


class SuggestionType(StrEnum):
    """The three explicitly separated C-202 improvement targets."""

    DATA_FIX = "data_fix"
    SKILL_OVERRIDE = "skill_override"
    ENGINE_PROPOSAL = "engine_proposal"


class SuggestionStatus(StrEnum):
    """Durable lifecycle states for one suggestion record."""

    OPEN = "open"
    ADOPTED = "adopted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


def _single_line(value: str, label: str) -> str:
    if any(character in value for character in "\r\n") or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(f"{label} must be a printable single-line value")
    return value


def _validate_source_refs(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    canonical = tuple(validate_durable_reference(value) for value in values)
    if len(canonical) != len(set(canonical)):
        raise ValueError("source_refs must contain unique durable references")
    return canonical


class _SuggestionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SuggestionPayload(_SuggestionModel):
    """Closed input accepted by :func:`create_suggestion`."""

    schema_version: Literal[1] = 1
    id: str | None = Field(default=None, pattern=SUGGESTION_ID_PATTERN)
    type: SuggestionType
    rationale: str = Field(min_length=1, max_length=1000)
    signal: str = Field(min_length=1, max_length=2000)
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=100)
    proposal: TransactionProposal | None = None
    actor: Actor
    supersedes: str | None = Field(default=None, pattern=SUGGESTION_ID_PATTERN)
    body: str = Field(default="", max_length=100_000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        return _single_line(value, "rationale")

    @field_validator("signal")
    @classmethod
    def validate_signal(cls, value: str) -> str:
        return _single_line(value, "signal")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_source_refs(values)

    @model_validator(mode="after")
    def validate_type_contract(self) -> Self:
        if self.type is SuggestionType.DATA_FIX:
            if self.proposal is None:
                raise ValueError("data_fix suggestions require a transaction proposal")
            if self.proposal.approval != "required":
                raise ValueError("embedded data-fix proposals must require approval")
            if self.proposal.actor != self.actor:
                raise ValueError("suggestion actor must match the embedded proposal actor")
        elif self.proposal is not None:
            raise ValueError("only data_fix suggestions may embed a transaction proposal")
        if self.id is not None and self.supersedes == self.id:
            raise ValueError("a suggestion cannot supersede itself")
        return self


class SuggestionRecord(EntityFrontmatter):
    """Closed frontmatter contract for one canonical suggestion document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=SUGGESTION_ID_PATTERN)
    entity_type: Literal["investigation"]
    title: str = Field(min_length=1, max_length=200)
    status: SuggestionStatus
    type: SuggestionType
    rationale: str = Field(min_length=1, max_length=1000)
    signal: str = Field(min_length=1, max_length=2000)
    source_refs: list[str] = Field(min_length=1, max_length=100)
    proposal: TransactionProposal | None
    actor: Actor
    supersedes: str | None = Field(pattern=SUGGESTION_ID_PATTERN)
    superseded_by: str | None = Field(pattern=SUGGESTION_ID_PATTERN)

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        return _single_line(value, "rationale")

    @field_validator("signal")
    @classmethod
    def validate_signal(cls, value: str) -> str:
        return _single_line(value, "signal")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: list[str]) -> list[str]:
        return list(_validate_source_refs(values))

    @model_validator(mode="after")
    def validate_suggestion_contract(self) -> Self:
        match = _SUGGESTION_ID.fullmatch(self.id)
        if match is None:  # pragma: no cover - constrained field invariant
            return self
        created_day = self.created_at.astimezone(UTC).strftime("%Y%m%d")
        if self.id[4:12] != created_day:
            raise ValueError("suggestion ID date must match created_at in UTC")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

        parsed = WorkctxUri.parse(self.uri)
        if self.proposal is not None and self.proposal.context_id != parsed.context_id:
            raise ValueError("embedded proposal context must match the suggestion context")
        if self.type is SuggestionType.DATA_FIX:
            if self.proposal is None:
                raise ValueError("data_fix suggestions require a transaction proposal")
            if self.proposal.approval != "required":
                raise ValueError("embedded data-fix proposals must require approval")
            if self.proposal.actor != self.actor:
                raise ValueError("suggestion actor must match the embedded proposal actor")
        elif self.proposal is not None:
            raise ValueError("only data_fix suggestions may embed a transaction proposal")

        if self.status is SuggestionStatus.SUPERSEDED:
            if self.superseded_by is None:
                raise ValueError("superseded suggestions require superseded_by")
        elif self.superseded_by is not None:
            raise ValueError("only superseded suggestions may set superseded_by")
        if self.supersedes == self.id or self.superseded_by == self.id:
            raise ValueError("suggestion supersession links cannot be self-referential")
        return self


class SuggestionDocument(_SuggestionModel):
    """One validated canonical record together with its Markdown body and path."""

    record: SuggestionRecord
    body: str
    path: str


class SuggestionMutationResult(_SuggestionModel):
    """One committed suggestion lifecycle mutation."""

    schema_version: Literal[1] = 1
    operation: Literal["created", "adopted", "rejected"]
    suggestion: SuggestionDocument
    superseded_id: str | None = None
    receipt: ApplyResult


__all__ = [
    "SUGGESTION_ID_PATTERN",
    "SuggestionDocument",
    "SuggestionMutationResult",
    "SuggestionPayload",
    "SuggestionRecord",
    "SuggestionStatus",
    "SuggestionType",
]
