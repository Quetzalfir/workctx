"""Evidence-backed task operations applied through canonical transactions."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import BaseModel

from workctx.adapters.filesystem import CanonicalStore, load_markdown_model
from workctx.adapters.sqlite import ClaimRecord, SQLiteProjection, TaskRecord
from workctx.domain import (
    Claim,
    ClaimStatus,
    Confidence,
    Task,
    TaskHierarchyError,
    TaskStatus,
    TaskType,
    validate_task_hierarchy,
)
from workctx.domain.transactions import (
    AgentActor,
    ApprovalRequirement,
    ClaimDocumentPayload,
    CreateOperation,
    HumanActor,
    SystemActor,
    TaskDocumentPayload,
    TransactionOperation,
    TransactionProposal,
    UpdateOperation,
)
from workctx.models.context import LocalMutationPolicy
from workctx.tasks.errors import (
    ClaimSequenceExhaustedError,
    TaskEvidenceRequiredError,
    TaskNotFoundError,
    TaskStateError,
)
from workctx.tasks.models import TaskMutationResult
from workctx.transactions import ApplyResult, apply, verify_ledger

type TaskActor = AgentActor | HumanActor | SystemActor

_CLAIM_FILE = re.compile(r"^CLM-(?P<year>[0-9]{4})-(?P<sequence>[0-9]{5})\.md$")
_MAX_CLAIM_SEQUENCE = 99_999


class _ApplyTransaction(Protocol):
    def __call__(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ) -> ApplyResult: ...


class TaskService:
    """Application service bound to one isolated Work Context root."""

    def __init__(
        self,
        context_root: Path,
        *,
        actor: TaskActor,
        clock: Callable[[], datetime] | None = None,
        transaction_apply: _ApplyTransaction = apply,
        projection_factory: Callable[[Path], SQLiteProjection] = SQLiteProjection,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._actor = actor
        self._clock = clock or _utc_now
        self._transaction_apply = transaction_apply
        self._projection = projection_factory(self._root)

    @property
    def context_root(self) -> Path:
        return self._root

    def create_task(
        self,
        task: Task,
        *,
        body: str = "",
        approved: bool = False,
        base_revision: str | None = None,
        session_id: str | None = None,
    ) -> TaskMutationResult:
        """Create one parent task or subtask with initial mutable-state claims."""

        observations = _require_observations(task.source_observations)
        now = _normalize_time(self._clock())
        self._projection.rebuild()
        if self._projection.get_task(task.id) is not None:
            raise TaskStateError("A task with the requested identity already exists.")
        self._validate_hierarchy(addition=task)

        claim_specs: list[tuple[str, object]] = [("status", task.status.value)]
        if task.owner is not None:
            claim_specs.append(("owner", task.owner))
        if task.due_at is not None:
            claim_specs.append(("due", _json_datetime(task.due_at)))
        claim_ids = self._allocate_claim_ids(now.year, len(claim_specs))

        operations: list[TransactionOperation] = [
            CreateOperation(
                op="create",
                target=_task_path(task.id),
                payload=TaskDocumentPayload(kind="task", document=task, body=body),
            )
        ]
        for claim_id, (predicate, value) in zip(claim_ids, claim_specs, strict=True):
            claim = _new_claim(
                claim_id=claim_id,
                task=task,
                predicate=predicate,
                value=value,
                observed_at=now,
                observations=observations,
                supersedes=None,
            )
            operations.append(_claim_create(claim, _claim_body(predicate, value)))

        return self._apply(
            task.id,
            claim_ids,
            action="create",
            observations=observations,
            operations=operations,
            created_at=now,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def create_subtask(
        self,
        task: Task,
        *,
        body: str = "",
        approved: bool = False,
        base_revision: str | None = None,
        session_id: str | None = None,
    ) -> TaskMutationResult:
        """Create a real subtask after validating its parent relationship."""

        if task.task_type is not TaskType.SUBTASK:
            raise TaskStateError("create_subtask requires a task whose task_type is subtask.")
        return self.create_task(
            task,
            body=body,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def transition_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        source_observations: Sequence[str],
        claim_id: str | None = None,
        approved: bool = False,
        base_revision: str | None = None,
        session_id: str | None = None,
    ) -> TaskMutationResult:
        """Transition task status and supersede its current status claim atomically."""

        return self._update_claimed_field(
            task_id,
            field_name="status",
            predicate="status",
            value=status,
            claim_value=status.value,
            action="status",
            source_observations=source_observations,
            claim_id=claim_id,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def update_owner(
        self,
        task_id: str,
        owner: str | None,
        *,
        source_observations: Sequence[str],
        claim_id: str | None = None,
        approved: bool = False,
        base_revision: str | None = None,
        session_id: str | None = None,
    ) -> TaskMutationResult:
        """Set or clear task ownership and preserve the superseded owner claim."""

        return self._update_claimed_field(
            task_id,
            field_name="owner",
            predicate="owner",
            value=owner,
            claim_value=owner,
            action="owner",
            source_observations=source_observations,
            claim_id=claim_id,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def update_due(
        self,
        task_id: str,
        due_at: datetime | None,
        *,
        source_observations: Sequence[str],
        claim_id: str | None = None,
        approved: bool = False,
        base_revision: str | None = None,
        session_id: str | None = None,
    ) -> TaskMutationResult:
        """Set or clear a task deadline and preserve its supersession history."""

        normalized_due = None if due_at is None else _normalize_time(due_at)
        claim_value = None if normalized_due is None else _json_datetime(normalized_due)
        return self._update_claimed_field(
            task_id,
            field_name="due_at",
            predicate="due",
            value=normalized_due,
            claim_value=claim_value,
            action="due",
            source_observations=source_observations,
            claim_id=claim_id,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def update_waiting_on(
        self,
        task_id: str,
        waiting_on: Sequence[str],
        *,
        source_observations: Sequence[str],
        approved: bool = False,
        base_revision: str | None = None,
        session_id: str | None = None,
    ) -> TaskMutationResult:
        """Replace the ordered waiting-on values through one task proposal."""

        return self._update_unclaimed_fields(
            task_id,
            updates={"waiting_on": list(waiting_on)},
            action="waiting-on",
            source_observations=source_observations,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def update_next_action(
        self,
        task_id: str,
        next_action: str,
        *,
        source_observations: Sequence[str],
        approved: bool = False,
        base_revision: str | None = None,
        session_id: str | None = None,
    ) -> TaskMutationResult:
        """Replace the next action through one task proposal."""

        return self._update_unclaimed_fields(
            task_id,
            updates={"next_action": next_action},
            action="next-action",
            source_observations=source_observations,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def _update_claimed_field(
        self,
        task_id: str,
        *,
        field_name: str,
        predicate: str,
        value: object,
        claim_value: object,
        action: str,
        source_observations: Sequence[str],
        claim_id: str | None,
        approved: bool,
        base_revision: str | None,
        session_id: str | None,
    ) -> TaskMutationResult:
        observations = _require_observations(source_observations)
        now = _normalize_time(self._clock())
        self._projection.rebuild()
        record, task, body, task_hash = self._task_document(task_id)
        if getattr(task, field_name) == value:
            raise TaskStateError("The requested task field already has that value.")

        updated_task = _updated_task(
            task,
            {field_name: value},
            observed_at=now,
            observations=observations,
        )
        self._validate_hierarchy(replacement=updated_task)
        allocated_id = claim_id or self._allocate_claim_ids(now.year, 1)[0]
        current = self._current_claim(record, predicate)

        operations: list[TransactionOperation] = [
            UpdateOperation(
                op="update",
                target=record.source_path,
                payload=TaskDocumentPayload(kind="task", document=updated_task, body=body),
                expected_hash=task_hash,
            )
        ]
        if current is not None:
            old_claim, old_body, old_hash = self._claim_document(current)
            superseded = _updated_claim(
                old_claim,
                {"status": ClaimStatus.SUPERSEDED, "superseded_by": allocated_id},
            )
            operations.append(
                UpdateOperation(
                    op="update",
                    target=current.source_path,
                    payload=ClaimDocumentPayload(
                        kind="claim",
                        document=superseded,
                        body=old_body,
                    ),
                    expected_hash=old_hash,
                )
            )
        new_claim = _new_claim(
            claim_id=allocated_id,
            task=updated_task,
            predicate=predicate,
            value=claim_value,
            observed_at=now,
            observations=observations,
            supersedes=None if current is None else current.id,
        )
        operations.append(_claim_create(new_claim, _claim_body(predicate, claim_value)))
        return self._apply(
            task_id,
            (allocated_id,),
            action=action,
            observations=observations,
            operations=operations,
            created_at=now,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def _update_unclaimed_fields(
        self,
        task_id: str,
        *,
        updates: dict[str, object],
        action: str,
        source_observations: Sequence[str],
        approved: bool,
        base_revision: str | None,
        session_id: str | None,
    ) -> TaskMutationResult:
        observations = _require_observations(source_observations)
        now = _normalize_time(self._clock())
        self._projection.rebuild()
        record, task, body, task_hash = self._task_document(task_id)
        if all(getattr(task, field_name) == value for field_name, value in updates.items()):
            raise TaskStateError("The requested task fields already have those values.")
        updated_task = _updated_task(
            task,
            updates,
            observed_at=now,
            observations=observations,
        )
        self._validate_hierarchy(replacement=updated_task)
        operation = UpdateOperation(
            op="update",
            target=record.source_path,
            payload=TaskDocumentPayload(kind="task", document=updated_task, body=body),
            expected_hash=task_hash,
        )
        return self._apply(
            task_id,
            (),
            action=action,
            observations=observations,
            operations=[operation],
            created_at=now,
            approved=approved,
            base_revision=base_revision,
            session_id=session_id,
        )

    def _task_document(self, task_id: str) -> tuple[TaskRecord, Task, str, str]:
        record = self._projection.get_task(task_id)
        if record is None:
            raise TaskNotFoundError()
        task, body, content_hash = self._read_markdown(record.source_path, Task)
        return record, task, body, content_hash

    def _claim_document(self, record: ClaimRecord) -> tuple[Claim, str, str]:
        return self._read_markdown(record.source_path, Claim)

    def _read_markdown[ModelT: BaseModel](
        self,
        relative_path: str,
        model_type: type[ModelT],
    ) -> tuple[ModelT, str, str]:
        path = _resolved_canonical_path(self._root, relative_path)
        content = path.read_bytes()
        document = load_markdown_model(content, model_type)
        return document.frontmatter, document.body, _content_hash(content)

    def _current_claim(self, task: TaskRecord, predicate: str) -> ClaimRecord | None:
        matches = tuple(
            claim
            for claim in self._projection.claims_for_subject(
                task.uri,
                statuses=frozenset({ClaimStatus.CURRENT}),
            )
            if claim.predicate == predicate
        )
        if len(matches) > 1:
            raise TaskStateError("Task state has more than one current claim for a predicate.")
        return matches[0] if matches else None

    def _validate_hierarchy(
        self,
        *,
        addition: Task | None = None,
        replacement: Task | None = None,
    ) -> None:
        tasks: list[Task] = []
        replaced = False
        for record in self._projection.query_tasks():
            task, _body, _content_hash_value = self._read_markdown(record.source_path, Task)
            if replacement is not None and task.id == replacement.id:
                tasks.append(replacement)
                replaced = True
            else:
                tasks.append(task)
        if replacement is not None and not replaced:
            raise TaskNotFoundError()
        if addition is not None:
            tasks.append(addition)
        try:
            validate_task_hierarchy(tasks)
        except TaskHierarchyError:
            raise

    def _allocate_claim_ids(self, year: int, count: int) -> tuple[str, ...]:
        if count == 0:
            return ()
        used: set[int] = set()
        knowledge = self._root / "02_knowledge"
        for path in knowledge.rglob(f"CLM-{year:04d}-*.md"):
            if not path.is_file() or path.is_symlink():
                continue
            match = _CLAIM_FILE.fullmatch(path.name)
            if match is not None and int(match.group("year")) == year:
                used.add(int(match.group("sequence")))
        start = max(used, default=0) + 1
        if start + count - 1 > _MAX_CLAIM_SEQUENCE:
            raise ClaimSequenceExhaustedError(year)
        return tuple(f"CLM-{year:04d}-{sequence:05d}" for sequence in range(start, start + count))

    def _apply(
        self,
        task_id: str,
        claim_ids: tuple[str, ...],
        *,
        action: str,
        observations: tuple[str, ...],
        operations: list[TransactionOperation],
        created_at: datetime,
        approved: bool,
        base_revision: str | None,
        session_id: str | None,
    ) -> TaskMutationResult:
        revision = verify_ledger(self._root).head_hash if base_revision is None else base_revision
        config = self._store.read_context_config()
        approval: ApprovalRequirement = (
            "required"
            if config.policies.local_mutations is LocalMutationPolicy.REVIEW_REQUIRED
            else "not_required"
        )
        proposal = TransactionProposal(
            schema_version=1,
            id=_proposal_id(created_at, action, task_id),
            context_id=self._store.context_id,
            base_revision=revision,
            actor=self._actor,
            created_at=created_at,
            source_refs=list(observations),
            operations=operations,
            preconditions=[],
            postconditions=[],
            expected_views=["sqlite"],
            approval=approval,
        )
        receipt = self._transaction_apply(
            self._root,
            proposal,
            approved=approved,
            session_id=session_id,
        )
        return TaskMutationResult(task_id=task_id, claim_ids=claim_ids, receipt=receipt)


def _new_claim(
    *,
    claim_id: str,
    task: Task,
    predicate: str,
    value: object,
    observed_at: datetime,
    observations: tuple[str, ...],
    supersedes: str | None,
) -> Claim:
    return Claim.model_validate(
        {
            "schema_version": 1,
            "id": claim_id,
            "subject": task.uri,
            "predicate": predicate,
            "object": value,
            "observed_at": observed_at,
            "valid_from": observed_at,
            "valid_to": None,
            "status": ClaimStatus.CURRENT,
            "supersedes": supersedes,
            "superseded_by": None,
            "confidence": Confidence.HIGH,
            "source_observations": list(observations),
        }
    )


def _updated_task(
    task: Task,
    updates: dict[str, object],
    *,
    observed_at: datetime,
    observations: tuple[str, ...],
) -> Task:
    values = task.model_dump(mode="python")
    values.update(updates)
    values["updated_at"] = observed_at
    values["source_observations"] = list(dict.fromkeys((*task.source_observations, *observations)))
    return Task.model_validate(values)


def _updated_claim(claim: Claim, updates: dict[str, object]) -> Claim:
    values = claim.model_dump(mode="python")
    values.update(updates)
    return Claim.model_validate(values)


def _claim_create(claim: Claim, body: str) -> CreateOperation:
    return CreateOperation(
        op="create",
        target=_claim_path(claim.id),
        payload=ClaimDocumentPayload(kind="claim", document=claim, body=body),
    )


def _require_observations(values: Sequence[str]) -> tuple[str, ...]:
    observations = tuple(dict.fromkeys(values))
    if not observations:
        raise TaskEvidenceRequiredError()
    return observations


def _resolved_canonical_path(root: Path, relative_path: str) -> Path:
    portable = PurePosixPath(relative_path)
    if portable.is_absolute() or any(part in {"", ".", ".."} for part in portable.parts):
        raise TaskStateError("A projected task or claim path is invalid.")
    path = root.joinpath(*portable.parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TaskStateError("A projected task or claim document is unavailable.") from exc
    if not resolved.is_relative_to(root) or path.is_symlink() or not path.is_file():
        raise TaskStateError("A projected task or claim path is unsafe.")
    return path


def _content_hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _task_path(task_id: str) -> str:
    return f"03_work/tasks/{task_id}.md"


def _claim_path(claim_id: str) -> str:
    return f"02_knowledge/claims/{claim_id}.md"


def _claim_body(predicate: str, value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return f"Task {predicate} recorded as {rendered}.\n"


def _proposal_id(created_at: datetime, action: str, task_id: str) -> str:
    slug = f"task-{action}-{task_id.lower()}-{secrets.token_hex(4)}"
    return f"TXP-{created_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{slug}"


def _json_datetime(value: datetime) -> str:
    return _normalize_time(value).isoformat().replace("+00:00", "Z")


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("task clocks and deadlines must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["TaskActor", "TaskService"]
