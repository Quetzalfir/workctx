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
from workctx.domain.transactions import (
    AuditCreateOperation,
    AuditEvent,
    AuditMoveOperation,
    AuditUpdateOperation,
)
from workctx.ingestion import ArtifactRecord, list_inbox
from workctx.retrieval import resolve, trace
from workctx.transactions import AuditSummary, audit_summary, read_audit_events
from workctx.views.errors import ViewSourceChangedError
from workctx.views.models import (
    AgedTaskItem,
    AgendaPayload,
    BlockedWaitingTaskItem,
    BriefPayload,
    CommitmentTaskItem,
    CompletedTaskItem,
    DirectoryTaskItem,
    DueTaskItem,
    GeneratedView,
    GlossaryAliasItem,
    GlossaryOwnerItem,
    GlossaryPayload,
    LedgerActivitySummary,
    PeopleDirectoryItem,
    PeopleDirectoryPayload,
    ProcessedArtifactItem,
    ResourceAccess,
    ResourceDirectoryItem,
    ResourceDirectoryPayload,
    ResourceLinkItem,
    StaleClaimItem,
    StatusReportPayload,
    SuggestionItem,
    SuggestionsPayload,
    TaskTransitionItem,
    TaskViewItem,
    ViewName,
    ViewRebuildResult,
    WaitingAgendaItem,
    WaitingOnGroup,
)
from workctx.views.rendering import render_view

