"""Deterministic context gathering and transaction-only outbox persistence."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from workctx.adapters.filesystem import CanonicalStore, ContextZone
from workctx.adapters.sqlite import ClaimRecord, SQLiteProjection, TaskQuery, TaskRecord
from workctx.domain import EntityFrontmatter, WorkctxUri
from workctx.domain.transactions import (
    AgentActor,
    ApprovalRequirement,
    CreateOperation,
    EntityDocumentPayload,
    PathAbsentCondition,
    PathExistsCondition,
    PathHashCondition,
    TransactionCondition,
    TransactionOperation,
    TransactionProposal,
    UpdateOperation,
)
from workctx.drafting.errors import (
    DraftContextChangedError,
    DraftContextError,
    DraftInputError,
    DraftNotFoundError,
    DraftSecretError,
    DraftStateError,
)
from workctx.drafting.models import (
    DRAFT_ID_PATTERN,
    DraftPayload,
    DraftRecord,
    DraftSaveResult,
    PersonClaimSummary,
    RecentLedgerActivity,
    ReplyContext,
    WaitingOnTask,
    _DraftFrontmatter,
    draft_frontmatter_record,
)
from workctx.models.context import LocalMutationPolicy
from workctx.retrieval import PackBuildStatus, build_pack
from workctx.transactions import ApplyResult, apply, audit_summary, verify_ledger
from workctx.validation import contains_possible_secret

_DRAFT_ID = re.compile(DRAFT_ID_PATTERN)
_DRAFT_FILE = re.compile(r"^DRAFT-.+\.md$")
_MAX_DRAFT_SEQUENCE = 99


class _ApplyTransaction(Protocol):
    def __call__(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ) -> ApplyResult: ...


class DraftService:
    """Application service bound to one isolated Work Context root."""

    def __init__(
        self,
        context_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        transaction_apply: _ApplyTransaction = apply,
        projection_factory: Callable[[Path], SQLiteProjection] = SQLiteProjection,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._clock = clock or _utc_now
        self._transaction_apply = transaction_apply
        self._projection_factory = projection_factory

    @property
    def context_root(self) -> Path:
        return self._root

    def gather_reply_context(
        self,
        person_uri: str,
        *,
        task_uri: str | None = None,
    ) -> ReplyContext:
        """Gather one stable reply context without an LLM, clock, or network call."""

        person = self._local_uri(person_uri, entity_type="person")
        task = None if task_uri is None else self._local_uri(task_uri, entity_type="task")
        projection = self._projection_factory(self._root)

        for _attempt in range(2):
            ledger_before = audit_summary(self._root)
            metadata_before = projection.metadata()
            person_record = projection.get_entity_by_uri(person)
            if person_record is None or person_record.entity_type.value != "person":
                raise DraftNotFoundError("The intended recipient was not found.")

            selected_record = None if task is None else projection.get_task(task)
            if task is not None and selected_record is None:
                raise DraftNotFoundError("The related task was not found.")

            focal = str(task or person)
            pack_result = build_pack(projection, focal, include_history=True)
            if pack_result.status is not PackBuildStatus.BUILT or pack_result.pack is None:
                raise DraftNotFoundError("Reply context could not resolve its focal entity.")

            claims = projection.claims_for_subject(person)
            waiting = projection.query_tasks(TaskQuery(waiting_on=str(person)))
            metadata_after = projection.metadata()
            ledger_after = audit_summary(self._root)
            if _same_snapshot(
                metadata_before.source_fingerprint,
                metadata_after.source_fingerprint,
                ledger_before.head_hash,
                ledger_after.head_hash,
            ):
                return ReplyContext(
                    context_id=self._store.context_id,
                    person_uri=str(person),
                    task_uri=None if task is None else str(task),
                    context_pack=pack_result.pack,
                    person_claims=tuple(_claim_summary(claim) for claim in claims),
                    waiting_on_tasks=tuple(_task_summary(item) for item in waiting),
                    selected_task=(
                        None if selected_record is None else _task_summary(selected_record)
                    ),
                    recent_ledger_activity=_ledger_activity(ledger_after),
                )
        raise DraftContextChangedError()

    def save_draft(
        self,
        payload: DraftPayload | Mapping[str, object],
        *,
        approved: bool,
    ) -> DraftSaveResult:
        """Create or revise one unsent draft through the transaction engine only."""

        validated = DraftPayload.model_validate(payload)
        if any(contains_possible_secret(value) for value in _payload_strings(validated)):
            raise DraftSecretError()

        recipient = self._local_uri(validated.recipient_uri, entity_type="person")
        task = (
            None
            if validated.task_uri is None
            else self._local_uri(validated.task_uri, entity_type="task")
        )
        projection = self._projection_factory(self._root)
        if projection.get_entity_by_uri(recipient) is None:
            raise DraftNotFoundError("The intended recipient was not found.")
        if task is not None and projection.get_task(task) is None:
            raise DraftNotFoundError("The related task was not found.")

        now = _normalize_time(self._clock())
        draft_id = validated.draft_id or self._allocate_draft_id(validated.title, now)
        relative_path = _draft_path(draft_id)
        existing = self._read_draft(draft_id, required=False)
        created_at = now if existing is None else existing.created_at
        frontmatter = self._frontmatter(
            validated,
            draft_id=draft_id,
            created_at=created_at,
            updated_at=now,
        )
        document = EntityFrontmatter.model_validate(frontmatter.model_dump(mode="python"))
        entity_payload = EntityDocumentPayload(
            kind="entity",
            document=document,
            body=validated.body,
        )

        operation: Literal["created", "updated"]
        operations: list[TransactionOperation]
        preconditions: list[TransactionCondition]
        if existing is None:
            operation = "created"
            operations = [
                CreateOperation(op="create", target=relative_path, payload=entity_payload)
            ]
            preconditions = [PathAbsentCondition(kind="path_absent", path=relative_path)]
        else:
            operation = "updated"
            expected_hash = self._draft_content_hash(relative_path)
            operations = [
                UpdateOperation(
                    op="update",
                    target=relative_path,
                    payload=entity_payload,
                    expected_hash=expected_hash,
                )
            ]
            preconditions = [
                PathHashCondition(
                    kind="path_hash",
                    path=relative_path,
                    content_hash=expected_hash,
                )
            ]

        config = self._store.read_context_config()
        approval: ApprovalRequirement = (
            "required"
            if config.policies.local_mutations is LocalMutationPolicy.REVIEW_REQUIRED
            else "not_required"
        )
        proposal = TransactionProposal(
            schema_version=1,
            id=_proposal_id(now, draft_id),
            context_id=self._store.context_id,
            base_revision=verify_ledger(self._root).head_hash,
            actor=AgentActor(
                type="agent",
                id=validated.author_id,
                agent=validated.agent,
                model=validated.model,
            ),
            created_at=now,
            source_refs=list(validated.source_refs),
            operations=operations,
            preconditions=preconditions,
            postconditions=[PathExistsCondition(kind="path_exists", path=relative_path)],
            expected_views=["sqlite"],
            approval=approval,
        )
        receipt = self._transaction_apply(self._root, proposal, approved=approved)
        saved = self._read_draft(draft_id, required=True)
        if saved is None:  # pragma: no cover - required=True invariant
            raise DraftStateError()
        return DraftSaveResult(operation=operation, draft=saved, receipt=receipt)

    def list_drafts(self) -> tuple[DraftRecord, ...]:
        """List canonical drafts in stable ID order without consulting generated state."""

        outbox = self._store.resolve_path("05_outbox", zones=(ContextZone.OUTBOX,))
        records: list[DraftRecord] = []
        for path in sorted(outbox.iterdir(), key=lambda item: item.name):
            if not _DRAFT_FILE.fullmatch(path.name):
                continue
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise DraftStateError("A canonical draft path is not a regular file.")
            record = self._read_draft(path.stem, required=True)
            if record is not None:
                records.append(record)
        return tuple(records)

    def get_draft(self, draft: str) -> DraftRecord:
        """Return one canonical draft by DRAFT ID or local draft URI."""

        draft_id = self._draft_identifier(draft)
        record = self._read_draft(draft_id, required=True)
        if record is None:  # pragma: no cover - required=True invariant
            raise DraftNotFoundError()
        return record

    def _local_uri(self, value: str, *, entity_type: str) -> WorkctxUri:
        try:
            parsed = WorkctxUri.parse(value)
        except ValueError as exc:
            raise DraftInputError(f"A canonical {entity_type} URI is required.") from exc
        if parsed.context_id != self._store.context_id:
            raise DraftContextError()
        if parsed.entity_type != entity_type or str(parsed) != value:
            raise DraftInputError(f"A canonical {entity_type} URI is required.")
        return parsed

    def _draft_identifier(self, value: str) -> str:
        if value.startswith("workctx://"):
            parsed = self._local_uri(value, entity_type="draft")
            value = parsed.entity_id
        if _DRAFT_ID.fullmatch(value) is None:
            raise DraftInputError("A valid DRAFT identifier is required.")
        return value

    def _allocate_draft_id(self, title: str, timestamp: datetime) -> str:
        day = timestamp.astimezone(UTC).strftime("%Y%m%d")
        slug = _slug(title)
        used: set[int] = set()
        prefix = f"DRAFT-{day}-{slug}-"
        outbox = self._store.resolve_path("05_outbox", zones=(ContextZone.OUTBOX,))
        for path in outbox.iterdir():
            if path.name.startswith(prefix) and path.suffix == ".md":
                match = _DRAFT_ID.fullmatch(path.stem)
                if match is not None:
                    used.add(int(path.stem.rsplit("-", maxsplit=1)[1]))
        for sequence in range(1, _MAX_DRAFT_SEQUENCE + 1):
            if sequence not in used:
                return f"{prefix}{sequence:02d}"
        raise DraftStateError("The daily draft identifier sequence is exhausted.")

    def _frontmatter(
        self,
        payload: DraftPayload,
        *,
        draft_id: str,
        created_at: datetime,
        updated_at: datetime,
    ) -> _DraftFrontmatter:
        references: list[dict[str, Any]] = [
            {
                "relation": "mentions",
                "target": payload.recipient_uri,
                "confidence": "high",
            }
        ]
        if payload.task_uri is not None:
            references.append(
                {
                    "relation": "related_to",
                    "target": payload.task_uri,
                    "confidence": "high",
                }
            )
        return _DraftFrontmatter.model_validate(
            {
                "schema_version": 1,
                "id": draft_id,
                "entity_type": "draft",
                "title": payload.title,
                "uri": f"workctx://{self._store.context_id}/draft/{draft_id}",
                "aliases": [],
                "status": "draft",
                "confidence": "high",
                "tags": ["outbox", "unsent"],
                "references": references,
                "created_at": created_at,
                "updated_at": updated_at,
                "delivery_state": "unsent",
                "recipient_uri": payload.recipient_uri,
                "purpose": payload.purpose,
                "draft_format": payload.format,
                "task_uri": payload.task_uri,
                "source_refs": list(payload.source_refs),
            }
        )

    def _read_draft(self, draft_id: str, *, required: bool) -> DraftRecord | None:
        relative_path = _draft_path(draft_id)
        path = self._store.resolve_path(relative_path, zones=(ContextZone.OUTBOX,))
        if not path.exists():
            if required:
                raise DraftNotFoundError()
            return None
        try:
            document = self._store.read_entity(relative_path)
            record = draft_frontmatter_record(
                document.frontmatter,
                body=document.body,
                path=relative_path,
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise DraftStateError() from exc
        if record.id != draft_id:
            raise DraftStateError()
        return record

    def _draft_content_hash(self, relative_path: str) -> str:
        path = self._store.resolve_path(relative_path, zones=(ContextZone.OUTBOX,))
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise DraftStateError("A canonical draft path is not a regular file.")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise DraftStateError("A canonical draft could not be read safely.") from exc
        return f"sha256:{hashlib.sha256(content).hexdigest()}"


def gather_reply_context(
    root: Path,
    person_uri: str,
    *,
    task_uri: str | None = None,
) -> ReplyContext:
    """Gather a deterministic bounded context for one reply."""

    return DraftService(root).gather_reply_context(person_uri, task_uri=task_uri)


def save_draft(
    root: Path,
    payload: DraftPayload | Mapping[str, object],
    *,
    approved: bool,
) -> DraftSaveResult:
    """Persist an unsent local draft through a validated transaction."""

    return DraftService(root).save_draft(payload, approved=approved)


def list_drafts(root: Path) -> tuple[DraftRecord, ...]:
    """List local canonical drafts in stable ID order."""

    return DraftService(root).list_drafts()


def get_draft(root: Path, draft: str) -> DraftRecord:
    """Get one local canonical draft by ID or URI."""

    return DraftService(root).get_draft(draft)


def _payload_strings(payload: DraftPayload) -> Iterator[str]:
    pending: list[object] = [payload.model_dump(mode="python")]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)


def _claim_summary(claim: ClaimRecord) -> PersonClaimSummary:
    return PersonClaimSummary(
        id=claim.id,
        uri=str(claim.uri),
        predicate=claim.predicate,
        object=claim.object,
        observed_at=claim.observed_at,
        status=claim.status,
        confidence=claim.confidence,
        source_observations=tuple(str(value) for value in claim.source_observations),
    )


def _task_summary(task: TaskRecord) -> WaitingOnTask:
    return WaitingOnTask(
        id=task.id,
        uri=str(task.uri),
        title=task.title,
        priority=task.priority,
        status=task.status,
        owner=task.owner,
        due_at=task.due_at,
        next_action=task.next_action,
        blockers=task.blockers,
        waiting_on=task.waiting_on,
    )


def _ledger_activity(summary: Any) -> RecentLedgerActivity:
    return RecentLedgerActivity(
        event_count=summary.event_count,
        head_revision=summary.head_hash,
        last_event_id=summary.last_event_id,
        last_proposal_id=summary.last_proposal_id,
        last_timestamp=summary.last_timestamp,
    )


def _same_snapshot(
    fingerprint_before: str,
    fingerprint_after: str,
    ledger_before: str,
    ledger_after: str,
) -> bool:
    return fingerprint_before == fingerprint_after and ledger_before == ledger_after


def _draft_path(draft_id: str) -> str:
    return f"05_outbox/{draft_id}.md"


def _slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = decomposed.encode("ascii", "ignore").decode("ascii").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (slug or "draft")[:60].rstrip("-") or "draft"


def _proposal_id(created_at: datetime, draft_id: str) -> str:
    slug = f"draft-save-{draft_id.lower()}-{secrets.token_hex(4)}"
    return f"TXP-{created_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{slug}"


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("draft clocks must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DraftService",
    "gather_reply_context",
    "get_draft",
    "list_drafts",
    "save_draft",
]
