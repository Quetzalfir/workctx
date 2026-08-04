from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workctx.adapters.filesystem import CanonicalStore
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import EntityFrontmatter, TaskPriority, TaskStatus
from workctx.services.contexts import initialize_context
from workctx.tasks import TaskService
from workctx.validation.engine import contains_possible_secret
from workctx.views import ViewName, ViewService

from .support import (
    ALICE_URI,
    CONTEXT_ID,
    MutableClock,
    clocked_transaction_apply,
    human_actor,
    initialize_tasks_context,
    task,
)

TEAM_URI = f"workctx://{CONTEXT_ID}/team/TEAM-platform"
CONNECTED_URI = f"workctx://{CONTEXT_ID}/system/SYS-connected"
ORPHAN_URI = f"workctx://{CONTEXT_ID}/system/SYS-orphan"
BLOCKED_TASK_URI = f"workctx://{CONTEXT_ID}/task/TASK-2026-200"


def test_four_new_views_are_deterministic_complete_and_rebuildable(tmp_path: Path) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    timestamp = datetime(2026, 6, 1, 12, tzinfo=UTC)
    _write_entity(
        root,
        relative_path="02_knowledge/directory/TEAM-platform.md",
        entity_id="TEAM-platform",
        entity_type="team",
        title="Platform Team",
        aliases=["HCM"],
        body="Platform operations team.\nThis second line is not a definition.\n",
        timestamp=timestamp,
        references=[{"relation": "related_to", "target": CONNECTED_URI}],
        extras={"channels": {"slack": "#fictional-platform"}, "timezone": "UTC"},
    )
    _write_entity(
        root,
        relative_path="02_knowledge/directory/SYS-connected.md",
        entity_id="SYS-connected",
        entity_type="system",
        title="Connected HCM Platform",
        aliases=["HCM", "People Hub"],
        body="Human capital management platform.\nA later line stays out of the glossary.\n",
        timestamp=timestamp,
    )
    _write_entity(
        root,
        relative_path="02_knowledge/directory/SYS-orphan.md",
        entity_id="SYS-orphan",
        entity_type="system",
        title="Orphaned Fictional System",
        aliases=["OFS"],
        body="A deliberately disconnected fictional system.\n",
        timestamp=timestamp,
    )
    _write_alice(root, timestamp, blocks=())
    SQLiteProjection(root).rebuild()

    mutation_clock = MutableClock(datetime(2026, 6, 1, 13, tzinfo=UTC))
    tasks = TaskService(
        root,
        actor=human_actor(),
        clock=mutation_clock,
        transaction_apply=clocked_transaction_apply(mutation_clock),
    )
    tasks.create_task(
        task(
            "TASK-2026-203",
            mutation_clock.value,
            owner=None,
            next_action="Supply the fictional blocker input.",
        ),
        approved=True,
    )
    mutation_clock.value += timedelta(minutes=1)
    tasks.create_task(
        task(
            "TASK-2026-200",
            mutation_clock.value,
            status=TaskStatus.BLOCKED,
            priority=TaskPriority.P0,
            due_at=datetime(2026, 7, 1, 17, tzinfo=UTC),
            blockers=(f"workctx://{CONTEXT_ID}/task/TASK-2026-203",),
            next_action="Resolve the fictional blocker.",
        ),
        approved=True,
    )
    mutation_clock.value += timedelta(minutes=1)
    tasks.create_task(
        task(
            "TASK-2026-201",
            mutation_clock.value,
            status=TaskStatus.WAITING,
            priority=TaskPriority.P1,
            owner=None,
            waiting_on=(ALICE_URI,),
            due_at=datetime(2026, 8, 10, 17, tzinfo=UTC),
            next_action="Wait for Alice's fictional response.",
        ),
        approved=True,
    )
    mutation_clock.value += timedelta(minutes=1)
    tasks.create_task(
        task(
            "TASK-2026-202",
            mutation_clock.value,
            status=TaskStatus.ACTIVE,
            owner=None,
        ),
        approved=True,
    )

    _write_alice(root, timestamp + timedelta(days=1), blocks=(BLOCKED_TASK_URI,))
    broken = task(
        "TASK-2026-204",
        timestamp,
        status=TaskStatus.ACTIVE,
        owner=None,
    ).model_copy(
        update={
            "source_observations": [
                f"workctx://{CONTEXT_ID}/observation/EVD-20260601-task-state-01%23OBS-999"
            ]
        }
    )
    CanonicalStore(root).write_task(
        "03_work/tasks/TASK-2026-204.md",
        broken,
        "Fictional task with intentionally broken evidence provenance.\n",
    )
    SQLiteProjection(root).rebuild()

    generated_at = datetime(2026, 8, 3, 12, tzinfo=UTC)
    service = ViewService(root, clock=lambda: generated_at)
    first = service.rebuild_views()
    first_bytes = {view.path: (root / view.path).read_bytes() for view in first.views}
    second = service.rebuild_views()
    second_bytes = {view.path: (root / view.path).read_bytes() for view in second.views}

    assert first == second
    assert first_bytes == second_bytes
    assert {view.name for view in first.views} == set(ViewName)
    for view in first.views:
        assert view.content_hash == f"sha256:{hashlib.sha256(first_bytes[view.path]).hexdigest()}"

    people = first_bytes["04_views/people-directory.md"].decode()
    assert people.index("### Alice Rivera") < people.index("### Jordan Lee")
    alice = _section(people, "### Alice Rivera", "### Jordan Lee")
    assert "- Role: Fictional engineering lead" in alice
    assert f"Platform Team ({TEAM_URI})" in alice
    assert "email: alice@example.test" in alice
    assert "slack: @alice-fictional" in alice
    assert "Direct message: slack://fictional/alice" in alice
    assert "- Timezone: America/Chicago" in alice
    assert "- Owns:" in alice and "TASK-2026-200" in alice
    assert "- Blocks:" in alice and "TASK-2026-200" in alice
    assert "- Waiting on them:" in alice and "TASK-2026-201" in alice
    teams = _section(people, "## Teams", None)
    assert "### Platform Team" in teams
    assert "#fictional-platform" in teams

    glossary = first_bytes["04_views/glossary.md"].decode()
    assert glossary.index("## HCM") < glossary.index("## OFS")
    hcm = _section(glossary, "## HCM", "## OFS")
    assert "Connected HCM Platform" in hcm
    assert "Platform Team" in hcm
    assert "Human capital management platform." in hcm
    assert "Platform operations team." in hcm
    assert "A later line stays out of the glossary." not in glossary
    assert "This second line is not a definition." not in glossary
    assert "Alice Rivera" not in glossary

    agenda = first_bytes["04_views/agenda.md"].decode()
    due = _section(agenda, "## Due tasks", "## Waiting on")
    assert due.index("TASK-2026-200") < due.index("TASK-2026-201")
    assert "2026-07-01T17:00:00Z | yes" in due
    assert "2026-08-10T17:00:00Z | no" in due
    waiting = _section(agenda, "## Waiting on", "## Blocked tasks")
    assert "TASK-2026-201" in waiting
    assert ALICE_URI in waiting
    assert "| 62 |" in waiting
    blocked = _section(agenda, "## Blocked tasks", None)
    assert "TASK-2026-200" in blocked
    assert BLOCKED_TASK_URI in blocked
    assert "| 62 |" in blocked

    suggestions = first_bytes["04_views/suggestions.md"].decode()
    assert "It never takes action automatically." in suggestions
    assert "review or supersede" in suggestions
    assert "TASK-2026-204" in _section(suggestions, "## Broken evidence links", "## Inactive tasks")
    assert "TASK-2026-202" in _section(suggestions, "## Inactive tasks", "## Orphaned knowledge")
    orphaned = _section(suggestions, "## Orphaned knowledge", "## Old waiting-on entries")
    assert "SYS-orphan" in orphaned
    assert ORPHAN_URI in orphaned
    assert "SYS-connected" not in orphaned
    old_waiting = _section(suggestions, "## Old waiting-on entries", None)
    assert "TASK-2026-201" in old_waiting
    assert "chase or drop" in old_waiting
    suggestion_lines = [line for line in suggestions.splitlines() if line.startswith("- [")]
    assert suggestion_lines
    assert all("workctx://" in line and "Signal:" in line for line in suggestion_lines)
    assert "usage count" not in suggestions.casefold()
    assert "telemetry" not in suggestions.casefold()

    shutil.rmtree(root / "04_views")
    rebuilt = service.rebuild_views()
    rebuilt_bytes = {view.path: (root / view.path).read_bytes() for view in rebuilt.views}
    assert rebuilt == first
    assert rebuilt_bytes == first_bytes


