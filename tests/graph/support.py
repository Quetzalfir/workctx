from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from workctx.domain import Topic, TopicRegistry, TopicStatus
from workctx.services.contexts import initialize_context

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def write_markdown(path: Path, payload: dict[str, Any], body: str) -> None:
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{rendered}---\n\n{body}\n", encoding="utf-8")


def entity(
    identifier: str,
    title: str,
    entity_type: str = "project",
    *,
    topics: list[str] | None = None,
    references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": identifier,
        "entity_type": entity_type,
        "title": title,
        "uri": f"workctx://graph-test/{entity_type}/{identifier}",
        "aliases": [],
        "status": "active",
        "confidence": "high",
        "tags": [],
        "topics": [] if topics is None else topics,
        "references": [] if references is None else references,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


def graph_context(root: Path) -> Path:
    initialize_context(root, name="Graph Test", context_id="graph-test")
    registry = TopicRegistry(
        schema_version=1,
        topics=[
            Topic(
                slug="alpha",
                name="Alpha",
                scope="Fictional alpha workstream.",
                aliases=["alpha-team"],
                status=TopicStatus.ACTIVE,
                created_at=NOW,
            ),
            Topic(
                slug="beta",
                name="Beta",
                scope="Fictional beta workstream.",
                aliases=[],
                status=TopicStatus.ACTIVE,
                created_at=NOW,
            ),
            Topic(
                slug="gamma",
                name="Gamma",
                scope="Unconnected fictional workstream.",
                aliases=[],
                status=TopicStatus.ACTIVE,
                created_at=NOW,
            ),
        ],
    )
    (root / "99_meta" / "topics.yaml").write_text(
        yaml.safe_dump(registry.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    shared_uri = "workctx://graph-test/evidence/EVD-shared"
    write_markdown(
        root / "02_knowledge" / "EVD-shared.md",
        entity("EVD-shared", "Shared evidence", "evidence"),
        'Document body only: api_key = "sk-fictional-1234567890abcdef"',
    )
    write_markdown(
        root / "02_knowledge" / "PRJ-alpha.md",
        entity(
            "PRJ-alpha",
            "Alpha project",
            topics=["alpha-team"],
            references=[{"relation": "evidenced_by", "target": shared_uri}],
        ),
        "Alpha body must never enter the graph export.",
    )
    write_markdown(
        root / "02_knowledge" / "PRJ-beta.md",
        entity(
            "PRJ-beta",
            "Beta project",
            topics=["beta"],
            references=[{"relation": "evidenced_by", "target": shared_uri}],
        ),
        "Beta body must never enter the graph export.",
    )
    write_markdown(
        root / "02_knowledge" / "PRJ-gamma.md",
        entity("PRJ-gamma", "Gamma project", topics=["gamma"]),
        "Gamma body must never enter the graph export.",
    )
    return root
