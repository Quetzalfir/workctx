from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workctx.adapters.filesystem import CanonicalStore
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import ArtifactSourceType, EntityFrontmatter, TaskPriority, TaskStatus
from workctx.domain.transactions import TransactionProposal
from workctx.ingestion import ArtifactRecord, IngestionService, RegisterRequest
from workctx.tasks import TaskService
from workctx.transactions import ApplyResult, verify_ledger
from workctx.views import ViewName, ViewService

from .support import (
    CONTEXT_ID,
    OBSERVATION_URI,
    MutableClock,
    clocked_transaction_apply,
    human_actor,
    initialize_tasks_context,
    task,
)


def test_resource_directory_and_status_report_are_deterministic_and_complete(
    tmp_path: Path,
) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    _write_resource(
        root,
        entity_id="SYS-fictional-portal",
        entity_type="system",
        title="Fictional employee portal",
        body="Portal for fictional employee workflows.\nSecond line is not rendered.\n",
        references=[
            {
                "relation": "related_to",
                "target": "https://docs.example.test/portal",
            }
        ],
        access_urls=[
            {
                "url": "https://portal.example.test",
                "label": "Production",
                "access": "public",
            }
        ],
    )
    _write_resource(
        root,
        entity_id="SVC-fictional-platform",
        entity_type="service",
        title="Fictional platform service",
        body="Service console for a fictional platform.\n",
        access_urls=[
            {
                "url": "https://console.example.test",
                "label": "Console",
                "access": "sso",
            },
            {
                "url": "https://dev.example.test",
                "label": "Development",
                "access": "vpn",
            },
            {
                "url": "https://ops.example.test",
                "label": "Operations",
                "access": "restricted",
            },
        ],
    )
    _write_resource(
        root,
        entity_id="INT-fictional-feed",
        entity_type="integration",
        title="Fictional status feed",
        body="Integration for fictional status updates.\n",
        access_urls=["https://feed.example.test"],
    )

    clock = MutableClock(datetime(2026, 7, 20, 9, tzinfo=UTC))
    transaction_apply = clocked_transaction_apply(clock)
    tasks = TaskService(
        root,
        actor=human_actor(),
        clock=clock,
        transaction_apply=transaction_apply,
    )
    tasks.create_task(
        task(
            "TASK-2026-102",
            clock.value,
            status=TaskStatus.WAITING,
            priority=TaskPriority.P2,
            next_action="Wait for the fictional external review.",
        ),
        approved=True,
    )
    clock.value = datetime(2026, 7, 25, 9, tzinfo=UTC)
    tasks.create_task(task("TASK-2026-101", clock.value), approved=True)
    clock.value = datetime(2026, 7, 29, 9, tzinfo=UTC)
    tasks.create_task(
        task(
            "TASK-2026-100",
            clock.value,
            due_at=datetime(2026, 8, 10, 17, tzinfo=UTC),
        ),
        approved=True,
    )
    clock.value = datetime(2026, 7, 31, 9, tzinfo=UTC)
    tasks.transition_status(
        "TASK-2026-101",
        TaskStatus.BLOCKED,
        source_observations=[OBSERVATION_URI],
        approved=True,
    )
    clock.value = datetime(2026, 8, 1, 9, tzinfo=UTC)
    tasks.transition_status(
        "TASK-2026-100",
        TaskStatus.DONE,
        source_observations=[OBSERVATION_URI],
        approved=True,
    )
    clock.value = datetime(2026, 8, 2, 10, tzinfo=UTC)
    archived = _archive_status_artifact(root, clock, transaction_apply)

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
        digest = hashlib.sha256(first_bytes[view.path]).hexdigest()
        assert view.content_hash == f"sha256:{digest}"

    resources = first_bytes["04_views/resource-directory.md"].decode()
    assert resources.index("## Public") < resources.index("## SSO")
    assert resources.index("## SSO") < resources.index("## VPN")
    assert resources.index("## VPN") < resources.index("## Other")
    assert resources.index("## Other") < resources.index("## Ungrouped")
    assert "[Production](https://portal.example.test)" in resources
    assert "[Console](https://console.example.test)" in resources
    assert "[Development](https://dev.example.test)" in resources
    assert "[Operations](https://ops.example.test)" in resources
    assert "[https://docs.example.test/portal](https://docs.example.test/portal)" in resources
    assert "[https://feed.example.test](https://feed.example.test)" in resources
    assert "Portal for fictional employee workflows." in resources
    assert "Second line is not rendered." not in resources
    assert f"workctx://{CONTEXT_ID}/system/SYS-fictional-portal" in resources

    status = first_bytes["04_views/status-report.md"].decode()
    assert "Period: 2026-07-27T12:00:00Z to 2026-08-03T12:00:00Z" in status
    assert "TASK-2026-100" in _section(status, "Completed", "Moved")
    moved = _section(status, "Moved", "Blocked and waiting")
    assert "TASK-2026-101" in moved
    assert "moved from active to blocked on 2026-07-31T09:00:00Z" in moved
    assert "TASK-2026-100" in moved
    assert "moved from active to done on 2026-08-01T09:00:00Z" in moved
    assert "TASK-2026-102" not in moved
    blocked = _section(status, "Blocked and waiting", "New commitments")
    assert "TASK-2026-101" in blocked
    assert "blocked for 3 days since 2026-07-31T09:00:00Z" in blocked
    assert "TASK-2026-102" in blocked
    assert "waiting for 14 days since 2026-07-20T09:00:00Z" in blocked
    commitments = _section(status, "New commitments", "Evidence processed")
    assert "TASK-2026-100" in commitments
    assert "is due 2026-08-10T17:00:00Z" in commitments
    evidence = _section(status, "Evidence processed", None)
    assert archived.manifest.id in evidence
    assert archived.reference in evidence
    assert "fictional-status-note.txt" in evidence
    assert "was archived on 2026-08-02T10:02:00Z" in evidence


