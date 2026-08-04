"""Approved, transaction-only lifecycle operations for suggestion records."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from workctx.adapters.filesystem import (
    CanonicalStore,
    ContextZone,
    render_markdown_bytes,
)
from workctx.domain import EntityFrontmatter, WorkctxUri
from workctx.domain.transactions import (
    CreateOperation,
    DeleteGeneratedOperation,
    EntityDocumentPayload,
    MoveOperation,
    PathAbsentCondition,
    PathHashCondition,
    TransactionCondition,
    TransactionOperation,
    TransactionProposal,
    UpdateOperation,
)
from workctx.suggestions.errors import (
    SuggestionApprovalRequiredError,
    SuggestionContextError,
    SuggestionNotFoundError,
    SuggestionProposalError,
    SuggestionSequenceExhaustedError,
    SuggestionStateError,
)
from workctx.suggestions.models import (
    SUGGESTION_ID_PATTERN,
    SuggestionDocument,
    SuggestionMutationResult,
    SuggestionPayload,
    SuggestionRecord,
    SuggestionStatus,
)
from workctx.transactions import ApplyResult, validate_proposal, verify_ledger
from workctx.transactions.models import ProposalValidationResult

_SUGGESTION_ID = re.compile(SUGGESTION_ID_PATTERN)
_SUGGESTION_FILE = re.compile(r"^SUG-.+\.md$")
_MAX_SUGGESTION_SEQUENCE = 99
_SUGGESTIONS_DIRECTORY = "03_work/suggestions"


class _ApplyTransaction(Protocol):
    def __call__(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ) -> ApplyResult: ...


class _ValidateProposal(Protocol):
    def __call__(
        self,
        context_root: Path,
        proposal: TransactionProposal,
    ) -> ProposalValidationResult: ...


class SuggestionService:
    """Application service bound to one isolated Work Context root."""

    def __init__(
        self,
        context_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        transaction_apply: _ApplyTransaction | None = None,
        proposal_validator: _ValidateProposal = validate_proposal,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._clock = clock or _utc_now
        # The default apply shares this service's clock so record frontmatter
        # and the audit-ledger event carry one consistent timeline.
        self._transaction_apply = transaction_apply or self._clock_bound_apply
        self._proposal_validator = proposal_validator

    def _clock_bound_apply(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
    ) -> ApplyResult:
        from workctx.transactions import TransactionEngine

        return TransactionEngine(context_root, clock=self._clock).apply(
            proposal,
            approved=approved,
        )

    @property
    def context_root(self) -> Path:
        return self._root

    def create(
        self,
        payload: SuggestionPayload | Mapping[str, object],
        *,
        approved: bool,
    ) -> SuggestionMutationResult:
        """Create one open record, optionally superseding an open predecessor."""

        validated = SuggestionPayload.model_validate(payload)
        _require_approval(approved)
        if validated.proposal is not None:
            self._validate_embedded_proposal(validated.proposal)

        now = _normalize_time(self._clock())
        suggestion_id = validated.id or self._allocate_id(validated.rationale, now)
        if self._read(suggestion_id, required=False) is not None:
            raise SuggestionStateError("A suggestion with the requested identity already exists.")

        predecessor = (
            None
            if validated.supersedes is None
            else self._read(validated.supersedes, required=True)
        )
        if predecessor is not None:
            if predecessor.record.status is not SuggestionStatus.OPEN:
                raise SuggestionStateError("Only an open suggestion can be superseded.")
            if predecessor.record.type is not validated.type:
                raise SuggestionStateError("A replacement must preserve the suggestion type.")

        record = self._new_record(validated, suggestion_id=suggestion_id, created_at=now)
        relative_path = _suggestion_path(suggestion_id)
        entity_payload, postimage_hash = _record_payload(record, validated.body)
        operations: list[TransactionOperation] = [
            CreateOperation(op="create", target=relative_path, payload=entity_payload)
        ]
        preconditions: list[TransactionCondition] = [
            PathAbsentCondition(kind="path_absent", path=relative_path)
        ]
        postconditions: list[TransactionCondition] = [
            PathHashCondition(
                kind="path_hash",
                path=relative_path,
                content_hash=postimage_hash,
            )
        ]
        source_refs = list(validated.source_refs)

        if predecessor is not None:
            old_hash = self._content_hash(predecessor.path)
            updated_old = _updated_record(
                predecessor.record,
                status=SuggestionStatus.SUPERSEDED,
                superseded_by=suggestion_id,
                updated_at=now,
            )
            old_payload, old_postimage_hash = _record_payload(updated_old, predecessor.body)
            operations.append(
                UpdateOperation(
                    op="update",
                    target=predecessor.path,
                    payload=old_payload,
                    expected_hash=old_hash,
                )
            )
            preconditions.append(
                PathHashCondition(
                    kind="path_hash",
                    path=predecessor.path,
                    content_hash=old_hash,
                )
            )
            postconditions.append(
                PathHashCondition(
                    kind="path_hash",
                    path=predecessor.path,
                    content_hash=old_postimage_hash,
                )
            )
            source_refs.append(predecessor.record.uri)

        proposal = self._lifecycle_proposal(
            action="create",
            suggestion_id=suggestion_id,
            actor=validated.actor,
            created_at=now,
            source_refs=source_refs,
            operations=operations,
            preconditions=preconditions,
            postconditions=postconditions,
        )
        self._ensure_directory()
        receipt = self._transaction_apply(self._root, proposal, approved=approved)
        saved = self._read(suggestion_id, required=True)
        if saved is None:  # pragma: no cover - required=True invariant
            raise SuggestionStateError()
        return SuggestionMutationResult(
            operation="created",
            suggestion=saved,
            superseded_id=None if predecessor is None else predecessor.record.id,
            receipt=receipt,
        )

    def adopt(self, suggestion: str, *, approved: bool) -> SuggestionMutationResult:
        """Adopt one open suggestion through exactly one transaction apply."""

        return self._transition(suggestion, status=SuggestionStatus.ADOPTED, approved=approved)

    def reject(self, suggestion: str, *, approved: bool) -> SuggestionMutationResult:
        """Reject one open suggestion without deleting its canonical history."""

        return self._transition(suggestion, status=SuggestionStatus.REJECTED, approved=approved)

    def list(
        self,
        *,
        statuses: Collection[SuggestionStatus] | None = None,
    ) -> tuple[SuggestionDocument, ...]:
        """List canonical suggestion documents in deterministic age/ID order."""

        directory = self._root / Path(_SUGGESTIONS_DIRECTORY)
        if not directory.exists():
            return ()
        if _is_link(directory) or not directory.is_dir():
            raise SuggestionStateError("The canonical suggestions directory is unsafe.")
        selected = None if statuses is None else frozenset(statuses)
        records: list[SuggestionDocument] = []
        try:
            paths = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise SuggestionStateError("Canonical suggestion records could not be listed.") from exc
        for path in paths:
            if not _SUGGESTION_FILE.fullmatch(path.name):
                continue
            if _is_link(path) or not path.is_file():
                raise SuggestionStateError("A canonical suggestion path is not a regular file.")
            document = self._read(path.stem, required=True)
            if document is not None and (selected is None or document.record.status in selected):
                records.append(document)
        return tuple(sorted(records, key=lambda item: (item.record.created_at, item.record.id)))

    def get(self, suggestion: str) -> SuggestionDocument:
        """Return one canonical suggestion by SUG ID or local investigation URI."""

        suggestion_id = self._identifier(suggestion)
        document = self._read(suggestion_id, required=True)
        if document is None:  # pragma: no cover - required=True invariant
            raise SuggestionNotFoundError()
        return document

    def _transition(
        self,
        suggestion: str,
        *,
        status: SuggestionStatus,
        approved: bool,
    ) -> SuggestionMutationResult:
        _require_approval(approved)
        current = self.get(suggestion)
        if current.record.status is not SuggestionStatus.OPEN:
            raise SuggestionStateError("Only an open suggestion can change lifecycle state.")

        now = _normalize_time(self._clock())
        updated = _updated_record(current.record, status=status, updated_at=now)
        expected_hash = self._content_hash(current.path)
        record_payload, postimage_hash = _record_payload(updated, current.body)
        record_update = UpdateOperation(
            op="update",
            target=current.path,
            payload=record_payload,
            expected_hash=expected_hash,
        )
        operations: list[TransactionOperation] = []
        preconditions: list[TransactionCondition] = []
        postconditions: list[TransactionCondition] = []
        source_refs = [current.record.uri]

        embedded = current.record.proposal if status is SuggestionStatus.ADOPTED else None
        if embedded is not None:
            self._validate_data_fix_scope(embedded)
            operations.extend(embedded.operations)
            preconditions.extend(embedded.preconditions)
            postconditions.extend(embedded.postconditions)
            source_refs.extend(embedded.source_refs)
        operations.append(record_update)
        preconditions.append(
            PathHashCondition(
                kind="path_hash",
                path=current.path,
                content_hash=expected_hash,
            )
        )
        postconditions.append(
            PathHashCondition(
                kind="path_hash",
                path=current.path,
                content_hash=postimage_hash,
            )
        )

        proposal = self._lifecycle_proposal(
            action=status.value,
            suggestion_id=current.record.id,
            actor=current.record.actor,
            created_at=now,
            source_refs=source_refs,
            operations=operations,
            preconditions=_unique_conditions(preconditions),
            postconditions=_unique_conditions(postconditions),
        )
        receipt = self._transaction_apply(self._root, proposal, approved=approved)
        saved = self._read(current.record.id, required=True)
        if saved is None:  # pragma: no cover - required=True invariant
            raise SuggestionStateError()
        return SuggestionMutationResult(
            operation="adopted" if status is SuggestionStatus.ADOPTED else "rejected",
            suggestion=saved,
            receipt=receipt,
        )

    def _new_record(
        self,
        payload: SuggestionPayload,
        *,
        suggestion_id: str,
        created_at: datetime,
    ) -> SuggestionRecord:
        uri = str(WorkctxUri(self._store.context_id, "investigation", suggestion_id))
        references: list[dict[str, object]] = [
            {
                "relation": "evidenced_by",
                "target": source_ref,
                "confidence": "high",
            }
            for source_ref in payload.source_refs
        ]
        if payload.supersedes is not None:
            references.append(
                {
                    "relation": "supersedes",
                    "target": str(
                        WorkctxUri(
                            self._store.context_id,
                            "investigation",
                            payload.supersedes,
                        )
                    ),
                    "confidence": "high",
                }
            )
        return SuggestionRecord.model_validate(
            {
                "schema_version": 1,
                "id": suggestion_id,
                "entity_type": "investigation",
                "title": _title(payload.rationale),
                "uri": uri,
                "aliases": [],
                "status": SuggestionStatus.OPEN,
                "confidence": "high",
                "tags": ["suggestion", payload.type.value],
                "references": references,
                "created_at": created_at,
                "updated_at": created_at,
                "type": payload.type,
                "rationale": payload.rationale,
                "signal": payload.signal,
                "source_refs": list(payload.source_refs),
                "proposal": payload.proposal,
                "actor": payload.actor,
                "supersedes": payload.supersedes,
                "superseded_by": None,
            }
        )

    def _lifecycle_proposal(
        self,
        *,
        action: str,
        suggestion_id: str,
        actor: object,
        created_at: datetime,
        source_refs: Sequence[str],
        operations: Sequence[TransactionOperation],
        preconditions: Sequence[TransactionCondition],
        postconditions: Sequence[TransactionCondition],
    ) -> TransactionProposal:
        return TransactionProposal.model_validate(
            {
                "schema_version": 1,
                "id": _proposal_id(created_at, action, suggestion_id),
                "context_id": self._store.context_id,
                "base_revision": verify_ledger(self._root).head_hash,
                "actor": actor,
                "created_at": created_at,
                "source_refs": _unique_strings(source_refs),
                "operations": list(operations),
                "preconditions": list(preconditions),
                "postconditions": list(postconditions),
                "expected_views": ["sqlite"],
                "approval": "required",
            }
        )

    def _validate_embedded_proposal(self, proposal: TransactionProposal) -> None:
        if proposal.context_id != self._store.context_id:
            raise SuggestionContextError()
        self._validate_data_fix_scope(proposal)
        validation = self._proposal_validator(self._root, proposal)
        if not validation.valid:
            raise SuggestionProposalError()

    def _validate_data_fix_scope(self, proposal: TransactionProposal) -> None:
        for operation in proposal.operations:
            for path in _operation_paths(operation):
                if path.casefold().startswith(f"{_SUGGESTIONS_DIRECTORY}/".casefold()):
                    raise SuggestionProposalError(
                        "Data-fix proposals cannot mutate suggestion lifecycle records."
                    )

    def _identifier(self, value: str) -> str:
        if value.startswith("workctx://"):
            try:
                parsed = WorkctxUri.parse(value)
            except ValueError as exc:
                raise SuggestionNotFoundError() from exc
            if parsed.context_id != self._store.context_id:
                raise SuggestionContextError()
            if parsed.entity_type != "investigation" or str(parsed) != value:
                raise SuggestionNotFoundError()
            value = parsed.entity_id
        if _SUGGESTION_ID.fullmatch(value) is None:
            raise SuggestionNotFoundError()
        return value

    def _read(self, suggestion_id: str, *, required: bool) -> SuggestionDocument | None:
        suggestion_id = self._identifier(suggestion_id)
        relative_path = _suggestion_path(suggestion_id)
        path = self._store.resolve_path(relative_path, zones=(ContextZone.WORK,))
        if not path.exists():
            if required:
                raise SuggestionNotFoundError()
            return None
        if _is_link(path) or not path.is_file():
            raise SuggestionStateError("A canonical suggestion path is not a regular file.")
        try:
            document = self._store.read_entity(relative_path)
            record = SuggestionRecord.model_validate(document.frontmatter.model_dump(mode="python"))
        except (OSError, ValueError, ValidationError) as exc:
            raise SuggestionStateError("A canonical suggestion record is invalid.") from exc
        if record.id != suggestion_id:
            raise SuggestionStateError("Suggestion identity does not match its canonical path.")
        return SuggestionDocument(record=record, body=document.body, path=relative_path)

    def _content_hash(self, relative_path: str) -> str:
        path = self._store.resolve_path(relative_path, zones=(ContextZone.WORK,))
        if _is_link(path) or not path.is_file():
            raise SuggestionStateError("A canonical suggestion path is not a regular file.")
        try:
            return _hash_bytes(path.read_bytes())
        except OSError as exc:
            raise SuggestionStateError("A canonical suggestion record could not be read.") from exc

    def _allocate_id(self, rationale: str, timestamp: datetime) -> str:
        day = timestamp.astimezone(UTC).strftime("%Y%m%d")
        slug = _slug(rationale)
        prefix = f"SUG-{day}-{slug}-"
        directory = self._root / Path(_SUGGESTIONS_DIRECTORY)
        used: set[int] = set()
        if directory.exists():
            if _is_link(directory) or not directory.is_dir():
                raise SuggestionStateError("The canonical suggestions directory is unsafe.")
            try:
                paths = tuple(directory.iterdir())
            except OSError as exc:
                raise SuggestionStateError(
                    "Canonical suggestion records could not be listed."
                ) from exc
            for path in paths:
                if path.name.startswith(prefix) and path.suffix == ".md":
                    match = _SUGGESTION_ID.fullmatch(path.stem)
                    if match is not None:
                        used.add(int(path.stem.rsplit("-", maxsplit=1)[1]))
        for sequence in range(1, _MAX_SUGGESTION_SEQUENCE + 1):
            if sequence not in used:
                return f"{prefix}{sequence:02d}"
        raise SuggestionSequenceExhaustedError(day, slug)

    def _ensure_directory(self) -> None:
        work = self._root / "03_work"
        if _is_link(work) or not work.is_dir():
            raise SuggestionStateError("The canonical work directory is unsafe.")
        directory = self._root / Path(_SUGGESTIONS_DIRECTORY)
        try:
            directory.mkdir(exist_ok=True)
        except OSError as exc:
            raise SuggestionStateError("The suggestions directory could not be created.") from exc
        if _is_link(directory) or not directory.is_dir():
            raise SuggestionStateError("The canonical suggestions directory is unsafe.")


def create_suggestion(
    root: Path,
    payload: SuggestionPayload | Mapping[str, object],
    *,
    approved: bool,
) -> SuggestionMutationResult:
    """Create one canonical open suggestion through an approved transaction."""

    return SuggestionService(root).create(payload, approved=approved)


def adopt_suggestion(
    root: Path,
    suggestion_id: str,
    *,
    approved: bool,
) -> SuggestionMutationResult:
    """Adopt one suggestion through one approved transaction."""

    return SuggestionService(root).adopt(suggestion_id, approved=approved)


def reject_suggestion(
    root: Path,
    suggestion_id: str,
    *,
    approved: bool,
) -> SuggestionMutationResult:
    """Reject one suggestion through an approved transaction."""

    return SuggestionService(root).reject(suggestion_id, approved=approved)


def list_suggestions(
    root: Path,
    *,
    statuses: Collection[SuggestionStatus] | None = None,
) -> tuple[SuggestionDocument, ...]:
    """List canonical suggestion records without consulting generated state."""

    return SuggestionService(root).list(statuses=statuses)


def get_suggestion(root: Path, suggestion: str) -> SuggestionDocument:
    """Get one canonical suggestion by ID or local URI."""

    return SuggestionService(root).get(suggestion)


def _record_payload(
    record: SuggestionRecord,
    body: str,
) -> tuple[EntityDocumentPayload, str]:
    # Suggestion-only fields become free-form EntityFrontmatter extras at the
    # unchanged transaction boundary, so nested proposal values must already
    # be JSON-native before canonical serialization preflight.
    generic = EntityFrontmatter.model_validate(record.model_dump(mode="json"))
    payload = EntityDocumentPayload(kind="entity", document=generic, body=body)
    return payload, _hash_bytes(render_markdown_bytes(generic, body))


def _updated_record(
    record: SuggestionRecord,
    *,
    status: SuggestionStatus,
    updated_at: datetime,
    superseded_by: str | None = None,
) -> SuggestionRecord:
    values = record.model_dump(mode="python")
    values.update(
        {
            "status": status,
            "updated_at": updated_at,
            "superseded_by": superseded_by,
        }
    )
    return SuggestionRecord.model_validate(values)


def _operation_paths(operation: TransactionOperation) -> tuple[str, ...]:
    if isinstance(operation, MoveOperation):
        return (operation.source, operation.destination)
    if isinstance(operation, (CreateOperation, UpdateOperation, DeleteGeneratedOperation)):
        return (operation.target,)
    raise TypeError("Unsupported transaction operation")  # pragma: no cover


def _unique_conditions(
    conditions: Sequence[TransactionCondition],
) -> list[TransactionCondition]:
    unique: list[TransactionCondition] = []
    seen: set[str] = set()
    for condition in conditions:
        key = json.dumps(condition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            unique.append(condition)
    return unique


def _unique_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _suggestion_path(suggestion_id: str) -> str:
    return f"{_SUGGESTIONS_DIRECTORY}/{suggestion_id}.md"


def _title(rationale: str) -> str:
    return rationale[:200].rstrip() or "Suggestion"


def _slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (slug or "suggestion")[:60].rstrip("-") or "suggestion"


def _proposal_id(created_at: datetime, action: str, suggestion_id: str) -> str:
    slug = f"suggestion-{action}-{suggestion_id.lower()}-{secrets.token_hex(4)}"
    return f"TXP-{created_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{slug}"


def _hash_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("suggestion clocks must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _require_approval(approved: bool) -> None:
    if approved is not True:
        raise SuggestionApprovalRequiredError()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "SuggestionService",
    "adopt_suggestion",
    "create_suggestion",
    "get_suggestion",
    "list_suggestions",
    "reject_suggestion",
]
