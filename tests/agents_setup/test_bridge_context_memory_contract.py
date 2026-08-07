from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PROMPT_SURFACES = (
    "src/workctx/resources/agent_kit/bridges/AGENTS.md",
    "src/workctx/resources/agent_kit/bridges/CLAUDE.md",
    "src/workctx/resources/agent_kit/bridges/GEMINI.md",
    "src/workctx/resources/context_template/AGENTS.md",
    "templates/context/AGENTS.md",
)
ORIENTATION_MARKERS = (
    "`context.yaml` policies",
    "`04_views/people-directory.md`",
    "`04_views/resource-directory.md`",
    "`04_views/glossary.md`",
    "`04_views/current-focus.md`",
    '`workctx search "<topic>"`',
    "`90_integrations/`",
    "`workctx secret list`",
    "`workctx connector list`",
    "relevant entities",
    "`workctx ref`",
    "Asking the operator something the context already answers is a protocol violation.",
)
RETENTION_MARKERS = (
    "normal approval-gated proposal flow",
    "a person fact to a person entity",
    "an access or process fact to an integration entity",
    "or a system entity",
    "a standing preference to a suggested context `instructions.md` addition",
    "Did the operator repeat or newly supply any fact?",
    "it must be recorded before closing.",
)


@pytest.mark.parametrize("relative_path", PROMPT_SURFACES)
def test_prompt_surface_orients_before_asking_and_records_new_facts(
    relative_path: str,
) -> None:
    content = (ROOT / relative_path).read_text(encoding="utf-8")

    for marker in (*ORIENTATION_MARKERS, *RETENTION_MARKERS):
        assert marker in content, (relative_path, marker)
    assert content.count("workctx secret list") == 1
    assert content.count("protocol violation") == 1


def test_bootstrap_session_orients_before_asking_and_records_new_facts() -> None:
    path = ROOT / "src/workctx/resources/agent_kit/skills/bootstrap-session/SKILL.md"
    content = path.read_text(encoding="utf-8")

    for marker in (*ORIENTATION_MARKERS, *RETENTION_MARKERS):
        assert marker in content, marker
    assert "Reference secret names only, never values." in content
