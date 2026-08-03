from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workctx.adapters.filesystem import CanonicalStore
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import (
    ArtifactManifest,
    ArtifactSourceType,
    ArtifactStatus,
    EntityFrontmatter,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from workctx.domain.transactions import HumanActor
from workctx.services.contexts import initialize_context

CONTEXT_ID = "tasks-lab"
EVIDENCE_ID = "EVD-20260601-task-state-01"
OBSERVATION_ID = f"{EVIDENCE_ID}#OBS-001"
OBSERVATION_URI = f"workctx://{CONTEXT_ID}/observation/{EVIDENCE_ID}%23OBS-001"
ALICE_URI = f"workctx://{CONTEXT_ID}/person/PER-alice-rivera"
JORDAN_URI = f"workctx://{CONTEXT_ID}/person/PER-jordan-lee"


@dataclass(slots=True)
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


def human_actor() -> HumanActor:
    return HumanActor(
        type="human",
        id="fictional-operator",
        agent=None,
        model=None,
    )


def initialize_tasks_context(root: Path) -> Path:
    initialize_context(root, name="Fictional Tasks Lab", context_id=CONTEXT_ID)
    store = CanonicalStore(root)
    timestamp = datetime(2026, 6, 1, 12, tzinfo=UTC)

    evidence_bytes = b"Fictional task state was reviewed.\n"
    digest = hashlib.sha256(evidence_bytes).hexdigest()
    artifact_ref = f"artifact://sha256/{digest}"
    preserved = root / "01_processed" / "task-state.txt"
    preserved.write_bytes(evidence_bytes)
    manifest = ArtifactManifest(
        schema_version=1,
        id="ART-20260601-task-state-01",
        content_hash=f"sha256:{digest}",
        original_name="task-state.txt",
        media_type="text/plain",
        source_type=ArtifactSourceType.NOTE,
        source_origin=None,
        event_at=timestamp,
        event_at_inferred=False,
        ingested_at=timestamp,
        language="en",
        participants=[],
        classification="internal",
        status=ArtifactStatus.PROCESSED,
        preserved_path="01_processed/task-state.txt",
        sidecars=[],
        duplicate_of=None,
        notes=None,
    )
    store.write_artifact_manifest(
        "00_inbox/manifests/ART-20260601-task-state-01.json",
        manifest,
    )

    for person_id, title in (
        ("PER-alice-rivera", "Alice Rivera"),
        ("PER-jordan-lee", "Jordan Lee"),
    ):
        person = EntityFrontmatter(
            schema_version=1,
            id=person_id,
            entity_type="person",
            title=title,
            uri=f"workctx://{CONTEXT_ID}/person/{person_id}",
            aliases=[],
            status="active",
            confidence="high",
            tags=["fictional"],
            references=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
        store.write_entity(f"02_knowledge/people/{person_id}.md", person, "Fictional person.\n")

    evidence = EntityFrontmatter.model_validate(
        {
            "schema_version": 1,
            "id": EVIDENCE_ID,
            "entity_type": "evidence",
            "title": "Task state review",
            "uri": f"workctx://{CONTEXT_ID}/evidence/{EVIDENCE_ID}",
            "aliases": [],
            "status": "active",
            "confidence": "high",
            "tags": ["fictional"],
            "references": [],
            "created_at": timestamp,
            "updated_at": timestamp,
            "artifact_ref": artifact_ref,
            "observations": [
                {
                    "id": OBSERVATION_ID,
                    "kind": "fact",
                    "statement": "The fictional task state is supported by the note.",
                    "confidence": "high",
                    "source": {
                        "ref": artifact_ref,
                        "locator": {"type": "line_range", "start_line": 1, "end_line": 1},
                    },
                    "observed_at": "2026-06-01T12:00:00Z",
                    "valid_from": None,
                    "valid_to": None,
                    "derived_from": [],
                    "related": [],
                }
            ],
        }
    )
    store.write_entity(
        f"02_knowledge/evidence/{EVIDENCE_ID}.md",
        evidence,
        "Fictional source for task-state changes.\n",
    )
    SQLiteProjection(root).rebuild()
    return root


def task(
    task_id: str,
    timestamp: datetime,
    *,
    status: TaskStatus = TaskStatus.ACTIVE,
    priority: TaskPriority = TaskPriority.P1,
    owner: str | None = ALICE_URI,
    waiting_on: tuple[str, ...] = (),
    due_at: datetime | None = None,
    blockers: tuple[str, ...] = (),
    next_action: str = "Review the fictional task evidence.",
    task_type: TaskType = TaskType.PARENT,
) -> Task:
    parent_id = task_id.rsplit("-ST", maxsplit=1)[0]
    is_subtask = task_type is TaskType.SUBTASK
    return Task(
        schema_version=1,
        id=task_id,
        entity_type="task",
        title=f"Fictional {task_id}",
        uri=f"workctx://{CONTEXT_ID}/task/{task_id}",
        aliases=[],
        status=status,
        confidence="high",
        tags=["fictional"],
        references=[],
        created_at=timestamp,
        updated_at=timestamp,
        task_type=task_type,
        parent_task=parent_id if is_subtask else None,
        root_task=parent_id if is_subtask else task_id,
        priority=priority,
        owner=owner,
        requester=None,
        waiting_on=list(waiting_on),
        due_at=due_at,
        next_action=next_action,
        dependencies=[],
        blockers=list(blockers),
        source_observations=[OBSERVATION_URI],
    )
