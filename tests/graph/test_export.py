from __future__ import annotations

import json
from pathlib import Path

from workctx.graph import GraphMap, export_graph
from workctx.services.contexts import initialize_context

from .support import NOW, graph_context


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_export_is_deterministic_and_builds_both_graph_semantics(tmp_path: Path) -> None:
    root = graph_context(tmp_path / "context")

    result = export_graph(root, clock=lambda: NOW)
    graph_root = root / "04_views" / "graph"
    first = _tree_bytes(graph_root)

    assert result.context_id == "graph-test"
    assert result.source_revision == "0" * 64
    assert result.topics == ("alpha", "beta", "gamma")
    assert [vault.map for vault in result.vaults] == [
        GraphMap.TOPICS,
        GraphMap.REFERENCES,
    ]
    alpha_topic = (graph_root / "topic-map" / "alpha.md").read_text(encoding="utf-8")
    assert "generator: workctx graph export" in alpha_topic
    assert f"source_revision: {result.source_revision}" in alpha_topic
    assert "tags: [heat/cold]" in alpha_topic
    assert "[[beta|Beta]] — 1 shared item" in alpha_topic
    assert "[[gamma" not in alpha_topic
    assert "EVD-shared" not in alpha_topic

    alpha_reference = (graph_root / "reference-map" / "alpha.md").read_text(encoding="utf-8")
    assert "## Supporting items" in alpha_reference
    assert "Alpha project" in alpha_reference
    assert "Shared evidence" in alpha_reference
    stubs = tuple((graph_root / "reference-map" / "items").glob("*.md"))
    assert len(stubs) == 4
    shared_stub = next(
        path.read_text(encoding="utf-8")
        for path in stubs
        if "workctx://graph-test/evidence/EVD-shared" in path.read_text(encoding="utf-8")
    )
    assert 'title: "Shared evidence"' in shared_stub
    assert 'type: "evidence"' in shared_stub
    assert 'path: "02_knowledge/EVD-shared.md"' in shared_stub

    all_exported_text = b"\n".join(first.values()).decode("utf-8")
    for forbidden in (
        "sk-fictional-1234567890abcdef",
        "Alpha body must never enter",
        "Beta body must never enter",
        "Gamma body must never enter",
        "Document body only",
    ):
        assert forbidden not in all_exported_text

    for map_directory in ("topic-map", "reference-map"):
        graph_config = json.loads(
            (graph_root / map_directory / ".obsidian" / "graph.json").read_text(encoding="utf-8")
        )
        assert graph_config == {
            "colorGroups": [
                {"query": "tag:#heat/hot", "color": {"a": 1, "rgb": 0xFF0000}},
                {"query": "tag:#heat/warm", "color": {"a": 1, "rgb": 0xFFA500}},
                {"query": "tag:#heat/cool", "color": {"a": 1, "rgb": 0x0000FF}},
                {"query": "tag:#heat/cold", "color": {"a": 1, "rgb": 0x800080}},
            ]
        }

    stale = graph_root / "topic-map" / "stale.md"
    stale.write_text("stale", encoding="utf-8")
    (graph_root / "topic-map" / "alpha.md").write_text("edited", encoding="utf-8")

    repeated = export_graph(root, clock=lambda: NOW)

    assert repeated == result
    assert _tree_bytes(graph_root) == first
    assert not stale.exists()


def test_topic_filter_resolves_alias_and_exports_one_hop_neighborhood(tmp_path: Path) -> None:
    root = graph_context(tmp_path / "context")

    result = export_graph(root, topic="alpha-team", clock=lambda: NOW)
    graph_root = root / "04_views" / "graph"

    assert result.selected_topic == "alpha"
    assert result.topics == ("alpha", "beta")
    assert not (graph_root / "topic-map" / "gamma.md").exists()
    assert not (graph_root / "reference-map" / "gamma.md").exists()
    exported = _tree_bytes(graph_root)
    assert b"PRJ-gamma" not in b"\n".join(exported.values())


def test_empty_context_still_creates_two_valid_vaults(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    initialize_context(root, name="Empty Graph", context_id="empty-graph")

    result = export_graph(root, clock=lambda: NOW)

    assert result.topics == ()
    for vault in result.vaults:
        path = root / Path(vault.path)
        assert path.is_dir()
        assert vault.topic_count == 0
        assert (path / ".obsidian" / "app.json").is_file()
        assert (path / ".obsidian" / "graph.json").is_file()
        assert not tuple(path.glob("*.md"))
