"""Structured brief assembly and direct rebuilds of derived operational views."""

from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workctx.adapters.filesystem import (
    CanonicalStore,
    ContextLock,
    atomic_replace_bytes,
)
from workctx.adapters.sqlite import ClaimRecord, EntityRecord, SQLiteProjection, TaskRecord
from workctx.domain import (
    ArtifactStatus,
    ClaimStatus,
    EntityFrontmatter,
    EntityType,
    TaskPriority,
    TaskStatus,
)
from workctx.domain.transactions import AuditCreateOperation, AuditEvent, AuditUpdateOperation
from workctx.ingestion import ArtifactRecord, list_inbox
from workctx.retrieval import resolve
from workctx.transactions import AuditSummary, audit_summary, read_audit_events
from workctx.views.errors import ViewSourceChangedError
from workctx.views.models import (
    BlockedWaitingTaskItem,
    BriefPayload,
    CommitmentTaskItem,
    CompletedTaskItem,
    GeneratedView,
    LedgerActivitySummary,
    ProcessedArtifactItem,
    ResourceAccess,
    ResourceDirectoryItem,
    ResourceDirectoryPayload,
    ResourceLinkItem,
    StaleClaimItem,
    StatusReportPayload,
    TaskTransitionItem,
    TaskViewItem,
    ViewName,
    ViewRebuildResult,
    WaitingOnGroup,
)
from workctx.views.rendering import render_view