DEFAULT_STALE_AFTER = timedelta(days=30)
_STATUS_REPORT_PERIOD = timedelta(days=7)
_TASK_INACTIVITY_AFTER = timedelta(days=30)
_OLD_WAITING_AFTER = timedelta(days=14)
_RESOURCE_ENTITY_TYPES = frozenset({EntityType.SYSTEM, EntityType.SERVICE, EntityType.INTEGRATION})
_DIRECTORY_ENTITY_TYPES = frozenset({EntityType.PERSON, EntityType.TEAM})
_ORPHAN_EXCLUDED_TYPES = frozenset(
    {EntityType.TASK, EntityType.CLAIM, EntityType.OBSERVATION, EntityType.ARTIFACT}
)
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
_CURRENT_TASK_STATUSES = frozenset(
    {
        TaskStatus.BACKLOG,
        TaskStatus.READY,
        TaskStatus.ACTIVE,
        TaskStatus.BLOCKED,
        TaskStatus.WAITING,
    }
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
    people_directory: PeopleDirectoryPayload
    glossary: GlossaryPayload
    agenda: AgendaPayload
    suggestions: SuggestionsPayload


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
                    people_directory=snapshot.people_directory,
                    glossary=snapshot.glossary,
                    agenda=snapshot.agenda,
                    suggestions=snapshot.suggestions,
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
        entities = self._projection.query_entities()
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
        resource_directory = self._resource_directory(
            tuple(entity for entity in entities if entity.entity_type in _RESOURCE_ENTITY_TYPES)
        )
        status_report = self._status_report(tasks, events, artifacts, generated_at)
        people_directory = self._people_directory(entities, tasks)
        glossary = self._glossary(entities)
        agenda = self._agenda(tasks, generated_at)
        suggestions = self._suggestions(
            tasks,
            entities,
            events,
            stale_claims,
            agenda,
            generated_at,
        )
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
            people_directory=people_directory,
            glossary=glossary,
            agenda=agenda,
            suggestions=suggestions,
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

    def _people_directory(
        self,
        entities: tuple[EntityRecord, ...],
        tasks: tuple[TaskRecord, ...],
    ) -> PeopleDirectoryPayload:
        current_tasks = tuple(task for task in tasks if task.status in _CURRENT_TASK_STATUSES)
        tasks_by_uri = {str(task.uri): task for task in current_tasks}
        people: list[PeopleDirectoryItem] = []
        teams: list[PeopleDirectoryItem] = []

        for entity in entities:
            if entity.entity_type not in _DIRECTORY_ENTITY_TYPES:
                continue
            document = self._store.read_entity(entity.source_path)
            frontmatter = document.frontmatter
            extra = frontmatter.model_extra or {}
            uri = str(entity.uri)
            blocked = {task.id: task for task in current_tasks if uri in task.blockers}
            for reference in frontmatter.references:
                if reference.relation != "blocks":
                    continue
                task = tasks_by_uri.get(reference.target)
                if task is not None:
                    blocked[task.id] = task

            item = PeopleDirectoryItem(
                id=entity.id,
                uri=uri,
                title=entity.title,
                roles=_extra_values(extra, "role", "roles"),
                teams=self._team_values(frontmatter),
                channels=_channel_values(frontmatter),
                timezone=_extra_scalar(extra, "timezone"),
                owned_tasks=tuple(
                    _directory_task(task)
                    for task in sorted(
                        (task for task in current_tasks if task.owner == uri),
                        key=_task_title_sort_key,
                    )
                ),
                blocked_tasks=tuple(
                    _directory_task(task)
                    for task in sorted(blocked.values(), key=_task_title_sort_key)
                ),
                waiting_tasks=tuple(
                    _directory_task(task)
                    for task in sorted(
                        (task for task in current_tasks if uri in task.waiting_on),
                        key=_task_title_sort_key,
                    )
                ),
            )
            if entity.entity_type is EntityType.PERSON:
                people.append(item)
            else:
                teams.append(item)

        def item_key(item: PeopleDirectoryItem) -> tuple[str, str, str]:
            return item.title.casefold(), item.title, item.id

        return PeopleDirectoryPayload(
            people=tuple(sorted(people, key=item_key)),
            teams=tuple(sorted(teams, key=item_key)),
        )

    def _team_values(self, frontmatter: EntityFrontmatter) -> tuple[str, ...]:
        values = list(_extra_values(frontmatter.model_extra or {}, "team", "teams"))
        for reference in frontmatter.references:
            if not reference.target.startswith("workctx://"):
                continue
            resolution = resolve(self._projection, reference.target)
            record = resolution.record
            if isinstance(record, EntityRecord) and record.entity_type is EntityType.TEAM:
                values.append(f"{record.title} ({record.uri})")
        return _unique_sorted(values)

    def _glossary(self, entities: tuple[EntityRecord, ...]) -> GlossaryPayload:
        grouped: dict[str, list[GlossaryOwnerItem]] = defaultdict(list)
        for entity in entities:
            owner = GlossaryOwnerItem(
                id=entity.id,
                uri=str(entity.uri),
                title=entity.title,
                entity_type=entity.entity_type,
                definition=_first_body_line(entity.body),
            )
            for alias in entity.aliases:
                grouped[alias].append(owner)

        aliases: list[GlossaryAliasItem] = []
        for alias, entries in grouped.items():
            owners = {owner.id: owner for owner in entries}
            aliases.append(
                GlossaryAliasItem(
                    alias=alias,
                    owners=tuple(
                        sorted(
                            owners.values(),
                            key=lambda item: (
                                item.title.casefold(),
                                item.title,
                                item.entity_type.value,
                                item.id,
                            ),
                        )
                    ),
                )
            )
        return GlossaryPayload(
            aliases=tuple(sorted(aliases, key=lambda item: (item.alias.casefold(), item.alias)))
        )

    def _agenda(
        self,
        tasks: tuple[TaskRecord, ...],
        generated_at: datetime,
    ) -> AgendaPayload:
        due_tasks = tuple(
            DueTaskItem(
                id=task.id,
                uri=str(task.uri),
                title=task.title,
                status=task.status,
                due_at=task.due_at,
                overdue=(
                    task.due_at < generated_at
                    and task.status not in {TaskStatus.DONE, TaskStatus.CANCELLED}
                ),
            )
            for task in sorted(
                (task for task in tasks if task.due_at is not None),
                key=lambda item: (item.due_at, item.id),
            )
            if task.due_at is not None
        )

        waiting_items: list[WaitingAgendaItem] = []
        blocked_items: list[AgedTaskItem] = []
        for task in tasks:
            status_claims = tuple(
                claim
                for claim in self._projection.claims_for_subject(task.uri)
                if claim.predicate == "status"
            )
            status_since = _current_status_since(task, status_claims)
            waiting_since = status_since if task.status is TaskStatus.WAITING else task.updated_at
            for value in task.waiting_on:
                display_name, person_uri = self._waiting_party(value)
                waiting_items.append(
                    WaitingAgendaItem(
                        id=task.id,
                        uri=str(task.uri),
                        title=task.title,
                        waiting_on=value,
                        display_name=display_name,
                        person_uri=person_uri,
                        since=waiting_since,
                        age_days=max(0, (generated_at - waiting_since).days),
                    )
                )
            if task.status is TaskStatus.BLOCKED:
                blocked_items.append(
                    AgedTaskItem(
                        id=task.id,
                        uri=str(task.uri),
                        title=task.title,
                        since=status_since,
                        age_days=max(0, (generated_at - status_since).days),
                    )
                )

        return AgendaPayload(
            due_tasks=due_tasks,
            waiting_on=tuple(
                sorted(
                    waiting_items,
                    key=lambda item: (
                        item.since,
                        item.display_name.casefold(),
                        item.id,
                        item.waiting_on,
                    ),
                )
            ),
            blocked_tasks=tuple(sorted(blocked_items, key=lambda item: (item.since, item.id))),
        )

    def _suggestions(
        self,
        tasks: tuple[TaskRecord, ...],
        entities: tuple[EntityRecord, ...],
        events: tuple[AuditEvent, ...],
        stale_claims: tuple[StaleClaimItem, ...],
        agenda: AgendaPayload,
        generated_at: datetime,
    ) -> SuggestionsPayload:
        stale = tuple(
            SuggestionItem(
                id=claim.id,
                uri=claim.uri,
                title=f"{claim.predicate} for {claim.subject}",
                statement=(
                    f"This current claim is {claim.age_days} days old; review or supersede."
                ),
                signal=(
                    f"stale current claim reached the configured "
                    f"{self._stale_after / timedelta(days=1):g}-day horizon."
                ),
            )
            for claim in stale_claims
        )

        broken: list[SuggestionItem] = []
        inactive: list[SuggestionItem] = []
        for task in tasks:
            traced = trace(self._projection, task.uri)
            missing = {item.reference for item in traced.missing_observations}
            if task.source_observations and all(
                observation in missing for observation in task.source_observations
            ):
                broken.append(
                    SuggestionItem(
                        id=task.id,
                        uri=str(task.uri),
                        title=task.title,
                        statement=(
                            "Every canonical source observation is unresolved; "
                            "evidence link broken."
                        ),
                        signal="all task source_observations references failed retrieval trace.",
                    )
                )

            if task.status not in {TaskStatus.ACTIVE, TaskStatus.WAITING}:
                continue
            last_activity = self._last_task_activity(task, events)
            baseline = task.updated_at if last_activity is None else last_activity
            inactivity = generated_at - baseline
            if inactivity < _TASK_INACTIVITY_AFTER:
                continue
            age_days = max(0, inactivity.days)
            activity_fact = (
                f"No committed audit activity is recorded, and the task record is "
                f"{age_days} days old"
                if last_activity is None
                else f"No committed audit activity has occurred for {age_days} days"
            )
            inactive.append(
                SuggestionItem(
                    id=task.id,
                    uri=str(task.uri),
                    title=task.title,
                    statement=f"{activity_fact}; confirm still real, or close.",
                    signal="active/waiting task audit inactivity reached 30 days.",
                )
            )

        orphaned = self._orphaned_knowledge(tasks, entities)
        old_waiting = tuple(
            SuggestionItem(
                id=item.id,
                uri=item.uri,
                title=item.title,
                statement=(
                    f"This task has waited on {item.display_name} for {item.age_days} days; "
                    "chase or drop."
                ),
                signal="waiting-on age exceeded 14 days.",
            )
            for item in agenda.waiting_on
            if generated_at - item.since > _OLD_WAITING_AFTER
        )
        return SuggestionsPayload(
            stale_claims=stale,
            broken_evidence_links=tuple(sorted(broken, key=_suggestion_sort_key)),
            inactive_tasks=tuple(sorted(inactive, key=_suggestion_sort_key)),
            orphaned_knowledge=orphaned,
            old_waiting_on=old_waiting,
        )

    def _last_task_activity(
        self,
        task: TaskRecord,
        events: tuple[AuditEvent, ...],
    ) -> datetime | None:
        paths = {task.source_path}
        paths.update(claim.source_path for claim in self._projection.claims_for_subject(task.uri))
        uri = str(task.uri)
        timestamps = [
            event.timestamp
            for event in events
            if event.result == "committed"
            and (uri in event.source_refs or paths.intersection(_audit_event_paths(event)))
        ]
        return max(timestamps, default=None)

    def _orphaned_knowledge(
        self,
        tasks: tuple[TaskRecord, ...],
        entities: tuple[EntityRecord, ...],
    ) -> tuple[SuggestionItem, ...]:
        task_references = {
            value
            for task in tasks
            for value in (
                task.owner,
                task.requester,
                *task.waiting_on,
                *task.dependencies,
                *task.blockers,
            )
            if value is not None
        }
        suggestions: list[SuggestionItem] = []
        for entity in entities:
            if entity.entity_type in _ORPHAN_EXCLUDED_TYPES:
                continue
            uri = str(entity.uri)
            if uri in task_references:
                continue
            if self._projection.claims_for_subject(entity.uri):
                continue
            if self._projection.observations_for_parent(entity.uri):
                continue
            if self._projection.inbound_edges(entity.uri):
                continue
            if self._projection.outbound_edges(entity.uri):
                continue
            suggestions.append(
                SuggestionItem(
                    id=entity.id,
                    uri=uri,
                    title=entity.title,
                    statement=(
                        "No canonical task, claim, observation, or typed relation references "
                        "this entity; orphaned knowledge: connect or archive."
                    ),
                    signal="no structured canonical reference was found.",
                )
            )
        return tuple(sorted(suggestions, key=_suggestion_sort_key))

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


def _extra_values(extra: Mapping[str, object], *names: str) -> tuple[str, ...]:
    values: list[str] = []
    for name in names:
        values.extend(_display_values(extra.get(name)))
    return _unique_sorted(values)


def _channel_values(frontmatter: EntityFrontmatter) -> tuple[str, ...]:
    values = list(_extra_values(frontmatter.model_extra or {}, "channel", "channels"))
    for reference in frontmatter.references:
        if reference.relation not in {"mentions", "related_to"}:
            continue
        if reference.target.startswith(("workctx://", "artifact://", "repo://")):
            continue
        values.append(
            f"{reference.note}: {reference.target}" if reference.note else reference.target
        )
    return _unique_sorted(values)


def _display_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, (list, tuple)):
        return tuple(item for entry in value for item in _display_values(entry))
    if not isinstance(value, Mapping):
        return ()

    label = value.get("label")
    display_value = value.get("value")
    if isinstance(label, str) and isinstance(display_value, str):
        label = label.strip()
        display_value = display_value.strip()
        if label and display_value:
            return (f"{label}: {display_value}",)

    values: list[str] = []
    for key in sorted(value, key=lambda item: (str(item).casefold(), str(item))):
        for item in _display_values(value[key]):
            values.append(f"{key}: {item}")
    return tuple(values)