def test_resource_directory_excludes_possible_secret_lines(tmp_path: Path) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    _write_resource(
        root,
        entity_id="INT-fictional-secret",
        entity_type="integration",
        title="Fictional secret integration",
        body='api_key = "sk-fictional-1234567890abcdef"\nSafe second line.\n',
        access_urls=[
            {
                "url": "https://secret.example.test",
                "label": "Fictional secret console",
                "access": "public",
            }
        ],
    )
    SQLiteProjection(root).rebuild()

    generated_at = datetime(2026, 8, 3, 12, tzinfo=UTC)
    ViewService(root, clock=lambda: generated_at).rebuild_view(ViewName.RESOURCE_DIRECTORY)
    rendered = (root / "04_views" / "resource-directory.md").read_text(encoding="utf-8")

    assert "sk-fictional-1234567890abcdef" not in rendered
    assert "Fictional secret integration" not in rendered
    exclusion_lines = [line for line in rendered.splitlines() if "excluded:" in line]
    assert exclusion_lines == ["- `INT-fictional-secret`: excluded: possible secret"]


def test_new_views_render_empty_sections_and_rebuild_after_delete(tmp_path: Path) -> None:
    root = initialize_tasks_context(tmp_path / "context")
    generated_at = datetime(2026, 8, 3, 12, tzinfo=UTC)
    service = ViewService(root, clock=lambda: generated_at)

    first = service.rebuild_views()
    first_bytes = {view.path: (root / view.path).read_bytes() for view in first.views}
    resource = first_bytes["04_views/resource-directory.md"].decode()
    status = first_bytes["04_views/status-report.md"].decode()

    assert "_No resources._" in resource
    assert "_No tasks reached done in this period._" in status
    assert "_No task status transitions in this period._" in status
    assert "_No tasks are currently blocked or waiting._" in status
    assert "_No tasks with due dates were created in this period._" in status
    assert "_No evidence artifacts were archived in this period._" in status

    shutil.rmtree(root / "04_views")
    rebuilt = service.rebuild_views()
    rebuilt_bytes = {view.path: (root / view.path).read_bytes() for view in rebuilt.views}

    assert rebuilt == first
    assert rebuilt_bytes == first_bytes


