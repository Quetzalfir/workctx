from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from workctx.adapters.sqlite import EntityRecord, ObservationRecord, SQLiteProjection, TaskQuery
from workctx.domain import ClaimStatus, EntityType, RelationType, TaskStatus
from workctx.domain.frontmatter import parse_frontmatter
from workctx.domain.locators import LineRangeLocator

from .support import (
    ARTIFACT_REFERENCE,
    create_fictional_context,
    entity_frontmatter,
    write_markdown,
)


@pytest.mark.integration
def test_rebuild_indexes_typed_records_and_full_text(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths.values()}

    projection = SQLiteProjection(root)
    report = projection.rebuild()

    assert report.counts.documents_seen == 7
    assert report.counts.documents_indexed == 7
    assert report.counts.documents_skipped == 0
    assert report.counts.entities == 5
    assert report.counts.aliases == 3
    assert report.counts.edges == 3
    assert report.counts.backlinks == 3
    assert report.counts.observations == 1
    assert report.counts.claims == 2
    assert report.counts.tasks == 2
    assert report.counts.fts_records == 8
    assert report.metadata.context_id == "fictional-context"
    assert report.metadata.workspace_schema_version == 1
    assert len(report.metadata.source_fingerprint) == 64
    assert report.metadata.source_file_count == 7
    assert report.metadata.build_completed_at >= report.metadata.build_started_at
    assert projection.metadata() == report.metadata
    assert {path.name for path in (root / "98_state").iterdir()} == {"index.sqlite3"}
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths.values()}

    system = projection.get_entity_by_id("SYS-identity-service")
    assert system is not None
    assert system.entity_type is EntityType.SYSTEM
    assert system.aliases == ("IdP",)
    assert system.tags == ("fictional",)
    assert projection.get_entity_by_uri(system.uri) == system
    assert projection.find_entities_by_alias("IdP") == (system,)

    evidence_uri = "workctx://fictional-context/evidence/EVD-20260730-auth-flow-01"
    outbound = projection.outbound_edges(evidence_uri)
    assert len(outbound) == 1
    assert outbound[0].relation is RelationType.MENTIONS
    assert outbound[0].source_observations == (
        "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-001",
    )
    assert outbound[0] in projection.inbound_edges(outbound[0].target)
    assert (
        projection.outbound_edges(evidence_uri, relations=frozenset({RelationType.SUPPORTS})) == ()
    )

    observation = projection.get_observation("EVD-20260730-auth-flow-01#OBS-001")
    assert observation is not None
    assert str(observation.uri).endswith("%23OBS-001")
    assert isinstance(observation.locator, LineRangeLocator)
    assert observation.locator.start_line == 4
    assert projection.get_observation(observation.uri) == observation
    related = projection.outbound_edges(observation.uri)
    assert len(related) == 1
    assert related[0].relation is RelationType.SUPPORTS
    assert related[0] in projection.inbound_edges(related[0].target)
    assert projection.observations_for_parent(evidence_uri) == (observation,)

    task_edges = projection.outbound_edges("workctx://fictional-context/task/TASK-2026-001")
    all_outbound = {*outbound, *related, *task_edges}
    all_inbound = {
        *projection.inbound_edges("workctx://fictional-context/system/SYS-identity-service"),
        *projection.inbound_edges("workctx://fictional-context/person/PER-alex-rivera"),
    }
    assert all_inbound == all_outbound

    task_uri = "workctx://fictional-context/task/TASK-2026-001"
    current_claims = projection.claims_for_subject(
        task_uri, statuses=frozenset({ClaimStatus.CURRENT})
    )
    assert len(current_claims) == 1
    current_claim = current_claims[0]
    assert current_claim.object == "waiting"
    assert current_claim.supersedes == "CLM-2026-00001"
    assert current_claim.source_observations == (observation.uri,)
    assert projection.get_claim(current_claim.uri) == current_claim
    assert projection.get_document_by_uri(current_claim.uri) == current_claim
    claim_history = projection.claims_for_subject(task_uri)
    assert [claim.id for claim in claim_history] == ["CLM-2026-00002", "CLM-2026-00001"]
    assert claim_history[0].observed_at.isoformat() == "2026-07-30T19:00:00+00:00"

    waiting_tasks = projection.query_tasks(
        TaskQuery(
            statuses=frozenset({TaskStatus.WAITING}),
            waiting_on="workctx://fictional-context/person/PER-alex-rivera",
            root_task="TASK-2026-001",
        )
    )
    assert [task.id for task in waiting_tasks] == ["TASK-2026-001"]
    assert waiting_tasks[0].entity_type is EntityType.TASK
    assert waiting_tasks[0].aliases == ()
    assert waiting_tasks[0].tags == ("fictional",)
    assert waiting_tasks[0].confidence is not None
    assert waiting_tasks[0].confidence.value == "high"
    assert waiting_tasks[0].blockers == ("Vendor test response",)
    assert projection.get_task(task_uri) == waiting_tasks[0]
    assert projection.get_document_by_uri(task_uri) == waiting_tasks[0]
    subtasks = projection.query_tasks(TaskQuery(parent_task="TASK-2026-001"))
    assert [task.id for task in subtasks] == ["TASK-2026-001-ST01"]

    assert projection.search("delegates")[0].id == observation.id
    assert (
        projection.search("rollout readiness", entity_types=frozenset({EntityType.TASK}))[0].id
        == "TASK-2026-001"
    )
    assert projection.search("current waiting")[0].id == "CLM-2026-00002"
    assert projection.search("cafe")[0].id == "SYS-identity-service"
    assert (
        projection.search("identity", entity_types=frozenset({EntityType.SYSTEM}))[0].id
        == "SYS-identity-service"
    )