def _extra_scalar(extra: Mapping[str, object], name: str) -> str | None:
    value = extra.get(name)
    if not isinstance(value, str) or not (stripped := value.strip()):
        return None
    return stripped


def _unique_sorted(values: list[str]) -> tuple[str, ...]:
    unique = {value for value in values if value}
    return tuple(sorted(unique, key=lambda item: (item.casefold(), item)))


def _first_body_line(body: str) -> str:
    return next((line.strip() for line in body.splitlines() if line.strip()), "")


def _directory_task(task: TaskRecord) -> DirectoryTaskItem:
    return DirectoryTaskItem(id=task.id, uri=str(task.uri), title=task.title)


def _task_title_sort_key(task: TaskRecord) -> tuple[str, str, str]:
    return task.title.casefold(), task.title, task.id


def _suggestion_sort_key(item: SuggestionItem) -> tuple[str, str, str]:
    return item.title.casefold(), item.title, item.id


def _audit_event_paths(event: AuditEvent) -> frozenset[str]:
    paths: set[str] = set()
    for operation in event.operations:
        if isinstance(operation, (AuditCreateOperation, AuditUpdateOperation)):
            paths.add(operation.target)
        elif isinstance(operation, AuditMoveOperation):
            paths.update((operation.source, operation.destination))
    return frozenset(paths)


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