def test_glossary_lists_every_owner_for_duplicate_alias(tmp_path: Path) -> None:
    root = _initialize_empty_context(tmp_path / "context")
    timestamp = datetime(2026, 7, 1, 12, tzinfo=UTC)
    for entity_id, title, body in (
        ("SYS-alpha", "Alpha System", "First fictional definition.\n"),
        ("SVC-beta", "Beta Service", "Second fictional definition.\n"),
    ):
        entity_type = "system" if entity_id.startswith("SYS") else "service"
        _write_entity(
            root,
            relative_path=f"02_knowledge/directory/{entity_id}.md",
            entity_id=entity_id,
            entity_type=entity_type,
            title=title,
            aliases=["Shared Alias"],
            body=body,
            timestamp=timestamp,
        )
    SQLiteProjection(root).rebuild()

    ViewService(root, clock=lambda: datetime(2026, 8, 3, 12, tzinfo=UTC)).rebuild_view(
        ViewName.GLOSSARY
    )
    rendered = (root / "04_views" / "glossary.md").read_text(encoding="utf-8")

    shared = _section(rendered, "## Shared Alias", None)
    assert shared.index("Alpha System") < shared.index("Beta Service")
    assert "First fictional definition." in shared
    assert "Second fictional definition." in shared


def test_new_views_render_empty_sections_and_rebuild_after_delete(tmp_path: Path) -> None:
    root = _initialize_empty_context(tmp_path / "context")
    generated_at = datetime(2026, 8, 3, 12, tzinfo=UTC)
    service = ViewService(root, clock=lambda: generated_at)

    first = service.rebuild_views()
    first_bytes = {view.path: (root / view.path).read_bytes() for view in first.views}
    assert "_No people._" in first_bytes["04_views/people-directory.md"].decode()
    assert "_No teams._" in first_bytes["04_views/people-directory.md"].decode()
    assert "_No glossary aliases._" in first_bytes["04_views/glossary.md"].decode()
    agenda = first_bytes["04_views/agenda.md"].decode()
    assert "_No tasks with due dates._" in agenda
    assert "_No waiting-on entries._" in agenda
    assert "_No blocked tasks._" in agenda
    suggestions = first_bytes["04_views/suggestions.md"].decode()
    assert suggestions.count("_No suggestions._") == 5

    shutil.rmtree(root / "04_views")
    rebuilt = service.rebuild_views()
    rebuilt_bytes = {view.path: (root / view.path).read_bytes() for view in rebuilt.views}
    assert rebuilt == first
    assert rebuilt_bytes == first_bytes