def test_query_inputs_are_typed_and_bounded(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    write_markdown(
        root / "02_knowledge" / "system-unicode.md",
        entity_frontmatter(
            "fictional-context",
            "SYS-unicode-token",
            "system",
            "Unicode token fixture",
        ),
        "Decomposed token x\u0301y remains searchable.",
    )
    projection = SQLiteProjection(root)
    projection.rebuild()

    assert projection.query_tasks(TaskQuery(statuses=frozenset())) == ()
    assert projection.search("identity", entity_types=frozenset()) == ()
    assert projection.search("identity_service")[0].id == "SYS-identity-service"
    assert projection.search("x\u0301y")[0].id == "SYS-unicode-token"
    with pytest.raises(ValueError, match="at least one word"):
        projection.search("---")
    with pytest.raises(ValueError, match="between 1 and 1000"):
        projection.search("identity", limit=0)


def test_temporal_provenance_and_json_fields_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")

    evidence, evidence_body = parse_frontmatter(paths["evidence"].read_text(encoding="utf-8"))
    evidence["references"][0].update(
        {
            "valid_from": "2026-07-30T08:00:00-04:00",
            "valid_to": "2026-08-01T12:00:00Z",
            "note": "Fictional temporal edge",
        }
    )
    evidence["observations"][0].update(
        {
            "observed_at": "2026-07-30T07:00:00-05:00",
            "valid_from": "2026-07-30T12:00:00Z",
            "valid_to": "2026-08-30T12:00:00Z",
            "derived_from": [ARTIFACT_REFERENCE],
        }
    )
    write_markdown(paths["evidence"], evidence, evidence_body.strip())

    claim, claim_body = parse_frontmatter(paths["claim_current"].read_text(encoding="utf-8"))
    claim["object"] = {
        "status": "waiting",
        "details": {"approved": False, "regions": ["north", None]},
    }
    claim["valid_from"] = "2026-07-30T06:00:00-06:00"
    claim["valid_to"] = "2026-09-01T12:00:00Z"
    write_markdown(paths["claim_current"], claim, claim_body.strip())

    task, task_body = parse_frontmatter(paths["task"].read_text(encoding="utf-8"))
    task["requester"] = "workctx://fictional-context/person/PER-alex-rivera"
    task["dependencies"] = ["workctx://fictional-context/task/TASK-2026-001-ST01"]
    write_markdown(paths["task"], task, task_body.strip())

    projection = SQLiteProjection(root)
    projection.rebuild()

    edge = projection.outbound_edges(
        "workctx://fictional-context/evidence/EVD-20260730-auth-flow-01"
    )[0]
    assert edge.valid_from is not None
    assert edge.valid_from.isoformat() == "2026-07-30T12:00:00+00:00"
    assert edge.valid_to is not None
    assert edge.note == "Fictional temporal edge"

    observation = projection.get_observation("EVD-20260730-auth-flow-01#OBS-001")
    assert observation is not None
    assert observation.observed_at is not None
    assert observation.observed_at.isoformat() == "2026-07-30T12:00:00+00:00"
    assert observation.valid_from is not None
    assert observation.valid_to is not None
    assert observation.derived_from == (ARTIFACT_REFERENCE,)

    current_claim = projection.get_claim("CLM-2026-00002")
    assert current_claim is not None
    assert current_claim.object == {
        "details": {"approved": False, "regions": ["north", None]},
        "status": "waiting",
    }
    assert current_claim.valid_from is not None
    assert current_claim.valid_from.isoformat() == "2026-07-30T12:00:00+00:00"
    assert current_claim.valid_to is not None

    projected_task = projection.get_task("TASK-2026-001")
    assert projected_task is not None
    assert projected_task.requester == "workctx://fictional-context/person/PER-alex-rivera"
    assert projected_task.dependencies == ("workctx://fictional-context/task/TASK-2026-001-ST01",)
    assert projected_task.source_observations == (
        "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-001",
    )


def test_standalone_observation_body_is_indexed_and_queryable(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    observation_id = "EVD-20260730-auth-flow-01#OBS-002"
    write_markdown(
        root / "02_knowledge" / "observation-standalone.md",
        {
            "id": observation_id,
            "kind": "inference",
            "statement": "The rollout sequence is independently documented.",
            "confidence": "medium",
            "source": {
                "ref": ARTIFACT_REFERENCE,
                "locator": {"type": "line_range", "start_line": 9, "end_line": 10},
            },
            "observed_at": "2026-07-30T13:00:00-06:00",
            "valid_from": None,
            "valid_to": None,
            "derived_from": [],
            "related": [],
        },
        "Standalone nebula detail retained from canonical Markdown.",
    )
    projection = SQLiteProjection(root)

    report = projection.rebuild()
    observation = projection.get_observation(observation_id)

    assert report.counts.observations == 2
    assert report.counts.fts_records == 9
    assert observation is not None
    assert observation.parent_entity_uri is None
    assert observation.body.strip() == "Standalone nebula detail retained from canonical Markdown."
    assert projection.search("nebula")[0].id == observation_id


@pytest.mark.parametrize(
    ("entity_id", "entity_type"),
    (
        ("NOTE-generic-claim", EntityType.CLAIM),
        ("OBS-generic-note", EntityType.OBSERVATION),
    ),
)
def test_generic_claim_or_observation_entity_remains_discoverable(
    tmp_path: Path,
    entity_id: str,
    entity_type: EntityType,
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    generic = entity_frontmatter(
        "fictional-context",
        entity_id,
        entity_type.value,
        f"Generic {entity_type.value} note",
    )
    write_markdown(
        root / "02_knowledge" / f"generic-{entity_type.value}.md",
        generic,
        "Generic modeled body.",
    )
    projection = SQLiteProjection(root)
    projection.rebuild()

    uri = f"workctx://fictional-context/{entity_type.value}/{entity_id}"
    record = projection.get_document_by_uri(uri)

    assert record is not None
    assert record.id == entity_id
    assert record.entity_type is entity_type


def test_document_lookup_uses_one_projection_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    transition_id = "EVD-20260730-transition-note-01#OBS-009"
    transition_uri = (
        "workctx://fictional-context/observation/EVD-20260730-transition-note-01%23OBS-009"
    )
    transition_path = root / "02_knowledge" / "transition-observation.md"
    generic = entity_frontmatter(
        "fictional-context",
        transition_id,
        "observation",
        "Generic observation before transition",
    )
    generic["uri"] = transition_uri
    write_markdown(transition_path, generic, "Old generic representation.")
    projection = SQLiteProjection(root)
    projection.rebuild()
    original_reader = projection._reader_connection
    reader_count = 0

    @contextmanager
    def swapping_reader() -> Any:
        nonlocal reader_count
        reader_count += 1
        with original_reader() as connection:
            yield connection
        if reader_count == 1:
            write_markdown(
                transition_path,
                {
                    "id": transition_id,
                    "kind": "fact",
                    "statement": "The specialized representation is now current.",
                    "confidence": "high",
                    "source": {
                        "ref": ARTIFACT_REFERENCE,
                        "locator": {
                            "type": "line_range",
                            "start_line": 1,
                            "end_line": 1,
                        },
                    },
                    "observed_at": None,
                    "valid_from": None,
                    "valid_to": None,
                    "derived_from": [],
                    "related": [],
                },
                "New specialized representation.",
            )
            projection.rebuild()

    monkeypatch.setattr(projection, "_readiness_trigger", lambda _config: None)
    monkeypatch.setattr(projection, "_reader_connection", swapping_reader)

    old_record = projection.get_document_by_uri(transition_uri)
    new_record = projection.get_document_by_uri(transition_uri)

    assert isinstance(old_record, EntityRecord)
    assert old_record.title == "Generic observation before transition"
    assert isinstance(new_record, ObservationRecord)
    assert new_record.statement == "The specialized representation is now current."
