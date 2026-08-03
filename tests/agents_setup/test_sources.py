from __future__ import annotations

from pathlib import Path

import pytest

from workctx.adapters.agents import sources
from workctx.adapters.agents.errors import InvalidAdapterStateError
from workctx.adapters.agents.manifest import source_set_aggregate_hash
from workctx.adapters.agents.models import AgentClient, SourceOrigin
from workctx.adapters.agents.renderers import content_hash
from workctx.mcp.contracts import TOOL_CONTRACTS

_SKILL_NAME = "fixture-skill"
_DESCRIPTION = "Use when exercising portable canonical source validation in isolated tests."


def _skill(body: str) -> str:
    return f"---\nname: {_SKILL_NAME}\ndescription: {_DESCRIPTION}\n---\n\n{body}\n"


def _write_local_source(project: Path, body: str) -> Path:
    skill_directory = project / ".agents" / "skills" / _SKILL_NAME
    skill_directory.mkdir(parents=True)
    (project / ".agents" / "skills" / "registry.yaml").write_text(
        f"schema_version: 1\nskills:\n  - id: {_SKILL_NAME}\n    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    (skill_directory / "SKILL.md").write_text(_skill(body), encoding="utf-8")
    return skill_directory


def _write_packaged_kit(root: Path, body: str) -> Path:
    skill_directory = root / "skills" / _SKILL_NAME
    skill_directory.mkdir(parents=True)
    (root / "skills" / "registry.yaml").write_text(
        f"schema_version: 1\nskills:\n  - id: {_SKILL_NAME}\n    side_effect_class: read_only\n",
        encoding="utf-8",
    )
    (skill_directory / "SKILL.md").write_text(_skill(body), encoding="utf-8")
    bridges = root / "bridges"
    bridges.mkdir()
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        (bridges / name).write_text("# Bridge\n", encoding="utf-8")
    return skill_directory


def test_local_source_lint_resolves_safe_markdown_links_relative_to_skill(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    skill_directory = _write_local_source(
        project,
        "See [guide](references/guide-file.md#section), "
        "[reference][guide-ref], [local section](#section), and "
        "[external](https://example.test/docs).\n\n"
        "Inline `[missing](missing.md)` and this fenced example are not links:\n\n"
        "```text\n[missing](missing.md)\n```\n\n"
        "[guide-ref]: references/guide-file.md#section",
    )
    references = skill_directory / "references"
    references.mkdir()
    (references / "guide-file.md").write_text("# Section\n", encoding="utf-8")

    loaded = sources.load_canonical_sources(project, AgentClient.CLAUDE)

    assert loaded.origin is SourceOrigin.LOCAL
    assert [skill.name for skill in loaded.skills] == [_SKILL_NAME]


def test_codex_native_source_set_covers_all_skill_files_and_changes_with_resources(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    skill_directory = _write_local_source(project, "Portable source set.")
    references = skill_directory / "references"
    references.mkdir()
    guide = references / "guide.md"
    guide.write_bytes(b"# Guide\n")

    first = sources.load_canonical_sources(project, AgentClient.CODEX).skills[0]
    expected_files = (
        (f".agents/skills/{_SKILL_NAME}/SKILL.md", content_hash(first.content)),
        (
            f".agents/skills/{_SKILL_NAME}/references/guide.md",
            content_hash(b"# Guide\n"),
        ),
    )

    assert first.source_files == expected_files
    assert first.source_set_hash == source_set_aggregate_hash(expected_files)

    guide.write_bytes(b"# Revised guide\n")
    revised = sources.load_canonical_sources(project, AgentClient.CODEX).skills[0]

    assert revised.source_files[0] == first.source_files[0]
    assert revised.source_files[1][0] == first.source_files[1][0]
    assert revised.source_files[1][1] != first.source_files[1][1]
    assert revised.source_set_hash != first.source_set_hash


def test_codex_single_file_skill_has_one_element_native_source_set(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, "Single file source set.")

    skill = sources.load_canonical_sources(project, AgentClient.CODEX).skills[0]

    assert skill.source_files == ((skill.path, skill.content_hash),)
    assert skill.source_set_hash == source_set_aggregate_hash(skill.source_files)


@pytest.mark.parametrize(
    "destination",
    ("references/missing.md", "../../../../outside.md"),
)
def test_local_source_lint_rejects_missing_or_escaping_internal_link(
    tmp_path: Path,
    destination: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, f"See [unsafe]({destination}).")

    with pytest.raises(InvalidAdapterStateError, match="broken or unsafe internal link"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)


def test_local_source_lint_rejects_undefined_reference_link(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, "See [missing][undefined-reference].")

    with pytest.raises(InvalidAdapterStateError, match="undefined reference link"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)


@pytest.mark.parametrize(
    "registry",
    (
        b"schema_version: true\nskills:\n  - id: valid-skill\n    side_effect_class: read_only\n",
        b"schema_version: 1\nskills:\n  - id: a\n    side_effect_class: read_only\n",
        b"? [a, b]\n: bad\n",
    ),
)
def test_registry_validation_matches_strict_schema_types_and_name_length(
    registry: bytes,
) -> None:
    with pytest.raises(sources.CanonicalRegistryInvalidError):
        sources._registry_entries(registry)


@pytest.mark.parametrize(
    "body",
    (
        "Read `/tmp`.",
        "Read `/数据`.",
        "Read `//server/share/notes.md`.",
        "Read </home/operator>.",
    ),
)
def test_local_source_lint_rejects_all_machine_specific_absolute_path_forms(
    tmp_path: Path,
    body: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, body)

    with pytest.raises(InvalidAdapterStateError, match="machine-specific absolute path"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)


def test_local_source_lint_allows_slash_option_and_html_closing_tag(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, "Run with `/quiet`.\n\n<details>Okay.</details>")

    sources.load_canonical_sources(project, AgentClient.CLAUDE)


def test_local_source_lint_maps_malformed_url_to_typed_invalid_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, "See [malformed](http://[).")

    with pytest.raises(InvalidAdapterStateError, match="broken or unsafe internal link"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)


def test_local_source_lint_rejects_quoted_secret_with_spaces(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, "Use password = 'correct horse battery staple'.")

    with pytest.raises(InvalidAdapterStateError, match="secret-like material"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)


def test_local_source_lint_never_inspects_client_auth_link_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, "See [credentials](../../../.codex/auth.json).")
    auth = project / ".codex" / "auth.json"
    auth.parent.mkdir()
    auth.write_bytes(b"AUTH-CANARY")
    original_list = sources.SafeRoot.list_directory

    def reject_auth_directory(self: sources.SafeRoot, path: str):
        if path.startswith(".codex"):
            raise AssertionError("portable lint inspected an agent auth path")
        return original_list(self, path)

    monkeypatch.setattr(sources.SafeRoot, "list_directory", reject_auth_directory)

    with pytest.raises(InvalidAdapterStateError, match="broken or unsafe internal link"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)
    assert auth.read_bytes() == b"AUTH-CANARY"


@pytest.mark.parametrize(
    ("body", "valid"),
    (
        ("Run `workctx version`.", True),
        ("Run `workctx context validate --json`.", True),
        ("Run `workctx context validate --repair`.", False),
        ("Run `workctx brief`.", False),
        ("Run `workctx brief` (planned).", True),
        ("Call MCP tool `context_lookup`.", False),
        ("Call MCP tool `context_lookup` (planned).", True),
        ("Call MCP tool `context_info`.", True),
        ("Call `mcp__workctx__transaction_apply` MCP tool.", True),
        (
            "Use `workctx agent install` (planned), then `workctx context migrate`.",
            False,
        ),
    ),
)
def test_local_source_lint_requires_unimplemented_product_references_to_be_planned(
    tmp_path: Path,
    body: str,
    valid: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, body)

    if valid:
        sources.load_canonical_sources(project, AgentClient.CLAUDE)
    else:
        with pytest.raises(InvalidAdapterStateError, match="unimplemented"):
            sources.load_canonical_sources(project, AgentClient.CLAUDE)


@pytest.mark.parametrize("tool_name", [contract.name for contract in TOOL_CONTRACTS])
def test_local_source_lint_allows_every_adr_0012_mcp_tool_name(
    tmp_path: Path,
    tool_name: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, f"Call MCP tool `{tool_name}`.")

    sources.load_canonical_sources(project, AgentClient.CLAUDE)


def test_local_source_lint_rejects_unknown_client_qualified_mcp_tool(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_local_source(project, "Call `mcp__workctx__unknown_tool` MCP tool.")

    with pytest.raises(InvalidAdapterStateError, match="unimplemented MCP tool"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)


def test_mcp_tool_allowlist_is_sourced_from_contract_names_and_rejects_unknowns() -> None:
    names = {contract.name for contract in TOOL_CONTRACTS}

    assert all(sources._is_implemented_mcp_tool(name) for name in names)
    assert sources._is_implemented_mcp_tool("mcp__workctx__transaction_apply")
    assert not sources._is_implemented_mcp_tool("mcp__workctx__unknown_tool")


def test_packaged_source_lint_resolves_links_inside_packaged_kit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    kit = tmp_path / "kit"
    skill_directory = _write_packaged_kit(kit, "See [guide](references/guide.md).")
    references = skill_directory / "references"
    references.mkdir()
    guide = references / "guide.md"
    guide.write_text("# Guide\n", encoding="utf-8")
    monkeypatch.setattr(sources.resources, "files", lambda _package: kit)

    loaded = sources.load_canonical_sources(project, AgentClient.CLAUDE)

    assert loaded.origin is SourceOrigin.PACKAGED
    guide.unlink()
    with pytest.raises(InvalidAdapterStateError, match="broken or unsafe internal link"):
        sources.load_canonical_sources(project, AgentClient.CLAUDE)
