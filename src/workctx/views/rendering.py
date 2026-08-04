"""Byte-deterministic Markdown rendering for operational views."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from workctx.validation.engine import contains_possible_secret
from workctx.views.models import (
    AgendaPayload,
    BriefPayload,
    DirectoryTaskItem,
    GlossaryPayload,
    PeopleDirectoryItem,
    PeopleDirectoryPayload,
    ResourceAccess,
    ResourceDirectoryPayload,
    ResourceLinkItem,
    StaleClaimItem,
    StatusReportPayload,
    SuggestionItem,
    SuggestionRecordItem,
    SuggestionsPayload,
    TaskViewItem,
    ViewName,
    WaitingOnGroup,
)

GENERATOR_NAME = "workctx.views"


def render_view(
    name: ViewName,
    payload: BriefPayload,
    *,
    next_actions: Sequence[TaskViewItem],
    resource_directory: ResourceDirectoryPayload,
    status_report: StatusReportPayload,
    people_directory: PeopleDirectoryPayload,
    glossary: GlossaryPayload,
    agenda: AgendaPayload,
    suggestions: SuggestionsPayload,
) -> bytes:
    """Render one generated file from an already consistent structured snapshot."""

    sections = {
        ViewName.CURRENT_FOCUS: _current_focus(payload.today_focus),
        ViewName.NEXT_ACTIONS: _next_actions(next_actions),
        ViewName.WAITING_ON: _waiting_on(payload.waiting_on),
        ViewName.STALE_KNOWLEDGE: _stale_knowledge(payload.stale_claims),
        ViewName.BRIEF: _brief(payload),
        ViewName.RESOURCE_DIRECTORY: _resource_directory(resource_directory),
        ViewName.STATUS_REPORT: _status_report(status_report),
        ViewName.PEOPLE_DIRECTORY: _people_directory(people_directory),
        ViewName.GLOSSARY: _glossary(glossary),
        ViewName.AGENDA: _agenda(agenda),
        ViewName.SUGGESTIONS: _suggestions(suggestions),
    }
    content = _header(payload) + sections[name]
    return content.encode("utf-8")


def _header(payload: BriefPayload) -> str:
    generated_at = _timestamp(payload.generated_at)
    return (
        "---\n"
        f"generated_by: {GENERATOR_NAME}\n"
        f"source_revision: {payload.source_revision}\n"
        f"generated_at: {generated_at}\n"
        "---\n\n"
    )


def _current_focus(tasks: Sequence[TaskViewItem]) -> str:
    lines = [
        "# Current focus",
        "",
        "| Task | Priority | Status | Owner | Due | Blockers |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not tasks:
        lines.append("| _No current-focus tasks._ | — | — | — | — | — |")
    else:
        for task in tasks:
            lines.append(
                "| "
                + " | ".join(
                    (
                        _task_link(task),
                        task.priority.value,
                        task.status.value,
                        _cell(task.owner or "—"),
                        _cell(_optional_timestamp(task.due_at)),
                        _cell(", ".join(task.blockers) or "—"),
                    )
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def _next_actions(tasks: Sequence[TaskViewItem]) -> str:
    lines = [
        "# Next actions",
        "",
        "| Task | Status | Next action |",
        "| --- | --- | --- |",
    ]
    if not tasks:
        lines.append("| _No actionable tasks._ | — | — |")
    else:
        for task in tasks:
            lines.append(
                f"| {_task_link(task)} | {task.status.value} | {_cell(task.next_action)} |"
            )
    return "\n".join(lines) + "\n"


def _waiting_on(groups: Sequence[WaitingOnGroup]) -> str:
    lines = ["# Waiting on", ""]
    if not groups:
        lines.append("_No tasks are waiting on a person or external value._")
        return "\n".join(lines) + "\n"
    for group in groups:
        label = _cell(group.display_name)
        reference = _cell(group.person_uri or group.value)
        lines.extend((f"## {label}", "", f"Reference: `{reference}`", ""))
        for task in group.tasks:
            lines.append(f"- {_task_link(task)} — {_inline(task.next_action)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _stale_knowledge(claims: Sequence[StaleClaimItem]) -> str:
    lines = [
        "# Stale knowledge",
        "",
        "| Claim | Subject | Predicate | Value | Observed | Age (days) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not claims:
        lines.append("| _No stale current task claims._ | — | — | — | — | — |")
    else:
        for claim in claims:
            value = json.dumps(
                claim.object,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(claim.id),
                        _cell(claim.subject),
                        _cell(claim.predicate),
                        _cell(value),
                        _timestamp(claim.observed_at),
                        str(claim.age_days),
                    )
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def _brief(payload: BriefPayload) -> str:
    activity = payload.recent_ledger_activity
    lines = [
        "# Daily brief",
        "",
        "## Today focus",
        "",
    ]
    lines.extend(_task_bullets(payload.today_focus, "No current-focus tasks."))
    lines.extend(("", "## Blockers", ""))
    lines.extend(_task_bullets(payload.blockers, "No blockers recorded."))
    lines.extend(("", "## Waiting on", ""))
    if payload.waiting_on:
        for group in payload.waiting_on:
            task_ids = ", ".join(task.id for task in group.tasks)
            lines.append(f"- {_inline(group.display_name)}: {_inline(task_ids)}")
    else:
        lines.append("_No waiting-on relationships._")
    lines.extend(("", "## Stale claims", ""))
    if payload.stale_claims:
        for claim in payload.stale_claims:
            lines.append(
                f"- `{_inline(claim.id)}` — {_inline(claim.predicate)} ({claim.age_days} days old)"
            )
    else:
        lines.append("_No stale current task claims._")
    lines.extend(
        (
            "",
            "## Recent ledger activity",
            "",
            f"- Events: {activity.event_count}",
            f"- Head revision: `{activity.head_revision}`",
            f"- Last event: `{_inline(activity.last_event_id or 'none')}`",
            f"- Last proposal: `{_inline(activity.last_proposal_id or 'none')}`",
            f"- Last timestamp: {_optional_timestamp(activity.last_timestamp)}",
        )
    )
    return "\n".join(lines) + "\n"


def _resource_directory(payload: ResourceDirectoryPayload) -> str:
    lines: list[str] = []
    excluded: set[str] = set()
    _append_resource_line(lines, "# Resource directory", excluded=excluded)
    _append_resource_line(lines, "", excluded=excluded)
    if not payload.resources:
        _append_resource_line(lines, "_No resources._", excluded=excluded)
        return "\n".join(lines) + "\n"

    labels = {
        ResourceAccess.PUBLIC: "Public",
        ResourceAccess.SSO: "SSO",
        ResourceAccess.VPN: "VPN",
        ResourceAccess.OTHER: "Other",
        ResourceAccess.UNGROUPED: "Ungrouped",
    }
    for access in ResourceAccess:
        resources = tuple(item for item in payload.resources if item.access is access)
        if not resources:
            continue
        _append_resource_line(lines, f"## {labels[access]}", excluded=excluded)
        _append_resource_line(lines, "", excluded=excluded)
        _append_resource_line(
            lines,
            "| Resource | Link(s) | Description | Entity URI |",
            excluded=excluded,
        )
        _append_resource_line(
            lines,
            "| --- | --- | --- | --- |",
            excluded=excluded,
        )
        rendered = 0
        for resource in resources:
            links = "<br>".join(_resource_link(link) for link in resource.links) or "—"
            row = (
                "| "
                + " | ".join(
                    (
                        _cell(resource.title),
                        links,
                        _cell(resource.description or "—"),
                        f"`{_cell(resource.uri)}`",
                    )
                )
                + " |"
            )
            if _append_resource_line(
                lines,
                row,
                excluded=excluded,
                entity_id=resource.id,
            ):
                rendered += 1
        if rendered == 0:
            _append_resource_line(
                lines,
                "| _No renderable resources._ | — | — | — |",
                excluded=excluded,
            )
        _append_resource_line(lines, "", excluded=excluded)

    if excluded:
        _append_resource_line(lines, "## Exclusions", excluded=excluded)
        _append_resource_line(lines, "", excluded=excluded)
        for entity_id in sorted(excluded):
            _append_resource_line(
                lines,
                f"- `{_inline(entity_id)}`: excluded: possible secret",
                excluded=excluded,
                entity_id=entity_id,
            )
    return "\n".join(lines).rstrip() + "\n"


def _status_report(payload: StatusReportPayload) -> str:
    lines = [
        "# Status report",
        "",
        f"Period: {_timestamp(payload.period_start)} to {_timestamp(payload.period_end)}",
        "",
        "## Completed",
        "",
    ]
    if payload.completed:
        lines.extend(
            f"- {_record_link(item.id, item.title, item.uri)} reached done on "
            f"{_timestamp(item.completed_at)}."
            for item in payload.completed
        )
    else:
        lines.append("_No tasks reached done in this period._")

    lines.extend(("", "## Moved", ""))
    if payload.moved:
        lines.extend(
            f"- {_record_link(item.id, item.title, item.uri)} moved from "
            f"{item.from_status.value} to {item.to_status.value} on "
            f"{_timestamp(item.moved_at)}."
            for item in payload.moved
        )
    else:
        lines.append("_No task status transitions in this period._")

    lines.extend(("", "## Blocked and waiting", ""))
    if payload.blocked_waiting:
        lines.extend(
            f"- {_record_link(item.id, item.title, item.uri)} has been "
            f"{item.status.value} for {item.age_days} days since {_timestamp(item.since)}; "
            f"next action: {_inline(item.next_action)}"
            for item in payload.blocked_waiting
        )
    else:
        lines.append("_No tasks are currently blocked or waiting._")

    lines.extend(("", "## New commitments", ""))
    if payload.new_commitments:
        lines.extend(
            f"- {_record_link(item.id, item.title, item.uri)} was created on "
            f"{_timestamp(item.created_at)} and is due {_timestamp(item.due_at)}."
            for item in payload.new_commitments
        )
    else:
        lines.append("_No tasks with due dates were created in this period._")

    lines.extend(("", "## Evidence processed", ""))
    if payload.evidence_processed:
        lines.extend(
            f"- {_record_link(item.id, item.original_name, item.uri)} was archived on "
            f"{_timestamp(item.archived_at)}."
            for item in payload.evidence_processed
        )
    else:
        lines.append("_No evidence artifacts were archived in this period._")
    return "\n".join(lines) + "\n"


def _people_directory(payload: PeopleDirectoryPayload) -> str:
    lines = ["# People directory", "", "## People", ""]
    if payload.people:
        for person in payload.people:
            lines.extend(_directory_entity(person, include_tasks=True))
    else:
        lines.extend(("_No people._", ""))

    lines.extend(("## Teams", ""))
    if payload.teams:
        for team in payload.teams:
            lines.extend(_directory_entity(team, include_tasks=False))
    else:
        lines.append("_No teams._")
    return "\n".join(lines).rstrip() + "\n"


def _directory_entity(
    item: PeopleDirectoryItem,
    *,
    include_tasks: bool,
) -> list[str]:
    lines = [
        f"### {_inline(item.title)}",
        "",
        f"- URI: `{_inline(item.uri)}`",
        f"- Role: {_inline('; '.join(item.roles) or '—')}",
        f"- Team: {_inline('; '.join(item.teams) or '—')}",
        f"- Channels: {_inline('; '.join(item.channels) or '—')}",
        f"- Timezone: {_inline(item.timezone or '—')}",
    ]
    if include_tasks:
        lines.extend(
            (
                f"- Owns: {_directory_tasks(item.owned_tasks)}",
                f"- Blocks: {_directory_tasks(item.blocked_tasks)}",
                f"- Waiting on them: {_directory_tasks(item.waiting_tasks)}",
            )
        )
    lines.append("")
    return lines


def _directory_tasks(tasks: Sequence[DirectoryTaskItem]) -> str:
    return ", ".join(_record_link(task.id, task.title, task.uri) for task in tasks) or "—"


def _glossary(payload: GlossaryPayload) -> str:
    lines = ["# Glossary", ""]
    excluded: set[str] = set()
    if not payload.aliases:
        lines.append("_No glossary aliases._")
        return "\n".join(lines) + "\n"

    for item in payload.aliases:
        lines.extend(
            (
                f"## {_inline(item.alias)}",
                "",
                "| Entity | Type | URI | Definition |",
                "| --- | --- | --- | --- |",
            )
        )
        rendered = 0
        for owner in item.owners:
            row = (
                "| "
                + " | ".join(
                    (
                        _cell(owner.title),
                        _cell(owner.entity_type.value),
                        f"`{_cell(owner.uri)}`",
                        _cell(owner.definition or "—"),
                    )
                )
                + " |"
            )
            if _append_glossary_line(lines, row, excluded=excluded, entity_id=owner.id):
                rendered += 1
        if rendered == 0:
            lines.append("| _No renderable entries._ | — | — | — |")
        lines.append("")

    if excluded:
        lines.extend(("## Exclusions", ""))
        lines.extend(
            f"- `{_inline(entity_id)}`: excluded: possible secret" for entity_id in sorted(excluded)
        )
    return "\n".join(lines).rstrip() + "\n"


def _agenda(payload: AgendaPayload) -> str:
    lines = [
        "# Agenda",
        "",
        "## Due tasks",
        "",
        "| Task | Status | Due | Overdue |",
        "| --- | --- | --- | --- |",
    ]
    if payload.due_tasks:
        lines.extend(
            f"| {_record_link(item.id, item.title, item.uri)} | {item.status.value} | "
            f"{_timestamp(item.due_at)} | {'yes' if item.overdue else 'no'} |"
            for item in payload.due_tasks
        )
    else:
        lines.append("| _No tasks with due dates._ | — | — | — |")

    lines.extend(
        (
            "",
            "## Waiting on",
            "",
            "| Task | Waiting on | Since | Age (days) |",
            "| --- | --- | --- | --- |",
        )
    )
    if payload.waiting_on:
        for item in payload.waiting_on:
            reference = item.person_uri or item.waiting_on
            waiting = f"{_cell(item.display_name)} (`{_cell(reference)}`)"
            lines.append(
                f"| {_record_link(item.id, item.title, item.uri)} | {waiting} | "
                f"{_timestamp(item.since)} | {item.age_days} |"
            )
    else:
        lines.append("| _No waiting-on entries._ | — | — | — |")

    lines.extend(
        (
            "",
            "## Blocked tasks",
            "",
            "| Task | Blocked since | Age (days) |",
            "| --- | --- | --- |",
        )
    )
    if payload.blocked_tasks:
        lines.extend(
            f"| {_record_link(item.id, item.title, item.uri)} | "
            f"{_timestamp(item.since)} | {item.age_days} |"
            for item in payload.blocked_tasks
        )
    else:
        lines.append("| _No blocked tasks._ | — | — |")
    return "\n".join(lines) + "\n"


def _suggestions(payload: SuggestionsPayload) -> str:
    lines = [
        "# Suggestions",
        "",
        "This advisory view reports canonical and verified audit signals only. "
        "It never takes action automatically.",
        "",
        "## Records",
        "",
        "| Suggestion | Type | Age (days) | Rationale |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(_suggestion_record_lines(payload.records))
    sections = (
        ("Stale claims", payload.stale_claims),
        ("Broken evidence links", payload.broken_evidence_links),
        ("Inactive tasks", payload.inactive_tasks),
        ("Orphaned knowledge", payload.orphaned_knowledge),
        ("Old waiting-on entries", payload.old_waiting_on),
    )
    for heading, items in sections:
        lines.extend(("", f"## {heading}", ""))
        lines.extend(_suggestion_lines(items))
    return "\n".join(lines) + "\n"


def _suggestion_record_lines(items: Sequence[SuggestionRecordItem]) -> list[str]:
    if not items:
        return ["| _No open suggestion records._ | — | — | — |"]
    return [
        f"| [{_cell(item.id)}]({_inline(item.uri)}) | {item.type} | "
        f"{item.age_days} | {_cell(item.rationale)} |"
        for item in items
    ]


def _suggestion_lines(items: Sequence[SuggestionItem]) -> list[str]:
    if not items:
        return ["_No suggestions._"]
    return [
        f"- {_record_link(item.id, item.title, item.uri)} — {_inline(item.statement)} "
        f"Signal: {_inline(item.signal)}"
        for item in items
    ]


def _append_resource_line(
    lines: list[str],
    line: str,
    *,
    excluded: set[str],
    entity_id: str | None = None,
) -> bool:
    if contains_possible_secret(line):
        if entity_id is not None:
            excluded.add(entity_id)
        return False
    lines.append(line)
    return True


def _append_glossary_line(
    lines: list[str],
    line: str,
    *,
    excluded: set[str],
    entity_id: str,
) -> bool:
    if contains_possible_secret(line):
        excluded.add(entity_id)
        return False
    lines.append(line)
    return True


def _resource_link(link: ResourceLinkItem) -> str:
    return f"[{_cell(link.label or link.url)}]({_inline(link.url)})"


def _task_bullets(tasks: Sequence[TaskViewItem], empty: str) -> list[str]:
    if not tasks:
        return [f"_{empty}_"]
    return [
        f"- {_task_link(task)} [{task.priority.value}/{task.status.value}] — "
        f"{_inline(task.next_action)}"
        for task in tasks
    ]


def _task_link(task: TaskViewItem) -> str:
    return _record_link(task.id, task.title, task.uri)


def _record_link(record_id: str, title: str, uri: str) -> str:
    return f"[{_cell(record_id)} — {_cell(title)}]({_inline(uri)})"


def _cell(value: str) -> str:
    return _inline(value).replace("|", "\\|")


def _inline(value: str) -> str:
    return " ".join(value.replace("\x00", "").splitlines()).strip()


def _optional_timestamp(value: datetime | None) -> str:
    return "—" if value is None else _timestamp(value)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["GENERATOR_NAME", "render_view"]
