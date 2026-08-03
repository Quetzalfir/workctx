"""Structured brief assembly and direct rebuilds of derived operational views."""

from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workctx.adapters.filesystem import (
    CanonicalStore,
    ContextLock,
    atomic_replace_bytes,
)
from workctx.adapters.sqlite import ClaimRecord, EntityRecord, SQLiteProjection, TaskRecord
from workctx.domain import ClaimStatus, TaskPriority, TaskStatus
from workctx.retrieval import resolve
from workctx.transactions import AuditSummary, audit_summary
from workctx.views.errors import ViewSourceChangedError
from workctx.views.models import (
    BriefPayload,
    GeneratedView,
    LedgerActivitySummary,
    StaleClaimItem,
    TaskViewItem,
    ViewName,
    ViewRebuildResult,
    WaitingOnGroup,
)
from workctx.views.rendering import render_view

DEFAULT_STALE_AFTER = timedelta(days=30)
_ACTIONABLE_STATUSES = frozenset(
    {TaskStatus.READY, TaskStatus.ACTIVE, TaskStatus.BLOCKED, TaskStatus.WAITING}
)
_PRIORITY_ORDER = {
    TaskPriority.P0: 0,
    TaskPriority.P1: 1,
    TaskPriority.P2: 2,
    TaskPriority.P3: 3,
    TaskPriority.P4: 4,
}


@dataclass(frozen=True, slots=True)
class _OperationalSnapshot:
    payload: BriefPayload
    next_actions: tuple[TaskViewItem, ...]


class ViewService:
    """Build operational views from typed, context-bound read APIs."""

    def __init__(
        self,
        context_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
        projection_factory: Callable[[Path], SQLiteProjection] = SQLiteProjection,
    ) -> None:
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        store = CanonicalStore(context_root)
        self._root = store.context_root
        self._context_id = store.context_id
        self._clock = clock or _utc_now
        self._stale_after = stale_after
        self._projection = projection_factory(self._root)

    @property
    def context_root(self) -> Path:
        return self._root

    def brief(self, *, generated_at: datetime | None = None) -> BriefPayload:
        """Return the structured doc-04 daily-brief payload without writing view files."""

        timestamp = _normalize_time(self._clock() if generated_at is None else generated_at)
        return self._snapshot(timestamp).payload

    def rebuild_views(
        self,
        *,
        generated_at: datetime | None = None,
        session_id: str | None = None,
    ) -> ViewRebuildResult:
        """Rebuild all five derived Markdown views from one consistent snapshot."""

        return self._rebuild(
            tuple(ViewName),
            generated_at=generated_at,
            session_id=session_id,
        )

    def rebuild_view(
        self,
        name: ViewName,
        *,
        generated_at: datetime | None = None,
        session_id: str | None = None,
    ) -> ViewRebuildResult:
        """Rebuild one named derived Markdown view."""

        return self._rebuild((name,), generated_at=generated_at, session_id=session_id)

    def _rebuild(
        self,
        names: tuple[ViewName, ...],
        *,
        generated_at: datetime | None,
        session_id: str | None,
    ) -> ViewRebuildResult:
        timestamp = _normalize_time(self._clock() if generated_at is None else generated_at)
        session = session_id or f"views-rebuild-{secrets.token_hex(8)}"
        with ContextLock.acquire(self._root, session_id=session) as lock:
            # 04_views is a disposable generated zone: a rebuild after the
            # whole directory was deleted must recreate it, because the
            # staging layer refuses to create parent directories itself.
            (self._root / "04_views").mkdir(exist_ok=True)
            snapshot = self._snapshot(timestamp)
            generated: list[GeneratedView] = []
            for name in names:
                content = render_view(
                    name,
                    snapshot.payload,
                    next_actions=snapshot.next_actions,
                )
                atomic_replace_bytes(
                    self._root,
                    name.relative_path,
                    content,
                    nonce=lock.nonce,
                    lock=lock,
                )
                generated.append(
                    GeneratedView(
                        name=name,
                        path=name.relative_path,
                        content_hash=_content_hash(content),
                    )
                )
        return ViewRebuildResult(
            context_id=self._context_id,
            generated_at=timestamp,
            source_revision=snapshot.payload.source_revision,
            views=tuple(generated),
        )

    def _snapshot(self, generated_at: datetime) -> _OperationalSnapshot:
        before = audit_summary(self._root)
        tasks = tuple(sorted(self._projection.query_tasks(), key=_task_sort_key))
        task_items = {task.id: _task_item(task) for task in tasks}
        next_actions = tuple(
            task_items[task.id] for task in tasks if task.status in _ACTIONABLE_STATUSES
        )
        today_focus = next_actions
        blockers = tuple(
            task_items[task.id]
            for task in tasks
            if task.status is TaskStatus.BLOCKED or task.blockers
        )
        waiting_on = self._waiting_on(tasks, task_items)
        stale_claims = self._stale_claims(tasks, generated_at)
        after = audit_summary(self._root)
        if before.head_hash != after.head_hash:
            raise ViewSourceChangedError()
        payload = BriefPayload(
            context_id=self._context_id,
            generated_at=generated_at,
            source_revision=after.head_hash,
            today_focus=today_focus,
            blockers=blockers,
            waiting_on=waiting_on,
            stale_claims=stale_claims,
            recent_ledger_activity=_ledger_activity(after),
        )
        return _OperationalSnapshot(payload=payload, next_actions=next_actions)

    def _waiting_on(
        self,
        tasks: tuple[TaskRecord, ...],
        task_items: dict[str, TaskViewItem],
    ) -> tuple[WaitingOnGroup, ...]:
        grouped: dict[str, list[TaskViewItem]] = defaultdict(list)
        for task in tasks:
            for value in task.waiting_on:
                grouped[value].append(task_items[task.id])

        results: list[WaitingOnGroup] = []
        for value in sorted(grouped, key=str.casefold):
            display_name, person_uri = self._waiting_party(value)
            results.append(
                WaitingOnGroup(
                    value=value,
                    display_name=display_name,
                    person_uri=person_uri,
                    tasks=tuple(sorted(grouped[value], key=_task_item_sort_key)),
                )
            )
        return tuple(
            sorted(results, key=lambda group: (group.display_name.casefold(), group.value))
        )

    def _waiting_party(self, value: str) -> tuple[str, str | None]:
        if not value.startswith("workctx://"):
            return value, None
        resolution = resolve(self._projection, value)
        record = resolution.record
        if isinstance(record, (EntityRecord, TaskRecord)):
            return record.title, value
        return value, value

    def _stale_claims(
        self,
        tasks: tuple[TaskRecord, ...],
        generated_at: datetime,
    ) -> tuple[StaleClaimItem, ...]:
        claims: dict[str, ClaimRecord] = {}
        for task in tasks:
            for claim in self._projection.claims_for_subject(
                task.uri,
                statuses=frozenset({ClaimStatus.CURRENT}),
            ):
                age = generated_at - claim.observed_at
                if age >= self._stale_after:
                    claims[claim.id] = claim
        return tuple(
            StaleClaimItem(
                id=claim.id,
                uri=str(claim.uri),
                subject=str(claim.subject),
                predicate=claim.predicate,
                object=claim.object,
                observed_at=claim.observed_at,
                age_days=(generated_at - claim.observed_at).days,
            )
            for claim in sorted(
                claims.values(),
                key=lambda item: (item.observed_at, item.id),
            )
        )


