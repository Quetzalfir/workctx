"""Byte-deterministic Markdown rendering for operational views."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from workctx.views.models import (
    BriefPayload,
    StaleClaimItem,
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
) -> bytes:
    """Render one generated file from an already consistent structured snapshot."""

    sections = {
        ViewName.CURRENT_FOCUS: _current_focus(payload.today_focus),
        ViewName.NEXT_ACTIONS: _next_actions(next_actions),
        ViewName.WAITING_ON: _waiting_on(payload.waiting_on),
        ViewName.STALE_KNOWLEDGE: _stale_knowledge(payload.stale_claims),
        ViewName.BRIEF: _brief(payload),
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


def _task_bullets(tasks: Sequence[TaskViewItem], empty: str) -> list[str]:
    if not tasks:
        return [f"_{empty}_"]
    return [
        f"- {_task_link(task)} [{task.priority.value}/{task.status.value}] — "
        f"{_inline(task.next_action)}"
        for task in tasks
    ]


def _task_link(task: TaskViewItem) -> str:
    return f"[{_cell(task.id)} — {_cell(task.title)}]({_inline(task.uri)})"


def _cell(value: str) -> str:
    return _inline(value).replace("|", "\\|")


def _inline(value: str) -> str:
    return " ".join(value.replace("\x00", "").splitlines()).strip()


def _optional_timestamp(value: datetime | None) -> str:
    return "—" if value is None else _timestamp(value)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = ["GENERATOR_NAME", "render_view"]
