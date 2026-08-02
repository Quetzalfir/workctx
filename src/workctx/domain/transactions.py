"""Typed transaction proposals and hash-chained audit events."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import quote

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from workctx.domain.artifacts import ArtifactManifest
from workctx.domain.claims import Claim
from workctx.domain.entities import EntityFrontmatter
from workctx.domain.observations import Observation
from workctx.domain.references import WorkctxUri, validate_durable_reference
from workctx.domain.relations import ContractDateTime
from workctx.domain.tasks import Task

TRANSACTION_PROPOSAL_ID_PATTERN = r"^TXP-[0-9]{8}T[0-9]{6}Z-[a-z0-9]+(?:-[a-z0-9]+)*$"
AUDIT_EVENT_ID_PATTERN = r"^AUD-[0-9]{8}T[0-9]{6}Z-[a-z0-9]+(?:-[a-z0-9]+)*$"
CONTEXT_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
CONTENT_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
REVISION_PATTERN = r"^[0-9a-f]{64}$"
ZERO_REVISION = "0" * 64

_TRANSACTION_PROPOSAL_ID = re.compile(
    r"^TXP-(?P<timestamp>[0-9]{8}T[0-9]{6}Z)-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_AUDIT_EVENT_ID = re.compile(
    r"^AUD-(?P<timestamp>[0-9]{8}T[0-9]{6}Z)-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_CANONICAL_ZONES = {
    "00_inbox",
    "01_processed",
    "02_knowledge",
    "03_work",
    "04_views",
    "05_outbox",
    "90_integrations",
    "99_meta",
}
_MARKDOWN_ZONES = {"02_knowledge", "03_work", "05_outbox"}
_SPECIAL_ENTITY_TYPES = {"artifact", "claim", "observation", "task"}
_LEDGER_PATH = "99_meta/audit/ledger.jsonl"
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:con|prn|aux|nul|clock\$|conin\$|conout\$|com[1-9]|lpt[1-9])(?:\..*)?$",
    re.IGNORECASE,
)

TransactionProposalId = Annotated[str, Field(pattern=TRANSACTION_PROPOSAL_ID_PATTERN)]
AuditEventId = Annotated[str, Field(pattern=AUDIT_EVENT_ID_PATTERN)]
ContextId = Annotated[
    str,
    Field(pattern=CONTEXT_ID_PATTERN, min_length=2, max_length=64),
]
ContentHash = Annotated[str, Field(pattern=CONTENT_HASH_PATTERN)]
Revision = Annotated[str, Field(pattern=REVISION_PATTERN)]
ExpectedView = Literal["sqlite"]
ApprovalRequirement = Literal["required", "not_required"]


def _validate_context_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Context paths must be non-empty strings")
    if _path_key(value) == _path_key(_LEDGER_PATH):
        raise ValueError("Transaction operations cannot address the audit ledger")
    if "\\" in value or "\x00" in value or any(ord(character) < 32 for character in value):
        raise ValueError("Context paths must be portable, printable POSIX paths")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("Context paths must use Unicode NFC")
    parts = value.split("/")
    if parts[0] not in _CANONICAL_ZONES:
        raise ValueError("Context paths must belong to a canonical workspace zone")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Context paths cannot contain empty or traversal segments")
    if any(
        ":" in part or part.endswith((".", " ")) or _WINDOWS_DEVICE_NAME.fullmatch(part) is not None
        for part in parts
    ):
        raise ValueError("Context paths must use Windows-portable path segments")
    return value


def _validate_generated_path(value: str) -> str:
    value = _validate_context_path(value)
    if not value.startswith("04_views/"):
        raise ValueError("delete_generated targets must belong to 04_views")
    return value


ContextPath = Annotated[str, AfterValidator(_validate_context_path)]
GeneratedPath = Annotated[str, AfterValidator(_validate_generated_path)]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ActorBase(_ContractModel):
    type: str
    id: str = Field(min_length=1, max_length=200)
    agent: str | None
    model: str | None


class HumanActor(_ActorBase):
    type: Literal["human"]
    agent: None
    model: None


class AgentActor(_ActorBase):
    type: Literal["agent"]
    agent: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)


class SystemActor(_ActorBase):
    type: Literal["system"]
    agent: None
    model: None


Actor = Annotated[HumanActor | AgentActor | SystemActor, Field(discriminator="type")]


class EntityDocumentPayload(_ContractModel):
    kind: Literal["entity"]
    document: EntityFrontmatter
    body: str

    @model_validator(mode="after")
    def reject_specialized_entity_kinds(self) -> Self:
        if str(self.document.entity_type) in _SPECIAL_ENTITY_TYPES:
            raise ValueError("Specialized entity types require their dedicated payload kind")
        return self


class TaskDocumentPayload(_ContractModel):
    kind: Literal["task"]
    document: Task
    body: str


class ClaimDocumentPayload(_ContractModel):
    kind: Literal["claim"]
    document: Claim
    body: str


class ObservationDocumentPayload(_ContractModel):
    kind: Literal["observation"]
    document: Observation
    body: str


class ArtifactManifestDocumentPayload(_ContractModel):
    kind: Literal["artifact_manifest"]
    document: ArtifactManifest


DocumentPayload = Annotated[
    EntityDocumentPayload
    | TaskDocumentPayload
    | ClaimDocumentPayload
    | ObservationDocumentPayload
    | ArtifactManifestDocumentPayload,
    Field(discriminator="kind"),
]


class CreateOperation(_ContractModel):
    op: Literal["create"]
    target: ContextPath
    payload: DocumentPayload

    @model_validator(mode="after")
    def validate_target_contract(self) -> Self:
        _validate_document_target(self.target, self.payload)
        return self


class UpdateOperation(_ContractModel):
    op: Literal["update"]
    target: ContextPath
    payload: DocumentPayload
    expected_hash: ContentHash

    @model_validator(mode="after")
    def validate_target_contract(self) -> Self:
        _validate_document_target(self.target, self.payload)
        return self


class MoveOperation(_ContractModel):
    op: Literal["move"]
    source: ContextPath
    destination: ContextPath
    expected_hash: ContentHash

    @model_validator(mode="after")
    def require_distinct_paths(self) -> Self:
        if _path_key(self.source) == _path_key(self.destination):
            raise ValueError("Move source and destination must be distinct paths")
        return self


class DeleteGeneratedOperation(_ContractModel):
    op: Literal["delete_generated"]
    target: GeneratedPath
    expected_hash: ContentHash


TransactionOperation = Annotated[
    CreateOperation | UpdateOperation | MoveOperation | DeleteGeneratedOperation,
    Field(discriminator="op"),
]


class PathExistsCondition(_ContractModel):
    kind: Literal["path_exists"]
    path: ContextPath


class PathAbsentCondition(_ContractModel):
    kind: Literal["path_absent"]
    path: ContextPath


class PathHashCondition(_ContractModel):
    kind: Literal["path_hash"]
    path: ContextPath
    content_hash: ContentHash


class ReferenceExistsCondition(_ContractModel):
    kind: Literal["reference_exists"]
    reference: str

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        return validate_durable_reference(value)


TransactionCondition = Annotated[
    PathExistsCondition | PathAbsentCondition | PathHashCondition | ReferenceExistsCondition,
    Field(discriminator="kind"),
]


class TransactionProposal(_ContractModel):
    """One fully typed, ordered canonical mutation proposal."""

    schema_version: int = Field(strict=True)
    id: TransactionProposalId
    context_id: ContextId
    base_revision: Revision
    actor: Actor
    created_at: ContractDateTime
    source_refs: list[str]
    operations: list[TransactionOperation] = Field(min_length=1)
    preconditions: list[TransactionCondition]
    postconditions: list[TransactionCondition]
    expected_views: list[ExpectedView] = Field(min_length=1, max_length=1)
    approval: ApprovalRequirement

    @field_validator("schema_version")
    @classmethod
    def require_schema_version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version must be 1")
        return value

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: list[str]) -> list[str]:
        canonical = [validate_durable_reference(value) for value in values]
        if len(canonical) != len(set(canonical)):
            raise ValueError("source_refs must contain unique durable references")
        return canonical

    @field_validator("preconditions", "postconditions")
    @classmethod
    def require_unique_conditions(
        cls,
        values: list[TransactionCondition],
    ) -> list[TransactionCondition]:
        serialized = [
            json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for value in values
        ]
        if len(serialized) != len(set(serialized)):
            raise ValueError("Transaction conditions must be unique")
        return values

    @field_validator("expected_views")
    @classmethod
    def require_unique_expected_views(cls, values: list[ExpectedView]) -> list[ExpectedView]:
        if len(values) != len(set(values)):
            raise ValueError("expected_views must contain unique values")
        return values

    @model_validator(mode="after")
    def validate_producer_invariants(self) -> Self:
        _require_proposal_time_matches_id(self.id, self.created_at)
        _require_operation_paths_do_not_collide(self.operations)
        for value in self.source_refs:
            _require_local_reference_context(value, self.context_id)
        for operation in self.operations:
            if isinstance(operation, (CreateOperation, UpdateOperation)):
                _require_payload_context(operation.payload, self.context_id)
                _require_target_filename_identity(operation.target, operation.payload)
        for condition in (*self.preconditions, *self.postconditions):
            if isinstance(condition, ReferenceExistsCondition):
                _require_local_reference_context(condition.reference, self.context_id)
        return self


class AuditCreateOperation(_ContractModel):
    op: Literal["create"]
    target: ContextPath
    postimage_hash: ContentHash


class AuditUpdateOperation(_ContractModel):
    op: Literal["update"]
    target: ContextPath
    preimage_hash: ContentHash
    postimage_hash: ContentHash


class AuditMoveOperation(_ContractModel):
    op: Literal["move"]
    source: ContextPath
    destination: ContextPath
    content_hash: ContentHash


class AuditDeleteGeneratedOperation(_ContractModel):
    op: Literal["delete_generated"]
    target: GeneratedPath
    preimage_hash: ContentHash


AuditOperation = Annotated[
    AuditCreateOperation
    | AuditUpdateOperation
    | AuditMoveOperation
    | AuditDeleteGeneratedOperation,
    Field(discriminator="op"),
]


class AuditEventContent(_ContractModel):
    """Audit event fields before the tamper-evident event hash is sealed."""

    schema_version: int = Field(strict=True)
    id: AuditEventId
    proposal_id: TransactionProposalId
    context_id: ContextId
    timestamp: ContractDateTime
    actor: Actor
    action: Literal["apply", "recovery"]
    result: Literal["committed", "rolled_back"]
    base_revision: Revision
    source_refs: list[str]
    operations: list[AuditOperation] = Field(min_length=1)
    prev_hash: Revision

    @field_validator("schema_version")
    @classmethod
    def require_schema_version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version must be 1")
        return value

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: list[str]) -> list[str]:
        canonical = [validate_durable_reference(value) for value in values]
        if len(canonical) != len(set(canonical)):
            raise ValueError("source_refs must contain unique durable references")
        return canonical

    @model_validator(mode="after")
    def validate_producer_invariants(self) -> Self:
        expected_id = f"AUD-{self.proposal_id.removeprefix('TXP-')}"
        if self.id != expected_id:
            raise ValueError("Audit event ID must derive from proposal_id")
        if self.base_revision != self.prev_hash:
            raise ValueError("Audit base_revision must equal prev_hash")
        if self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("Audit timestamps must use UTC")
        if self.action == "recovery":
            if self.result != "rolled_back":
                raise ValueError("Recovery audit events must record a rolled_back result")
            if not isinstance(self.actor, SystemActor) or self.actor.id != (
                "workctx-transaction-recovery"
            ):
                raise ValueError(
                    "Recovery audit events must use the reserved transaction-recovery actor"
                )
            if self.source_refs:
                raise ValueError("Recovery audit events cannot claim proposal source references")
        for value in self.source_refs:
            _require_local_reference_context(value, self.context_id)
        return self

    def expected_event_hash(self) -> str:
        data = self.model_dump(mode="json")
        data["event_hash"] = ""
        payload = json.dumps(
            data,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class AuditEvent(AuditEventContent):
    """One canonical ADR 0010 ledger event with a verified event hash."""

    event_hash: Revision

    @model_validator(mode="after")
    def require_valid_event_hash(self) -> Self:
        if self.event_hash != self.expected_event_hash():
            raise ValueError("event_hash does not match the canonical audit event")
        return self

    @classmethod
    def seal(cls, content: AuditEventContent) -> AuditEvent:
        data = content.model_dump(mode="json")
        data["event_hash"] = content.expected_event_hash()
        return cls.model_validate(data)

    def canonical_line_bytes(self) -> bytes:
        return (
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _validate_document_target(target: str, payload: DocumentPayload) -> None:
    path = PurePosixPath(target)
    suffix = path.suffix
    zone = path.parts[0]
    if isinstance(payload, ArtifactManifestDocumentPayload):
        if not target.startswith("00_inbox/manifests/") or suffix not in {
            ".json",
            ".yaml",
            ".yml",
        }:
            raise ValueError("Artifact manifests must target 00_inbox/manifests JSON or YAML")
        return
    if suffix != ".md":
        raise ValueError("Narrative transaction payloads must target Markdown files")
    if isinstance(payload, TaskDocumentPayload):
        if zone != "03_work":
            raise ValueError("Task payloads must target 03_work")
        return
    if zone not in _MARKDOWN_ZONES:
        raise ValueError("Narrative payloads must target a canonical Markdown zone")


def _payload_identifier(payload: DocumentPayload) -> str:
    return payload.document.id


def _require_target_filename_identity(target: str, payload: DocumentPayload) -> None:
    identifier = _payload_identifier(payload)
    stem = PurePosixPath(target).stem
    encoded = quote(identifier, safe="-._~")
    accepted = {identifier, encoded}
    if isinstance(payload, ArtifactManifestDocumentPayload):
        accepted.update({f"{identifier}.manifest", f"{encoded}.manifest"})
    if stem not in accepted:
        raise ValueError("Operation target filename must match the document ID")


def _require_proposal_time_matches_id(
    proposal_id: str,
    created_at: datetime,
) -> None:
    match = _TRANSACTION_PROPOSAL_ID.fullmatch(proposal_id)
    if match is None:  # pragma: no cover - constrained field invariant
        return
    identifier_time = datetime.strptime(match.group("timestamp"), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=UTC
    )
    if created_at.astimezone(UTC) != identifier_time:
        raise ValueError("Proposal ID timestamp must equal created_at in UTC")


def _require_operation_paths_do_not_collide(
    operations: list[TransactionOperation],
) -> None:
    seen: set[str] = set()
    for operation in operations:
        paths = (
            (operation.source, operation.destination)
            if isinstance(operation, MoveOperation)
            else (operation.target,)
        )
        for path in paths:
            key = _path_key(path)
            if key in seen:
                raise ValueError("Operation paths must be unique after normalization")
            seen.add(key)


def _require_local_reference_context(value: str, context_id: str) -> None:
    if not value.startswith("workctx://"):
        return
    WorkctxUri.parse(value).require_context(context_id)


def _require_payload_context(payload: DocumentPayload, context_id: str) -> None:
    document = payload.document
    if isinstance(document, (EntityFrontmatter, Task)):
        WorkctxUri.parse(document.uri).require_context(context_id)
        for reference in document.references:
            _require_local_reference_context(reference.target, context_id)
            for source in reference.source_observations or []:
                _require_local_reference_context(source, context_id)
        if isinstance(document, Task):
            for source in document.source_observations:
                _require_local_reference_context(source, context_id)
        return
    if isinstance(document, Claim):
        _require_local_reference_context(document.subject, context_id)
        for source in document.source_observations:
            _require_local_reference_context(source, context_id)
        return
    if isinstance(document, Observation):
        for source in document.derived_from:
            _require_local_reference_context(source, context_id)
        for related_reference in document.related:
            _require_local_reference_context(related_reference.target, context_id)
            for source in related_reference.source_observations or []:
                _require_local_reference_context(source, context_id)


__all__ = [
    "AUDIT_EVENT_ID_PATTERN",
    "CONTENT_HASH_PATTERN",
    "CONTEXT_ID_PATTERN",
    "REVISION_PATTERN",
    "TRANSACTION_PROPOSAL_ID_PATTERN",
    "ZERO_REVISION",
    "Actor",
    "AgentActor",
    "ApprovalRequirement",
    "ArtifactManifestDocumentPayload",
    "AuditCreateOperation",
    "AuditDeleteGeneratedOperation",
    "AuditEvent",
    "AuditEventContent",
    "AuditEventId",
    "AuditMoveOperation",
    "AuditOperation",
    "AuditUpdateOperation",
    "ClaimDocumentPayload",
    "ContentHash",
    "ContextId",
    "ContextPath",
    "CreateOperation",
    "DeleteGeneratedOperation",
    "DocumentPayload",
    "EntityDocumentPayload",
    "ExpectedView",
    "GeneratedPath",
    "HumanActor",
    "MoveOperation",
    "ObservationDocumentPayload",
    "PathAbsentCondition",
    "PathExistsCondition",
    "PathHashCondition",
    "ReferenceExistsCondition",
    "Revision",
    "SystemActor",
    "TaskDocumentPayload",
    "TransactionCondition",
    "TransactionOperation",
    "TransactionProposal",
    "TransactionProposalId",
    "UpdateOperation",
]
