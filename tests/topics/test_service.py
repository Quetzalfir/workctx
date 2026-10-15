from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain.frontmatter import parse_frontmatter
from workctx.services.contexts import initialize_context
from workctx.topics import TopicDuplicateError, TopicMutationError, TopicService, load_registry
from workctx.transactions import read_audit_events, verify_ledger

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _service(root: Path) -> TopicService:
    return TopicService(root, clock=lambda: NOW)


def _write_project(root: Path, identifier: str, topics: list[str]) -> Path:
    payload = {
        "schema_version": 1,
        "id": identifier,
        "entity_type": "project",
        "title": identifier,
        "uri": f"workctx://topic-service/project/{identifier}",
        "aliases": [],
        "status": "active",
        "confidence": "high",
        "tags": [],
        "topics": topics,
        "references": [],
        "created_at": "2026-10-14T10:00:00Z",
        "updated_at": "2026-10-14T10:00:00Z",
    }
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    path = root / "02_knowledge" / f"{identifier}.md"
    path.write_text(f"---\n{rendered}---\n\nFixture.\n", encoding="utf-8")
    return path


def test_add_preview_is_non_mutating_and_apply_is_audited(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Service", context_id="topic-service")
    service = _service(root)

    preview = service.add("Banorte", scope="Fictional account.")

    assert preview.applied is False
    assert preview.preview is not None and preview.preview.valid
    assert not (root / "99_meta" / "topics.yaml").exists()
    assert verify_ledger(root).event_count == 0

    applied = service.add("Banorte", scope="Fictional account.", approved=True)

    assert applied.applied is True
    assert load_registry(root).registry.get("banorte") is not None
    events = read_audit_events(root)
    assert len(events) == 1
    assert [operation.target for operation in events[0].operations] == ["99_meta/topics.yaml"]


def test_duplicate_guard_refuses_and_distinct_override_is_recorded(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Service", context_id="topic-service")
    service = _service(root)
    service.add("Banorte", scope="Fictional account.", approved=True)

    with pytest.raises(TopicDuplicateError) as captured:
        service.add("Banorte Test 2", scope="A forbidden test-run topic.", approved=True)

    assert "Banorte (banorte)" in str(captured.value)
    assert "Reuse the existing topic or add the spelling as an alias" in str(captured.value)
    assert "--distinct-from banorte --reason <text>" in str(captured.value)
    assert len(load_registry(root).registry.topics) == 1

    created = service.add(
        "Banorte Migration",
        scope="A separately governed migration workstream.",
        distinct_from="banorte",
        reason="Separate owners, budget, and production deliverable.",
        approved=True,
    )

    assert created.topic.distinct_from[0].slug == "banorte"
    assert "Separate owners" in created.topic.distinct_from[0].reason


def test_instance_only_topic_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Service", context_id="topic-service")

    with pytest.raises(TopicMutationError, match="durable subject"):
        _service(root).add("Test 2", scope="Not a durable subject.")

    with pytest.raises(TopicMutationError, match="durable subject"):
        _service(root).add(
            "Prueba 2026-10-14",
            slug="unrelated-durable-looking-slug",
            scope="An explicit slug must not bypass the subject guard.",
        )


def test_alias_noop_and_merge_retag_share_one_atomic_audit_event(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Service", context_id="topic-service")
    service = _service(root)
    service.add("Banorte", scope="Fictional account.", approved=True)
    service.add(
        "Banorte Sandbox",
        scope="Separate long-lived training environment.",
        distinct_from="banorte",
        reason="Permanent separately owned environment.",
        approved=True,
    )
    alias = service.alias("banorte-sandbox", "Sandbox Banorte", approved=True)
    assert alias.applied is True
    noop = service.alias("banorte-sandbox", "Sandbox Banorte", approved=True)
    assert noop.changed is False
    assert noop.receipt is None

    project_path = _write_project(root, "PRJ-banorte-sandbox", ["banorte-sandbox"])
    SQLiteProjection(root).rebuild()
    events_before = len(read_audit_events(root))

    merged = service.merge("banorte-sandbox", "banorte", approved=True)

    assert merged.applied is True
    registry = load_registry(root).registry
    source = registry.get("banorte-sandbox")
    target = registry.get("banorte")
    assert source is not None and source.status.value == "merged"
    assert source.merged_into == "banorte"
    assert target is not None
    assert "banorte-sandbox" in target.aliases
    assert "Sandbox Banorte" in target.aliases
    frontmatter, _body = parse_frontmatter(project_path.read_text(encoding="utf-8"))
    assert frontmatter["topics"] == ["banorte"]

    events = read_audit_events(root)
    assert len(events) == events_before + 1
    merge_event = events[-1]
    assert [operation.target for operation in merge_event.operations] == [
        "99_meta/topics.yaml",
        "02_knowledge/PRJ-banorte-sandbox.md",
    ]
    assert merge_event.result == "committed"
    assert verify_ledger(root).head_hash == merge_event.event_hash


def test_review_combines_guard_similarity_and_document_jaccard(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Service", context_id="topic-service")
    service = _service(root)
    service.add("Banorte", scope="Fictional account.", approved=True)
    service.add(
        "Banorte Migration",
        scope="Separate migration.",
        distinct_from="banorte",
        reason="Reviewed as separately governed.",
        approved=True,
    )
    service.add("Payments", scope="Payments domain.", approved=True)
    service.add("Risk", scope="Risk domain.", approved=True)
    _write_project(root, "PRJ-shared", ["payments", "risk"])

    candidates = service.review()

    pairs = {(candidate.left_slug, candidate.right_slug): candidate for candidate in candidates}
    assert ("banorte", "banorte-migration") not in pairs
    shared = pairs[("payments", "risk")]
    assert shared.name_rule is None
    assert shared.shared_documents == 1
    assert shared.union_documents == 1
    assert shared.co_reference_jaccard == 1.0
