"""Coverage for the exhaustive typed entity query added for generated views."""

from __future__ import annotations

from pathlib import Path

from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import EntityType

from .support import create_fictional_context


def test_query_entities_filters_by_type_and_orders_deterministically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    projection.rebuild()

    everything = projection.query_entities()
    assert len(everything) == 5
    ordering = [(record.entity_type.value, record.id) for record in everything]
    assert ordering == sorted(ordering)

    systems = projection.query_entities(entity_types=frozenset({EntityType.SYSTEM}))
    assert systems
    assert {record.entity_type for record in systems} == {EntityType.SYSTEM}
    assert all(record.body for record in systems)
    assert all(record.source_path for record in systems)

    assert projection.query_entities(entity_types=frozenset()) == ()
