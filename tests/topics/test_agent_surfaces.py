"""Topic workflow instructions must ship on every contracted agent surface."""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

TOPIC_SENTENCE = (
    "Resolve durable subjects with `workctx topic list` and `workctx topic review`; "
    "reuse an existing topic or alias, and use `workctx topic add` only for a durable "
    "account, initiative, system, or workstream, never for a test run, variant, date, "
    "or document."
)
BRIDGES_AND_TEMPLATES = (
    "src/workctx/resources/agent_kit/bridges/AGENTS.md",
    "src/workctx/resources/agent_kit/bridges/CLAUDE.md",
    "src/workctx/resources/agent_kit/bridges/GEMINI.md",
    "src/workctx/resources/context_template/AGENTS.md",
    "templates/context/AGENTS.md",
)
TOPIC_SKILLS = (
    "src/workctx/resources/agent_kit/skills/process-evidence/SKILL.md",
    "src/workctx/resources/agent_kit/skills/curate-knowledge/SKILL.md",
    "src/workctx/resources/agent_kit/skills/manage-tasks/SKILL.md",
    "src/workctx/resources/agent_kit/skills/draft-replies/SKILL.md",
)


@pytest.mark.parametrize("relative_path", BRIDGES_AND_TEMPLATES)
def test_bridge_and_template_topic_instruction_is_exactly_once(relative_path: str) -> None:
    content = (ROOT / relative_path).read_text(encoding="utf-8")

    assert content.count(TOPIC_SENTENCE) == 1, relative_path


@pytest.mark.parametrize("relative_path", TOPIC_SKILLS)
def test_named_skill_includes_the_topic_resolution_step(relative_path: str) -> None:
    content = (ROOT / relative_path).read_text(encoding="utf-8")

    for marker in (
        "`workctx topic list`",
        "`workctx topic review`",
        "reuse an existing topic or alias",
        "`workctx topic add` only for a durable",
        "never for a test run, variant, date, or document",
    ):
        assert marker in content, (relative_path, marker)
    assert content.count("Topic step:") == 1, relative_path
