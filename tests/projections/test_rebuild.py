from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workctx.adapters.sqlite import (
    PROJECTION_SCHEMA_VERSION,
    ContextIsolationError,
    RebuildTrigger,
    SkipReason,
    SQLiteProjection,
)
from workctx.domain.frontmatter import parse_frontmatter

from .support import (
    create_fictional_context,
    entity_frontmatter,
    rewrite_entity,
    write_markdown,
)


def _snapshot(projection: SQLiteProjection) -> tuple[object, ...]:
    task_uri = "workctx://fictional-context/task/TASK-2026-001"
    evidence_uri = "workctx://fictional-context/evidence/EVD-20260730-auth-flow-01"
    system_uri = "workctx://fictional-context/system/SYS-identity-service"
    return (
        projection.get_entity_by_uri(system_uri),
        projection.find_entities_by_alias("IdP"),
        projection.outbound_edges(evidence_uri),
        projection.inbound_edges(system_uri),
        projection.get_observation("EVD-20260730-auth-flow-01#OBS-001"),
        projection.claims_for_subject(task_uri),
        projection.query_tasks(),
        projection.search("authentication"),
    )


def test_empty_canonical_zones_build_a_queryable_projection(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    for path in paths.values():
        path.unlink()

    projection = SQLiteProjection(root)
    report = projection.rebuild()

    assert report.counts.documents_seen == 0
    assert report.counts.documents_indexed == 0
    assert report.counts.fts_records == 0
    assert projection.query_tasks() == ()
    assert projection.search("anything") == ()


def test_projection_metadata_normalizes_context_timestamp_to_utc(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(
        root,
        "fictional-context",
        context_timestamp="2026-07-30T06:00:00-06:00",
    )
    projection = SQLiteProjection(root)

    report = projection.rebuild()

    assert report.metadata.context_updated_at.isoformat() == "2026-07-30T12:00:00+00:00"
    assert projection.metadata() == report.metadata


@pytest.mark.acceptance
def test_rebuild_is_equivalent_twice_and_after_database_deletion(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)

    first = projection.rebuild()
    first_snapshot = _snapshot(projection)
    assert projection.ensure_ready() is None
    second = projection.rebuild()
    second_snapshot = _snapshot(projection)

    assert first.metadata.source_fingerprint == second.metadata.source_fingerprint
    assert first.counts == second.counts
    assert first_snapshot == second_snapshot

    projection.database_path.unlink()
    missing_report = projection.ensure_ready()
    assert missing_report is not None
    assert missing_report.trigger is RebuildTrigger.MISSING
    assert missing_report.counts == first.counts
    assert _snapshot(projection) == first_snapshot


@pytest.mark.acceptance
def test_projection_version_mismatch_performs_full_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    original = projection.rebuild()
    rewrite_entity(
        paths["system"],
        title="Revised Identity Service",
        aliases=["Revised IdP"],
    )
    connection = sqlite3.connect(projection.database_path)
    try:
        connection.execute(
            "UPDATE projection_metadata SET projection_schema_version = 0 WHERE singleton = 1"
        )
        connection.execute(
            """
            INSERT INTO aliases(context_id, entity_id, position, alias)
            VALUES ('fictional-context', 'SYS-identity-service', 99, 'rogue-alias')
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = projection.ensure_ready()

    assert report is not None
    assert report.trigger is RebuildTrigger.VERSION_MISMATCH
    assert report.metadata.projection_schema_version == PROJECTION_SCHEMA_VERSION
    assert report.metadata.source_fingerprint != original.metadata.source_fingerprint
    revised = projection.get_entity_by_id("SYS-identity-service")
    assert revised is not None
    assert revised.title == "Revised Identity Service"
    assert revised.aliases == ("Revised IdP",)
    assert projection.find_entities_by_alias("IdP") == ()
    assert projection.find_entities_by_alias("rogue-alias") == ()


def test_workspace_version_mismatch_performs_full_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    projection.rebuild()
    connection = sqlite3.connect(projection.database_path)
    try:
        connection.execute(
            "UPDATE projection_metadata SET workspace_schema_version = 0 WHERE singleton = 1"
        )
        connection.commit()
    finally:
        connection.close()

    report = projection.ensure_ready()

    assert report is not None
    assert report.trigger is RebuildTrigger.WORKSPACE_VERSION_MISMATCH
    assert report.metadata.workspace_schema_version == 1


def test_incompatible_database_is_rebuilt_from_canonical_files(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    projection.database_path.write_bytes(b"fictional incompatible database")

    report = projection.ensure_ready()

    assert report is not None
    assert report.trigger is RebuildTrigger.INCOMPATIBLE_DATABASE
    assert projection.get_entity_by_id("SYS-identity-service") is not None


@pytest.mark.parametrize(
    "damage_sql",
    (
        "DROP TABLE search_fts",
        "DROP TRIGGER guard_entities_context_insert",
    ),
)
def test_missing_required_schema_object_triggers_full_rebuild(
    tmp_path: Path,
    damage_sql: str,
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    projection.rebuild()
    connection = sqlite3.connect(projection.database_path)
    try:
        connection.execute(damage_sql)
        connection.commit()
    finally:
        connection.close()

    report = projection.ensure_ready()

    assert report is not None
    assert report.trigger is RebuildTrigger.INCOMPATIBLE_DATABASE
    assert projection.search("identity")[0].id == "SYS-identity-service"


def test_malformed_document_is_sanitized_and_skipped(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    secret = "fictional-secret-that-must-not-appear"
    write_markdown(
        root / "02_knowledge" / "broken.md",
        {
            "schema_version": 1,
            "id": "SYS-broken",
            "entity_type": "system",
            "title": secret,
            "uri": "workctx://fictional-context/system/SYS-wrong-id",
            "aliases": [],
            "references": [],
            "created_at": "not-a-date",
            "updated_at": "not-a-date",
        },
        secret,
    )
    projection = SQLiteProjection(root)

    report = projection.rebuild()

    assert report.counts.documents_seen == 8
    assert report.counts.documents_indexed == 7
    assert report.counts.documents_skipped == 1
    assert projection.get_entity_by_id("SYS-identity-service") is not None
    assert projection.get_entity_by_id("SYS-broken") is None
    skipped = report.skipped_documents[0]
    assert skipped.path == "02_knowledge/broken.md"
    assert skipped.reason is SkipReason.VALIDATION_ERROR
    assert secret not in skipped.message


def test_orphan_subtask_is_reported_without_losing_valid_tasks(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    orphan = {
        "schema_version": 1,
        "id": "TASK-2026-099-ST01",
        "entity_type": "task",
        "title": "Fictional orphan task",
        "uri": "workctx://fictional-context/task/TASK-2026-099-ST01",
        "aliases": [],
        "status": "active",
        "confidence": "high",
        "tags": [],
        "references": [],
        "created_at": "2026-07-30T12:00:00Z",
        "updated_at": "2026-07-30T12:00:00Z",
        "task_type": "subtask",
        "parent_task": "TASK-2026-099",
        "root_task": "TASK-2026-099",
        "priority": "P2",
        "owner": None,
        "requester": None,
        "waiting_on": [],
        "due_at": None,
        "next_action": "Create its missing fictional parent.",
        "dependencies": [],
        "blockers": [],
        "source_observations": [],
    }
    write_markdown(root / "03_work" / "task-orphan.md", orphan, "Orphan fixture.")

    report = SQLiteProjection(root).rebuild()

    assert any(item.reason is SkipReason.TASK_HIERARCHY for item in report.skipped_documents)
    projection = SQLiteProjection(root)
    assert projection.get_task("TASK-2026-099-ST01") is None
    assert projection.get_task("TASK-2026-001") is not None


def test_rebuild_excludes_noncanonical_zones(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    excluded_zones = (
        "00_inbox",
        "01_processed",
        "04_views",
        "05_outbox",
        "97_integrations",
        "99_meta/templates",
    )
    for position, zone in enumerate(excluded_zones):
        path = root / zone / f"excluded-{position}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(
            path,
            entity_frontmatter(
                "fictional-context",
                f"SYS-excluded-{position}",
                "system",
                f"Excluded quasar {position}",
            ),
            "This excluded-zone quasar must never enter the projection.",
        )

    projection = SQLiteProjection(root)
    report = projection.rebuild()

    assert report.counts.documents_seen == 7
    assert report.counts.documents_indexed == 7
    assert projection.search("quasar") == ()
    assert projection.get_entity_by_id("SYS-excluded-0") is None


def test_cross_zone_document_symlink_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    raw_path = root / "00_inbox" / "raw-system.md"
    raw_path.parent.mkdir()
    write_markdown(
        raw_path,
        entity_frontmatter(
            "fictional-context",
            "SYS-cross-zone",
            "system",
            "Cross-zone source",
        ),
        "Excluded raw evidence.",
    )
    link_path = root / "02_knowledge" / "linked-raw.md"
    try:
        link_path.symlink_to(raw_path)
    except OSError:
        pytest.skip("Symbolic links are unavailable for this test user")

    projection = SQLiteProjection(root)
    report = projection.rebuild()

    assert projection.get_entity_by_id("SYS-cross-zone") is None
    assert any(
        item.path == "02_knowledge/linked-raw.md" and item.reason is SkipReason.PATH_ESCAPE
        for item in report.skipped_documents
    )


def test_canonical_zone_symlink_to_excluded_zone_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    knowledge = root / "02_knowledge"
    retained = root / "retained-knowledge"
    inbox = root / "00_inbox"
    inbox.mkdir()
    knowledge.rename(retained)
    try:
        knowledge.symlink_to(inbox, target_is_directory=True)
    except OSError:
        retained.rename(knowledge)
        pytest.skip("Symbolic links are unavailable for this test user")

    with pytest.raises(ContextIsolationError, match="Canonical zones"):
        SQLiteProjection(root).rebuild()


def test_invalid_frontmatter_delimiters_are_classified_and_sanitized(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    source_secret = "fictional-delimiter-secret"
    (root / "02_knowledge" / "missing-delimiters.md").write_text(
        f"schema_version: 1\ntitle: {source_secret}\n",
        encoding="utf-8",
    )

    report = SQLiteProjection(root).rebuild()
    skipped = next(
        item for item in report.skipped_documents if item.path.endswith("missing-delimiters.md")
    )

    assert skipped.reason is SkipReason.FRONTMATTER_ERROR
    assert source_secret not in skipped.message


def test_machine_specific_file_reference_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    entity = entity_frontmatter(
        "fictional-context",
        "SYS-machine-path",
        "system",
        "Machine-specific source",
    )
    entity["references"] = [
        {
            "relation": "mentions",
            "target": "file:///C:/fictional/private.txt",
            "source_observations": [],
        }
    ]
    write_markdown(
        root / "02_knowledge" / "machine-reference.md",
        entity,
        "Invalid durable reference fixture.",
    )

    projection = SQLiteProjection(root)
    report = projection.rebuild()

    assert projection.get_entity_by_id("SYS-machine-path") is None
    assert any(
        item.path.endswith("machine-reference.md") and item.reason is SkipReason.VALIDATION_ERROR
        for item in report.skipped_documents
    )


@pytest.mark.parametrize("failure", ("invalid_nested", "wrong_evidence_owner"))
def test_invalid_embedded_observation_skips_its_whole_document(
    tmp_path: Path,
    failure: str,
) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    raw, body = parse_frontmatter(paths["evidence"].read_text(encoding="utf-8"))
    if failure == "invalid_nested":
        raw["observations"][0]["statement"] = ""
    else:
        raw["observations"][0]["id"] = "EVD-20260730-other-flow-01#OBS-001"
    write_markdown(paths["evidence"], raw, body.strip())
    projection = SQLiteProjection(root)

    report = projection.rebuild()

    assert projection.get_entity_by_id("EVD-20260730-auth-flow-01") is None
    assert projection.get_observation("EVD-20260730-auth-flow-01#OBS-001") is None
    assert any(
        item.path == "02_knowledge/evidence-auth-flow.md"
        and item.reason is SkipReason.VALIDATION_ERROR
        for item in report.skipped_documents
    )


def test_duplicate_identity_is_reported_without_partial_indexing(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    duplicate, _ = parse_frontmatter(paths["system"].read_text(encoding="utf-8"))
    duplicate["title"] = "Duplicate identity title"
    write_markdown(
        root / "02_knowledge" / "z-duplicate-system.md",
        duplicate,
        "Duplicate body must not be indexed.",
    )
    projection = SQLiteProjection(root)

    report = projection.rebuild()
    system = projection.get_entity_by_id("SYS-identity-service")

    assert system is not None
    assert system.title == "Identity Service"
    assert projection.search("Duplicate body") == ()
    assert any(
        item.path == "02_knowledge/z-duplicate-system.md"
        and item.reason is SkipReason.DUPLICATE_IDENTITY
        for item in report.skipped_documents
    )
