from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import workctx.adapters.agents.sources as sources_module
from workctx.adapters.agents.errors import InvalidAdapterStateError
from workctx.adapters.agents.manifest import load_manifest
from workctx.adapters.agents.models import AdapterState, AgentClient, FileOperation
from workctx.adapters.agents.service import AgentAdapterService
from workctx.adapters.agents.sources import (
    CanonicalRegistryInvalidError,
    refresh_registry_skills,
)
from workctx.cli import _adapter_status_payload
from workctx.services.contexts import initialize_context

_CUSTOM_SKILL = "fictional-context-check"
_NEW_PACKAGED_SKILL = "fictional-packaged-check"
_CUSTOM_SECTION = (
    "custom_skills:\n"
    "  # This operator-authored formatting must survive byte-for-byte.\n"
    f"  - id: {_CUSTOM_SKILL}\n"
    "    side_effect_class: read_only\n"
    "    notes: >-\n"
    "      Keep this folded note and its formatting exactly.\n"
).encode()


def test_registry_schema_accepts_optional_custom_skill_inventory() -> None:
    root = Path(__file__).parents[2]
    schema = json.loads(
        (root / "schemas" / "skill-registry.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(
        {
            "schema_version": 1,
            "skills": [{"id": "packaged-one", "side_effect_class": "read_only"}],
            "custom_skills": [
                {
                    "id": _CUSTOM_SKILL,
                    "side_effect_class": "local_proposal",
                    "notes": "Context-local fictional workflow.",
                }
            ],
        }
    )


def test_registry_refresh_preserves_custom_boundary_comment_after_folded_value() -> None:
    local_registry = b"""schema_version: 1
skills:
  - id: packaged-one
    side_effect_class: read_only
    notes: >-
      Packaged folded note.
# Operator boundary comment must survive.
custom_skills:
  - id: custom-one
    side_effect_class: read_only
"""
    packaged_registry = b"""schema_version: 1
skills:
  - id: packaged-two
    side_effect_class: read_only
"""

    refreshed = refresh_registry_skills(local_registry, packaged_registry)

    expected_suffix = b"""# Operator boundary comment must survive.
custom_skills:
  - id: custom-one
    side_effect_class: read_only
"""
    assert refreshed.endswith(expected_suffix)


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    versions = {
        "fake-codex": "codex 0.5.0",
        "fake-claude": "claude 1.5.0",
        "fake-gemini": "gemini 0.5.0",
    }
    return versions[executable]


def _service() -> AgentAdapterService:
    return AgentAdapterService(
        executable_finder=_finder,
        version_probe=_probe,
        session_id_factory=lambda: "custom-skills-session",
    )


def _installed_context(tmp_path: Path, context_id: str) -> tuple[Path, AgentAdapterService]:
    root = tmp_path / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    service = _service()
    service.install(service.plan_install(root, AgentClient.CODEX))
    return root, service


def _skill_bytes(name: str, *, description: str, body: str) -> bytes:
    return (f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n").encode()


def _register_custom_skill(
    root: Path,
    *,
    description: str = "Use when checking a fictional context-local workflow safely.",
    body: str = "Read [the local guide](references/guide.md).",
) -> tuple[bytes, bytes]:
    registry = root / ".agents" / "skills" / "registry.yaml"
    packaged_registry = registry.read_bytes()
    registry.write_bytes(packaged_registry + _CUSTOM_SECTION)
    skill_root = root / ".agents" / "skills" / _CUSTOM_SKILL
    skill_root.mkdir()
    skill_content = _skill_bytes(_CUSTOM_SKILL, description=description, body=body)
    (skill_root / "SKILL.md").write_bytes(skill_content)
    references = skill_root / "references"
    references.mkdir()
    (references / "guide.md").write_bytes(b"# Fictional local guide\n")
    return packaged_registry, skill_content


def _patch_packaged_addition(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: bytes,
    skill: bytes,
) -> None:
    original_file = sources_module._packaged_file
    original_resources = sources_module._packaged_skill_resources

    def packaged_file(*parts: str) -> bytes:
        if parts == ("skills", "registry.yaml"):
            return registry
        if parts == ("skills", _NEW_PACKAGED_SKILL, "SKILL.md"):
            return skill
        return original_file(*parts)

    def packaged_resources(name: str):
        if name == _NEW_PACKAGED_SKILL:
            return ()
        return original_resources(name)

    monkeypatch.setattr(sources_module, "_packaged_file", packaged_file)
    monkeypatch.setattr(sources_module, "_packaged_skill_resources", packaged_resources)


def test_custom_skill_renders_for_clients_and_survives_packaged_registry_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, service = _installed_context(tmp_path, "custom-skill-refresh")
    packaged_registry, custom_content = _register_custom_skill(root)
    custom_path = f".agents/skills/{_CUSTOM_SKILL}/SKILL.md"

    codex_plan = service.plan_install(root, AgentClient.CODEX)
    assert all(change.path != custom_path for change in codex_plan.changes)
    service.install(codex_plan)

    codex_status = service.status(root, AgentClient.CODEX)
    assert codex_status.custom_skills == (_CUSTOM_SKILL,)
    assert _adapter_status_payload(codex_status)["custom_skills"] == [_CUSTOM_SKILL]

    claude_plan = service.plan_install(root, AgentClient.CLAUDE)
    service.install(claude_plan)
    claude_primary = root / ".claude" / "skills" / _CUSTOM_SKILL / "SKILL.md"
    claude_resource = root / ".claude" / "skills" / _CUSTOM_SKILL / "references" / "guide.md"
    assert b"# workctx-side-effect-class: read_only" in claude_primary.read_bytes()
    assert claude_resource.read_bytes() == b"# Fictional local guide\n"

    gemini_plan = service.plan_install(root, AgentClient.GEMINI)
    service.install(gemini_plan)
    gemini_primary = root / ".gemini" / "skills" / _CUSTOM_SKILL / "SKILL.md"
    gemini_resource = root / ".gemini" / "skills" / _CUSTOM_SKILL / "references" / "guide.md"
    assert b"# workctx-side-effect-class: read_only" in gemini_primary.read_bytes()
    assert gemini_resource.read_bytes() == b"# Fictional local guide\n"

    packaged_registry_now = (
        packaged_registry
        + (f"  - id: {_NEW_PACKAGED_SKILL}\n    side_effect_class: read_only\n").encode()
    )
    packaged_skill_now = _skill_bytes(
        _NEW_PACKAGED_SKILL,
        description="Use when checking a fictional packaged registry refresh safely.",
        body="Inspect fictional packaged state.",
    )
    _patch_packaged_addition(
        monkeypatch,
        registry=packaged_registry_now,
        skill=packaged_skill_now,
    )

    status_before = service.status(root, AgentClient.CODEX)
    refresh = service.plan_install(root, AgentClient.CODEX)

    assert status_before.custom_skills == (_CUSTOM_SKILL,)
    assert status_before.merge_candidates == ()
    assert refresh.merge_candidates == ()
    assert any(
        change.path == ".agents/skills/registry.yaml" and change.operation is FileOperation.REPLACE
        for change in refresh.changes
    )
    assert not any(
        change.path.startswith(f".agents/skills/{_CUSTOM_SKILL}/")
        and change.operation in {FileOperation.REPLACE, FileOperation.DELETE}
        for change in refresh.changes
    )

    service.install(refresh)

    refreshed_registry = (root / ".agents" / "skills" / "registry.yaml").read_bytes()
    assert refreshed_registry.endswith(_CUSTOM_SECTION)
    assert (root / custom_path).read_bytes() == custom_content
    assert (
        root / ".agents" / "skills" / _NEW_PACKAGED_SKILL / "SKILL.md"
    ).read_bytes() == packaged_skill_now
    manifest = load_manifest(
        (root / "98_state" / "agent-adapters" / "codex" / "skill-manifest.json").read_bytes()
    ).require_producer_contract()
    assert {_CUSTOM_SKILL, _NEW_PACKAGED_SKILL} <= {
        skill_entry.name for skill_entry in manifest.skills
    }


@pytest.mark.parametrize(
    ("description", "body", "expected_rule"),
    (
        (
            "too short",
            "Read [the local guide](references/guide.md).",
            "description length rule: expected 20-600 characters",
        ),
        (
            "Use when checking a fictional context-local workflow safely.",
            "Read [a missing resource](references/missing.md).",
            "broken or unsafe internal link: references/missing.md",
        ),
    ),
)
def test_invalid_custom_skill_plan_error_names_skill_and_exact_rule(
    tmp_path: Path,
    description: str,
    body: str,
    expected_rule: str,
) -> None:
    root, service = _installed_context(tmp_path, "invalid-custom-skill")
    _register_custom_skill(root, description=description, body=body)

    with pytest.raises(InvalidAdapterStateError) as raised:
        service.plan_install(root, AgentClient.CODEX)

    message = str(raised.value)
    assert f"Custom skill {_CUSTOM_SKILL}" in message
    assert expected_rule in message


def test_misplaced_context_skill_surfaces_migration_repair_action_without_mutation(
    tmp_path: Path,
) -> None:
    root, service = _installed_context(tmp_path, "misplaced-custom-skill")
    registry = root / ".agents" / "skills" / "registry.yaml"
    misplaced_registry = (
        registry.read_bytes()
        + (f"  - id: {_CUSTOM_SKILL}\n    side_effect_class: read_only\n").encode()
    )
    registry.write_bytes(misplaced_registry)
    skill_root = root / ".agents" / "skills" / _CUSTOM_SKILL
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_bytes(
        _skill_bytes(
            _CUSTOM_SKILL,
            description="Use when checking migration of a fictional custom workflow.",
            body="Inspect fictional local state.",
        )
    )

    with pytest.raises(CanonicalRegistryInvalidError) as raised:
        service.plan_install(root, AgentClient.CODEX)

    assert raised.value.repair_action is not None
    assert _CUSTOM_SKILL in str(raised.value)
    assert _CUSTOM_SKILL in raised.value.repair_action
    assert "from skills: to custom_skills:" in raised.value.repair_action
    assert registry.read_bytes() == misplaced_registry
    assert b"custom_skills:" not in misplaced_registry
    status = service.status(root, AgentClient.CODEX)
    assert status.state is AdapterState.INVALID
    assert any("custom_skills:" in warning for warning in status.warnings)
