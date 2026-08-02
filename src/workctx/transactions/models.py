"""Typed application records returned by the transaction engine."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from workctx.domain.transactions import (
    AUDIT_EVENT_ID_PATTERN,
    CONTENT_HASH_PATTERN,
    CONTEXT_ID_PATTERN,
    REVISION_PATTERN,
    TRANSACTION_PROPOSAL_ID_PATTERN,
)

_DIAGNOSTIC_CODE_PATTERN = r"^[A-Z][A-Z0-9_-]*$"

ContentHashValue = Annotated[str, Field(pattern=CONTENT_HASH_PATTERN)]
RevisionValue = Annotated[str, Field(pattern=REVISION_PATTERN)]


def _single_line(value: str, label: str) -> str:
    if any(character in value for character in "\r\n") or any(
        ord(character) < 32 for character in value
    ):
        raise ValueError(f"{label} must be a single-line value without control characters")
    return value


class _TransactionRecord(BaseModel):
    """Closed, immutable base for JSON-facing transaction records."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    ADVISORY = "advisory"


class TransactionDiagnostic(_TransactionRecord):
    """Sanitized, location-only diagnostic returned by transaction APIs."""

    code: str = Field(
        min_length=1,
        max_length=64,
        pattern=_DIAGNOSTIC_CODE_PATTERN,
    )
    severity: DiagnosticSeverity
    message: str = Field(min_length=1, max_length=500)
    path: str | None = Field(default=None, max_length=500)
    repair_action: str | None = Field(default=None, max_length=500)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return _single_line(value, "diagnostic message")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return None if value is None else _single_line(value, "diagnostic path")

    @field_validator("repair_action")
    @classmethod
    def validate_repair_action(cls, value: str | None) -> str | None:
        return None if value is None else _single_line(value, "diagnostic repair action")


class OperationEffect(_TransactionRecord):
    """One deterministic filesystem effect described without applying it."""

    order: int = Field(ge=0)
    op: Literal["create", "update", "move", "delete_generated"]
    target: str = Field(min_length=1, max_length=1000)
    destination: str | None = Field(default=None, max_length=1000)
    preimage_hash: ContentHashValue | None = None
    postimage_hash: ContentHashValue | None = None
    hand_edits: bool

    @field_validator("op", "target")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _single_line(value, "operation effect value")

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, value: str | None) -> str | None:
        return None if value is None else _single_line(value, "operation destination")


class ProposalValidationResult(_TransactionRecord):
    proposal_id: str = Field(pattern=TRANSACTION_PROPOSAL_ID_PATTERN)
    context_id: str = Field(min_length=2, max_length=64, pattern=CONTEXT_ID_PATTERN)
    base_revision: RevisionValue
    valid: bool
    diagnostics: tuple[TransactionDiagnostic, ...] = ()


class DryRunResult(_TransactionRecord):
    proposal_id: str = Field(pattern=TRANSACTION_PROPOSAL_ID_PATTERN)
    context_id: str = Field(min_length=2, max_length=64, pattern=CONTEXT_ID_PATTERN)
    base_revision: RevisionValue
    valid: bool
    effects: tuple[OperationEffect, ...] = ()
    diagnostics: tuple[TransactionDiagnostic, ...] = ()


class ProjectionState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


class ProjectionStatus(_TransactionRecord):
    state: ProjectionState
    diagnostic_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=_DIAGNOSTIC_CODE_PATTERN,
    )
    repair_action: str | None = Field(default=None, max_length=500)
    invalidation_confirmed: bool | None = None
    skipped_documents: int = Field(default=0, ge=0)

    @field_validator("repair_action")
    @classmethod
    def validate_repair_action(cls, value: str | None) -> str | None:
        return None if value is None else _single_line(value, "projection repair action")


class ApplyResult(_TransactionRecord):
    """Receipt for a canonically committed transaction."""

    schema_version: Literal[1] = 1
    committed: Literal[True] = True
    proposal_id: str = Field(pattern=TRANSACTION_PROPOSAL_ID_PATTERN)
    context_id: str = Field(min_length=2, max_length=64, pattern=CONTEXT_ID_PATTERN)
    base_revision: RevisionValue
    committed_revision: RevisionValue
    applied_targets: tuple[str, ...]
    ledger_event_id: str = Field(pattern=AUDIT_EVENT_ID_PATTERN)
    ledger_event_hash: RevisionValue
    ledger_source_refs: tuple[str, ...]
    projection: ProjectionStatus

    @field_validator("applied_targets", "ledger_source_refs")
    @classmethod
    def validate_receipt_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value:
                raise ValueError("receipt values must not be empty")
            _single_line(value, "receipt value")
        return values


class RecoveryStrategy(StrEnum):
    COMPLETE = "complete"
    ROLLBACK = "rollback"


class RecoveryResult(_TransactionRecord):
    """Receipt for a completed or rolled-back interrupted transaction."""

    strategy: RecoveryStrategy
    outcome: Literal["committed", "rolled_back", "already_finalized"]
    transaction_id: str = Field(pattern=TRANSACTION_PROPOSAL_ID_PATTERN)
    committed_revision: RevisionValue | None = None
    applied_targets: tuple[str, ...] = ()
    ledger_event_id: str | None = Field(default=None, pattern=AUDIT_EVENT_ID_PATTERN)
    ledger_event_hash: RevisionValue | None = None
    projection: ProjectionStatus | None = None
    diagnostics: tuple[TransactionDiagnostic, ...] = ()

    @field_validator("transaction_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _single_line(value, "recovery value")

    @field_validator("applied_targets")
    @classmethod
    def validate_applied_targets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value:
                raise ValueError("applied targets must not be empty")
            _single_line(value, "applied target")
        return values


__all__ = [
    "ApplyResult",
    "DiagnosticSeverity",
    "DryRunResult",
    "OperationEffect",
    "ProjectionState",
    "ProjectionStatus",
    "ProposalValidationResult",
    "RecoveryResult",
    "RecoveryStrategy",
    "TransactionDiagnostic",
]