DEFAULT_STALE_AFTER = timedelta(days=30)
_STATUS_REPORT_PERIOD = timedelta(days=7)
_RESOURCE_ENTITY_TYPES = frozenset({EntityType.SYSTEM, EntityType.SERVICE, EntityType.INTEGRATION})
_RESOURCE_ACCESS_ORDER = {
    ResourceAccess.PUBLIC: 0,
    ResourceAccess.SSO: 1,
    ResourceAccess.VPN: 2,
    ResourceAccess.OTHER: 3,
    ResourceAccess.UNGROUPED: 4,
}
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
    resource_directory: ResourceDirectoryPayload
    status_report: StatusReportPayload


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
        self._store = store
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
        """Rebuild all derived Markdown views from one consistent snapshot."""

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
                    resource_directory=snapshot.resource_directory,
                    status_report=snapshot.status_report,
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
        entities = self._projection.query_entities(entity_types=_RESOURCE_ENTITY_TYPES)
        events = read_audit_events(self._root, through=generated_at)
        artifacts = list_inbox(
            self._root,
            statuses=frozenset({ArtifactStatus.PROCESSED}),
        ).artifacts
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
        resource_directory = self._resource_directory(entities)
        status_report = self._status_report(tasks, events, artifacts, generated_at)
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
        return _OperationalSnapshot(
            payload=payload,
            next_actions=next_actions,
            resource_directory=resource_directory,
            status_report=status_report,
        )

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

    def _resource_directory(
        self,
        entities: tuple[EntityRecord, ...],
    ) -> ResourceDirectoryPayload:
        resources: list[ResourceDirectoryItem] = []
        for entity in entities:
            document = self._store.read_entity(entity.source_path)
            grouped: dict[ResourceAccess, list[ResourceLinkItem]] = defaultdict(list)
            for access, link in _resource_links(document.frontmatter):
                grouped[access].append(link)
            if not grouped:
                grouped[ResourceAccess.UNGROUPED] = []

            for access in sorted(grouped, key=_RESOURCE_ACCESS_ORDER.__getitem__):
                links = tuple(
                    sorted(
                        grouped[access],
                        key=lambda item: (
                            (item.label or "").casefold(),
                            item.url.casefold(),
                            item.url,
                        ),
                    )
                )
                resources.append(
                    ResourceDirectoryItem(
                        id=entity.id,
                        uri=str(entity.uri),
                        title=entity.title,
                        description=_first_body_line(document.body),
                        access=access,
                        links=links,
                    )
                )

        return ResourceDirectoryPayload(
            resources=tuple(
                sorted(
                    resources,
                    key=lambda item: (
                        _RESOURCE_ACCESS_ORDER[item.access],
                        item.title.casefold(),
                        item.id,
                    ),
                )
            )
        )

    def _status_report(
        self,
        tasks: tuple[TaskRecord, ...],
        events: tuple[AuditEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
        generated_at: datetime,
    ) -> StatusReportPayload:
        period_start = generated_at - _STATUS_REPORT_PERIOD
        period_events = tuple(
            event
            for event in events
            if event.result == "committed" and period_start <= event.timestamp <= generated_at
        )
        tasks_by_uri = {str(task.uri): task for task in tasks}
        status_claims_by_task: dict[str, tuple[ClaimRecord, ...]] = {}
        status_claims_by_id: dict[str, ClaimRecord] = {}
        status_claims_by_path: dict[str, ClaimRecord] = {}
        for task in tasks:
            claims = tuple(
                claim
                for claim in self._projection.claims_for_subject(task.uri)
                if claim.predicate == "status"
            )
            status_claims_by_task[str(task.uri)] = claims
            for claim in claims:
                status_claims_by_id[claim.id] = claim
                status_claims_by_path[claim.source_path] = claim

        completed_items: list[CompletedTaskItem] = []
        for task in tasks:
            if task.status is not TaskStatus.DONE:
                continue
            completed_at = _current_status_since(
                task,
                status_claims_by_task[str(task.uri)],
            )
            if period_start <= completed_at <= generated_at:
                completed_items.append(
                    CompletedTaskItem(
                        id=task.id,
                        uri=str(task.uri),
                        title=task.title,
                        completed_at=completed_at,
                    )
                )
        completed = tuple(sorted(completed_items, key=lambda item: (item.completed_at, item.id)))

        moved_items: list[TaskTransitionItem] = []
        for event in period_events:
            for operation in event.operations:
                if not isinstance(operation, AuditCreateOperation):
                    continue
                transition_claim = status_claims_by_path.get(operation.target)
                if transition_claim is None or transition_claim.supersedes is None:
                    continue
                previous_claim = status_claims_by_id.get(transition_claim.supersedes)
                transition_task = tasks_by_uri.get(str(transition_claim.subject))
                if previous_claim is None or transition_task is None:
                    continue
                from_status = _task_status(previous_claim.object)
                to_status = _task_status(transition_claim.object)
                if from_status is None or to_status is None:
                    continue
                moved_items.append(
                    TaskTransitionItem(
                        id=transition_task.id,
                        uri=str(transition_task.uri),
                        title=transition_task.title,
                        from_status=from_status,
                        to_status=to_status,
                        moved_at=event.timestamp,
                    )
                )
        moved = tuple(sorted(moved_items, key=lambda item: (item.moved_at, item.id)))

        blocked_waiting_items: list[BlockedWaitingTaskItem] = []
        for task in tasks:
            if task.status not in {TaskStatus.BLOCKED, TaskStatus.WAITING}:
                continue
            since = _current_status_since(task, status_claims_by_task[str(task.uri)])
            blocked_waiting_items.append(
                BlockedWaitingTaskItem(
                    id=task.id,
                    uri=str(task.uri),
                    title=task.title,
                    status=task.status,
                    since=since,
                    age_days=max(0, (generated_at - since).days),
                    next_action=task.next_action,
                )
            )
        blocked_waiting = tuple(
            sorted(
                blocked_waiting_items,
                key=lambda item: (
                    0 if item.status is TaskStatus.BLOCKED else 1,
                    item.since,
                    item.id,
                ),
            )
        )

        new_commitments = tuple(
            CommitmentTaskItem(
                id=task.id,
                uri=str(task.uri),
                title=task.title,
                created_at=task.created_at,
                due_at=task.due_at,
            )
            for task in sorted(
                (
                    task
                    for task in tasks
                    if task.due_at is not None and period_start <= task.created_at <= generated_at
                ),
                key=lambda item: (item.created_at, item.id),
            )
            if task.due_at is not None
        )

        artifacts_by_path = {artifact.manifest_path: artifact for artifact in artifacts}
        archived_at: dict[str, datetime] = {}
        for event in period_events:
            for operation in event.operations:
                if (
                    isinstance(operation, AuditUpdateOperation)
                    and operation.target in artifacts_by_path
                ):
                    archived_at.setdefault(operation.target, event.timestamp)
        evidence_processed = tuple(
            ProcessedArtifactItem(
                id=artifact.manifest.id,
                uri=artifact.reference,
                original_name=artifact.manifest.original_name,
                archived_at=archived_at[artifact.manifest_path],
            )
            for artifact in sorted(
                (artifact for artifact in artifacts if artifact.manifest_path in archived_at),
                key=lambda item: (archived_at[item.manifest_path], item.manifest.id),
            )
        )

        return StatusReportPayload(
            period_start=period_start,
            period_end=generated_at,
            completed=completed,
            moved=moved,
            blocked_waiting=blocked_waiting,
            new_commitments=new_commitments,
            evidence_processed=evidence_processed,
        )


def _resource_links(
    frontmatter: EntityFrontmatter,
) -> tuple[tuple[ResourceAccess, ResourceLinkItem], ...]:
    entries: list[tuple[ResourceAccess, ResourceLinkItem]] = []
    explicit_urls: set[str] = set()
    extra = frontmatter.model_extra or {}
    access_urls = extra.get("access_urls")
    if isinstance(access_urls, list):
        for value in access_urls:
            parsed = _access_url(value)
            if parsed is None:
                continue
            access, link = parsed
            entries.append((access, link))
            explicit_urls.add(link.url)

    for reference in frontmatter.references:
        target = reference.target.strip()
        if target and target not in explicit_urls:
            entries.append(
                (
                    ResourceAccess.UNGROUPED,
                    ResourceLinkItem(url=target, label=None),
                )
            )

    unique: list[tuple[ResourceAccess, ResourceLinkItem]] = []
    seen: set[tuple[ResourceAccess, str, str | None]] = set()
    for access, link in entries:
        key = access, link.url, link.label
        if key not in seen:
            unique.append((access, link))
            seen.add(key)
    return tuple(unique)


def _access_url(value: object) -> tuple[ResourceAccess, ResourceLinkItem] | None:
    if isinstance(value, str):
        url = value.strip()
        if not url:
            return None
        return ResourceAccess.UNGROUPED, ResourceLinkItem(url=url, label=None)
    if not isinstance(value, Mapping):
        return None
    raw_url = value.get("url")
    if not isinstance(raw_url, str) or not (url := raw_url.strip()):
        return None
    raw_label = value.get("label")
    label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else None
    return (
        _resource_access(value.get("access")),
        ResourceLinkItem(url=url, label=label),
    )


def _resource_access(value: object) -> ResourceAccess:
    if not isinstance(value, str) or not (normalized := value.strip().casefold()):
        return ResourceAccess.UNGROUPED
    try:
        return ResourceAccess(normalized)
    except ValueError:
        return ResourceAccess.OTHER


def _first_body_line(body: str) -> str:
    return next((line.strip() for line in body.splitlines() if line.strip()), "")


def _task_status(value: object) -> TaskStatus | None:
    if not isinstance(value, str):
        return None
    try:
        return TaskStatus(value)
    except ValueError:
        return None


def _current_status_since(task: TaskRecord, claims: tuple[ClaimRecord, ...]) -> datetime:
    current = tuple(
        claim
        for claim in claims
        if claim.status is ClaimStatus.CURRENT and _task_status(claim.object) is task.status
    )
    if not current:
        return task.updated_at
    return max(current, key=lambda claim: (claim.observed_at, claim.id)).observed_at


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