def test_glossary_excludes_possible_secret_definition_lines(tmp_path: Path) -> None:
    root = _initialize_empty_context(tmp_path / "context")
    _write_entity(
        root,
        relative_path="02_knowledge/directory/SYS-secret.md",
        entity_id="SYS-secret",
        entity_type="system",
        title="Fictional Secret System",
        aliases=["Secret Alias"],
        body='api_key = "sk-fictional-1234567890abcdef"\nSafe second line is not substituted.\n',
        timestamp=datetime(2026, 7, 1, 12, tzinfo=UTC),
    )
    SQLiteProjection(root).rebuild()

    ViewService(root, clock=lambda: datetime(2026, 8, 3, 12, tzinfo=UTC)).rebuild_view(
        ViewName.GLOSSARY
    )
    rendered = (root / "04_views" / "glossary.md").read_text(encoding="utf-8")

    assert "sk-fictional-1234567890abcdef" not in rendered
    assert "Fictional Secret System" not in rendered
    assert "Safe second line is not substituted." not in rendered
    exclusion_lines = [line for line in rendered.splitlines() if "excluded:" in line]
    assert exclusion_lines == ["- `SYS-secret`: excluded: possible secret"]
    body_rows = [
        line
        for line in rendered.splitlines()
        if line.startswith("| ") and not line.startswith("| ---")
    ]
    assert all(not contains_possible_secret(line) for line in body_rows)


def _initialize_empty_context(root: Path) -> Path:
    initialize_context(root, name="Fictional Empty Views Lab", context_id=CONTEXT_ID)
    SQLiteProjection(root).rebuild()
    return root


def _write_alice(root: Path, timestamp: datetime, *, blocks: tuple[str, ...]) -> None:
    references: list[dict[str, object]] = [
        {"relation": "owned_by", "target": TEAM_URI},
        {
            "relation": "related_to",
            "target": "slack://fictional/alice",
            "note": "Direct message",
        },
    ]
    references.extend({"relation": "blocks", "target": target} for target in blocks)
    _write_entity(
        root,
        relative_path="02_knowledge/people/PER-alice-rivera.md",
        entity_id="PER-alice-rivera",
        entity_type="person",
        title="Alice Rivera",
        aliases=[],
        body="Fictional person.\n",
        timestamp=timestamp,
        references=references,
        extras={
            "role": "Fictional engineering lead",
            "channels": {
                "slack": "@alice-fictional",
                "email": "alice@example.test",
            },
            "timezone": "America/Chicago",
        },
    )


def _write_entity(
    root: Path,
    *,
    relative_path: str,
    entity_id: str,
    entity_type: str,
    title: str,
    aliases: list[str],
    body: str,
    timestamp: datetime,
    references: list[dict[str, object]] | None = None,
    extras: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": entity_id,
        "entity_type": entity_type,
        "title": title,
        "uri": f"workctx://{CONTEXT_ID}/{entity_type}/{entity_id}",
        "aliases": aliases,
        "status": "active",
        "confidence": "high",
        "tags": ["fictional"],
        "references": references or [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    payload.update(extras or {})
    frontmatter = EntityFrontmatter.model_validate(payload)
    (root / Path(relative_path).parent).mkdir(parents=True, exist_ok=True)
    CanonicalStore(root).write_entity(relative_path, frontmatter, body)


def _section(content: str, heading: str, next_heading: str | None) -> str:
    start = content.index(heading)
    if next_heading is None:
        return content[start:]
    return content[start : content.index(next_heading, start + len(heading))]
