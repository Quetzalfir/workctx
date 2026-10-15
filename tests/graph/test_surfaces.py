from __future__ import annotations

from pathlib import Path

import pytest

from workctx.guide import GUIDE, OwnershipClass

ROOT = Path(__file__).parents[2]
VISUAL_MAP_SENTENCE = (
    "When the operator asks for a visual map, run `workctx graph export` and treat both "
    "generated Obsidian vaults under `04_views/graph/` as read-only."
)
BRIDGES_AND_TEMPLATES = (
    "src/workctx/resources/agent_kit/bridges/AGENTS.md",
    "src/workctx/resources/agent_kit/bridges/CLAUDE.md",
    "src/workctx/resources/agent_kit/bridges/GEMINI.md",
    "src/workctx/resources/context_template/AGENTS.md",
    "templates/context/AGENTS.md",
)


@pytest.mark.parametrize("relative_path", BRIDGES_AND_TEMPLATES)
def test_visual_map_instruction_ships_exactly_once(relative_path: str) -> None:
    content = (ROOT / relative_path).read_text(encoding="utf-8")

    assert content.count(VISUAL_MAP_SENTENCE) == 1, relative_path


def test_guide_marks_graph_export_as_separately_generated() -> None:
    entry = next(item for item in GUIDE.ownership if item.path == "04_views/graph/")

    assert entry.ownership_class is OwnershipClass.GENERATED
    assert entry.regenerate_with == ("workctx graph export",)


def test_obsidian_guide_and_cli_reference_cover_the_public_contract() -> None:
    guide = (ROOT / "docs/guides/obsidian.md").read_text(encoding="utf-8")
    normalized_guide = " ".join(guide.split())
    cli_reference = (ROOT / "docs/reference/cli-envelope.md").read_text(encoding="utf-8")

    for marker in (
        "Canonical relationships use `workctx://` URIs",
        "`04_views/graph/topic-map/`",
        "`04_views/graph/reference-map/`",
        "never a document body or secret value",
        "Obsidian sizes nodes by link count",
        "workctx obsidian install --yes",
        "workctx obsidian open --map topics",
        "Everything under `04_views/graph/` is generated and read-only",
    ):
        assert marker in normalized_guide
    for command in ("`graph export`", "`obsidian install`", "`obsidian open`"):
        assert command in cli_reference
