"""Topic workflow instructions must ship on every contracted agent surface."""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

TOPIC_PREFIX = "Resolve durable subjects with `workctx topic list` and `workctx topic review`;"
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

    assert content.count(TOPIC_PREFIX) == 1, relative_path
    for marker in (
        "`workctx topic heat`",
        "`04_views/topics.md`",
        "prioritize hot topics",
        "every finding from each reviewed thread, channel, meeting, or source",
        "same session as evidence or events under the relevant topics",
        "`workctx checkpoint check <id>` when a session checkpoint exists",
        "surface yellow or red items first",
        "`workctx checkpoint save` for the canonical items relied on without secret values",
    ):
        assert marker in content, (relative_path, marker)


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


def test_process_evidence_prioritizes_heat_and_reconciles_every_reviewed_source() -> None:
    content = (ROOT / TOPIC_SKILLS[0]).read_text(encoding="utf-8")

    for marker in (
        "`workctx topic heat`",
        "prioritize hot topics during triage",
        "every finding from each reviewed thread, channel, meeting, or source",
        "same session as evidence or events under the relevant topics",
    ):
        assert marker in content


def test_session_skills_check_and_save_content_free_checkpoints() -> None:
    bootstrap = (
        ROOT / "src/workctx/resources/agent_kit/skills/bootstrap-session/SKILL.md"
    ).read_text(encoding="utf-8")
    close = (ROOT / "src/workctx/resources/agent_kit/skills/close-session/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`workctx checkpoint list`" in bootstrap
    assert "`workctx checkpoint check <checkpoint-id>`" in bootstrap
    assert "surface yellow or red items before normal work" in bootstrap
    assert "`workctx checkpoint save <label> --ref <workctx-uri> ... --yes`" in close
    assert "never secret values" in close