def brief(
    context_root: Path,
    *,
    clock: Callable[[], datetime] | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> BriefPayload:
    """Return one structured daily brief from the active projected context."""

    return ViewService(context_root, clock=clock, stale_after=stale_after).brief()


def rebuild_views(
    context_root: Path,
    *,
    clock: Callable[[], datetime] | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    session_id: str | None = None,
) -> ViewRebuildResult:
    """Rebuild every operational view directly as derived state."""

    return ViewService(context_root, clock=clock, stale_after=stale_after).rebuild_views(
        session_id=session_id
    )


def rebuild_view(
    context_root: Path,
    name: ViewName,
    *,
    clock: Callable[[], datetime] | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    session_id: str | None = None,
) -> ViewRebuildResult:
    """Rebuild one operational view directly as derived state."""

    return ViewService(context_root, clock=clock, stale_after=stale_after).rebuild_view(
        name,
        session_id=session_id,
    )


def _task_item(task: TaskRecord) -> TaskViewItem:
    return TaskViewItem(
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


def _task_sort_key(task: TaskRecord) -> tuple[int, datetime, str]:
    due = task.due_at or datetime.max.replace(tzinfo=UTC)
    return _PRIORITY_ORDER[task.priority], due, task.id


def _task_item_sort_key(task: TaskViewItem) -> tuple[int, datetime, str]:
    due = task.due_at or datetime.max.replace(tzinfo=UTC)
    return _PRIORITY_ORDER[task.priority], due, task.id


def _ledger_activity(summary: AuditSummary) -> LedgerActivitySummary:
    return LedgerActivitySummary(
        event_count=summary.event_count,
        head_revision=summary.head_hash,
        last_event_id=summary.last_event_id,
        last_proposal_id=summary.last_proposal_id,
        last_timestamp=summary.last_timestamp,
    )


def _content_hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("view clocks must return timezone-aware datetimes")
    return value.astimezone(UTC).replace(microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DEFAULT_STALE_AFTER",
    "ViewService",
    "brief",
    "rebuild_view",
    "rebuild_views",
]