def _write_resource(
    root: Path,
    *,
    entity_id: str,
    entity_type: str,
    title: str,
    body: str,
    references: list[dict[str, object]] | None = None,
    access_urls: list[object] | None = None,
) -> None:
    timestamp = datetime(2026, 7, 1, 12, tzinfo=UTC)
    frontmatter = EntityFrontmatter.model_validate(
        {
            "schema_version": 1,
            "id": entity_id,
            "entity_type": entity_type,
            "title": title,
            "uri": f"workctx://{CONTEXT_ID}/{entity_type}/{entity_id}",
            "aliases": [],
            "status": "active",
            "confidence": "high",
            "tags": ["fictional"],
            "references": references or [],
            "created_at": timestamp,
            "updated_at": timestamp,
            "access_urls": access_urls or [],
        }
    )
    (root / "02_knowledge" / "resources").mkdir(exist_ok=True)
    CanonicalStore(root).write_entity(
        f"02_knowledge/resources/{entity_id}.md",
        frontmatter,
        body,
    )


def _archive_status_artifact(
    root: Path,
    clock: MutableClock,
    transaction_apply: Callable[..., ApplyResult],
) -> ArtifactRecord:
    source = root / "00_inbox" / "raw" / "fictional-status-note.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"Fictional evidence processed for the status report.\n")
    ingestion = IngestionService(
        root,
        clock=clock,
        transaction_apply=transaction_apply,
    )
    registration = ingestion.register(
        RegisterRequest(
            path="00_inbox/raw/fictional-status-note.txt",
            source_type=ArtifactSourceType.NOTE,
        ),
        session_id="wp600-register",
    )
    clock.value += timedelta(minutes=1)
    processing = _processing_receipt(
        root,
        registration.artifact.reference,
        clock,
        transaction_apply,
    )
    clock.value += timedelta(minutes=1)
    archived = ingestion.archive_after(
        registration.artifact.manifest.id,
        processing,
        session_id="wp600-archive",
    )
    return archived.artifact


def _processing_receipt(
    root: Path,
    artifact_reference: str,
    clock: MutableClock,
    transaction_apply: Callable[..., ApplyResult],
) -> ApplyResult:
    timestamp = clock.value
    proposal = TransactionProposal.model_validate(
        {
            "schema_version": 1,
            "id": f"TXP-{timestamp:%Y%m%dT%H%M%SZ}-status-proof",
            "context_id": CONTEXT_ID,
            "base_revision": verify_ledger(root).head_hash,
            "actor": human_actor().model_dump(mode="json"),
            "created_at": timestamp,
            "source_refs": [artifact_reference],
            "operations": [
                {
                    "op": "create",
                    "target": "02_knowledge/projects/PRJ-status-proof.md",
                    "payload": {
                        "kind": "entity",
                        "document": {
                            "schema_version": 1,
                            "id": "PRJ-status-proof",
                            "entity_type": "project",
                            "title": "Fictional status proof",
                            "uri": f"workctx://{CONTEXT_ID}/project/PRJ-status-proof",
                            "aliases": [],
                            "status": "active",
                            "confidence": "high",
                            "tags": ["fictional"],
                            "references": [],
                            "created_at": timestamp,
                            "updated_at": timestamp,
                        },
                        "body": "Fictional processing proof.\n",
                    },
                }
            ],
            "preconditions": [],
            "postconditions": [],
            "expected_views": ["sqlite"],
            "approval": "required",
        }
    )
    return transaction_apply(
        root,
        proposal,
        approved=True,
        session_id="wp600-processing-proof",
    )


def _section(content: str, heading: str, next_heading: str | None) -> str:
    start = content.index(f"## {heading}")
    if next_heading is None:
        return content[start:]
    end = content.index(f"## {next_heading}", start)
    return content[start:end]
