from __future__ import annotations

import json

import pytest

from workctx.domain import Topic, TopicHeat, TopicHeatBucket, TopicStatus
from workctx.graph.errors import GraphExportError
from workctx.graph.service import (
    _GraphItem,
    _reference_map_files,
    _require_metadata_secret_free,
    _topic_map_files,
    _topic_note_names,
)

from .support import NOW

REVISION = "0" * 64


def _topics() -> dict[str, Topic]:
    return {
        slug: Topic(
            slug=slug,
            name=name,
            scope=f"Fictional {name.casefold()} workstream.",
            aliases=[],
            status=TopicStatus.ACTIVE,
            created_at=NOW,
        )
        for slug, name in (("alpha", "Alpha"), ("beta", "Beta"))
    }


def _heat() -> dict[str, TopicHeat]:
    return {
        slug: TopicHeat(
            slug=slug,
            name=topic.name,
            score=0.5,
            heat=TopicHeatBucket.COOL,
            last_activity=NOW,
        )
        for slug, topic in _topics().items()
    }


def test_topic_rendering_is_deterministic_and_contains_only_topic_edges() -> None:
    supports = {"alpha": {"shared"}, "beta": {"shared"}}

    first = _topic_map_files(("alpha", "beta"), _topics(), supports, _heat(), REVISION)
    second = _topic_map_files(("alpha", "beta"), _topics(), supports, _heat(), REVISION)

    assert first == second
    alpha = first["alpha.md"].decode("utf-8")
    assert "tags: [heat/cool]" in alpha
    assert "[[beta|Beta]] — 1 shared item" in alpha
    assert "shared.md" not in alpha
    graph_config = json.loads(first[".obsidian/graph.json"])
    assert [group["query"] for group in graph_config["colorGroups"]] == [
        "tag:#heat/hot",
        "tag:#heat/warm",
        "tag:#heat/cool",
        "tag:#heat/cold",
    ]
    assert all(set(group["color"]) == {"a", "rgb"} for group in graph_config["colorGroups"])


def test_reference_rendering_emits_one_metadata_only_stub() -> None:
    item = _GraphItem(
        entity_id="EVD-shared",
        entity_type="evidence",
        relative_path="02_knowledge/EVD-shared.md",
        title="Shared evidence",
        uri="workctx://graph-test/evidence/EVD-shared",
    )
    supports = {"alpha": {item.uri}, "beta": {item.uri}}

    files, item_uris = _reference_map_files(
        ("alpha", "beta"),
        _topics(),
        supports,
        _heat(),
        {item.uri: item},
        REVISION,
    )

    assert item_uris == (item.uri,)
    stubs = [
        content.decode("utf-8") for name, content in files.items() if name.startswith("items/")
    ]
    assert len(stubs) == 1
    stub = stubs[0]
    for marker in (
        'title: "Shared evidence"',
        'type: "evidence"',
        'uri: "workctx://graph-test/evidence/EVD-shared"',
        'path: "02_knowledge/EVD-shared.md"',
    ):
        assert marker in stub
    assert "document body" not in stub


def test_secret_guard_and_windows_reserved_topic_names_fail_safe() -> None:
    with pytest.raises(GraphExportError, match="possible secret"):
        _require_metadata_secret_free(('api_key = "sk-fictional-1234567890abcdef"',))

    names = _topic_note_names(("con", "ordinary"))
    assert names["ordinary"] == "ordinary"
    assert names["con"].startswith("topic-con-")
    assert names["con"].casefold() != "con"
