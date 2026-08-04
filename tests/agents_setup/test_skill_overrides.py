from __future__ import annotations

from pathlib import Path

import pytest

from workctx.adapters.agents import (
    SKILL_OVERRIDE_MAX_BYTES,
    SKILL_OVERRIDE_PROVENANCE_END,
    SKILL_OVERRIDE_PROVENANCE_START,
    AdapterState,
    AgentAdapterService,
    AgentClient,
    FileOperation,
    SkillOverrideSecretError,
    SkillOverrideTooLargeError,
    SkillOverrideWarningCode,
    discover_skill_override_files,
    sources,
)
from workctx.adapters.agents.errors import InvalidAdapterStateError
from workctx.adapters.agents.manifest import load_manifest
from workctx.adapters.agents.renderers import content_hash
from workctx.services.contexts import initialize_context
from workctx.validation import validate_workspace

_SKILL_NAME = "fictional-review"
_DESCRIPTION = "Use when reviewing a fictional project change with traceable evidence."


def _skill(name: str, body: str) -> bytes:
    return (f"---\nname: {name}\ndescription: {_DESCRIPTION}\n---\n\n{body}\n").encode()


def _write_packaged_kit(root: Path, skills: dict[str, bytes]) -> None:
    skill_root = root / "skills"
    skill_root.mkdir(parents=True)
    registry_lines = ["schema_version: 1", "skills:"]
    for name in sorted(skills):
        registry_lines.extend((f"  - id: {name}", "    side_effect_class: read_only"))
        directory = skill_root / name
        directory.mkdir()
        (directory / "SKILL.md").write_bytes(skills[name])
    (skill_root / "registry.yaml").write_text(
        "\n".join(registry_lines) + "\n",
        encoding="utf-8",
    )
    bridges = root / "bridges"
    bridges.mkdir()
    for bridge in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        (bridges / bridge).write_text("# Fictional bridge\n", encoding="utf-8")


def _write_override(
    context: Path,
    *,
    name: str,
    packaged_at_adoption: bytes,
    body: str,
) -> Path:
    path = context / "06_overrides" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {_DESCRIPTION}\n---\n"
        f"{SKILL_OVERRIDE_PROVENANCE_START}\n"
        f"source: 06_overrides/skills/{name}/SKILL.md\n"
        f"packaged-at-adoption: {content_hash(packaged_at_adoption)}\n"
        f"{SKILL_OVERRIDE_PROVENANCE_END}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _service() -> AgentAdapterService:
    versions = {
        "fake-codex": "codex 0.5.0",
        "fake-claude": "Claude Code 2.0.0",
        "fake-gemini": "Gemini CLI 0.5.0",
    }
    return AgentAdapterService(
        executable_finder=lambda name: f"fake-{name}",
        version_probe=lambda executable, _root: versions[executable],
        session_id_factory=lambda: "skill-override-session",
    )


@pytest.mark.parametrize(
    "names",
    ((), ("fictional-one",), ("fictional-a", "fictional-b", "fictional-c")),
)
def test_override_discovery_covers_none_one_and_many(
    tmp_path: Path,
    names: tuple[str, ...],
) -> None:
    context = tmp_path / "fictional-context"
    context.mkdir()
    for name in reversed(names):
        path = context / "06_overrides" / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Inert fictional override\n", encoding="utf-8")

    discovered = discover_skill_override_files(context)

    assert tuple(item.skill for item in discovered) == names
    assert tuple(item.path for item in discovered) == tuple(
        f"06_overrides/skills/{name}/SKILL.md" for name in names
    )


def test_install_plan_lists_override_first_and_output_keeps_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "Context With Spaces"
    initialize_context(context, name="Fictional Context", context_id="fictional-context")
    packaged = _skill(_SKILL_NAME, "Use the packaged review sequence.")
    kit = tmp_path / "fictional-kit"
    _write_packaged_kit(kit, {_SKILL_NAME: packaged})
    monkeypatch.setattr(sources.resources, "files", lambda _package: kit)
    override_path = _write_override(
        context,
        name=_SKILL_NAME,
        packaged_at_adoption=packaged,
        body="Use the context-specific review sequence.",
    )
    original = (override_path.read_bytes(), override_path.stat().st_mtime_ns)
    service = _service()

    plan = service.plan_install(context, AgentClient.CLAUDE)

    assert plan.changes[0].path == f"06_overrides/skills/{_SKILL_NAME}/SKILL.md"
    assert plan.changes[0].operation is FileOperation.VERIFY
    assert "\\" not in plan.changes[0].path
    assert plan.skill_overrides[0].known
    assert plan.skill_overrides[0].packaged_at_adoption_hash == content_hash(packaged)
    assert plan.skill_overrides[0].packaged_now_hash == content_hash(packaged)
    service.install(plan)

    installed = (context / ".claude" / "skills" / _SKILL_NAME / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert SKILL_OVERRIDE_PROVENANCE_START in installed
    assert SKILL_OVERRIDE_PROVENANCE_END in installed
    assert f"source: 06_overrides/skills/{_SKILL_NAME}/SKILL.md" in installed
    assert f"packaged-at-adoption: {content_hash(packaged)}" in installed
    assert "Use the context-specific review sequence." in installed
    assert "Use the packaged review sequence." not in installed
    assert installed.index(SKILL_OVERRIDE_PROVENANCE_START) < installed.index(
        "Use the context-specific review sequence."
    )
    assert (override_path.read_bytes(), override_path.stat().st_mtime_ns) == original


def test_stale_upgrade_marker_surfaces_three_hashes_without_blocking_or_merging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "stale-context"
    initialize_context(context, name="Stale Fictional", context_id="stale-fictional")
    packaged_old = _skill(_SKILL_NAME, "Use packaged sequence version one.")
    packaged_new = _skill(_SKILL_NAME, "Use packaged sequence version two.")
    kit = tmp_path / "upgrade-kit"
    _write_packaged_kit(kit, {_SKILL_NAME: packaged_old})
    monkeypatch.setattr(sources.resources, "files", lambda _package: kit)
    override = _write_override(
        context,
        name=_SKILL_NAME,
        packaged_at_adoption=packaged_old,
        body="Keep the deliberately independent context sequence.",
    )
    override_hash = content_hash(override.read_bytes())
    service = _service()
    service.install(service.plan_install(context, AgentClient.CLAUDE))

    (kit / "skills" / _SKILL_NAME / "SKILL.md").write_bytes(packaged_new)
    status = service.status(context, AgentClient.CLAUDE)
    plan = service.plan_install(context, AgentClient.CLAUDE)

    assert status.state is AdapterState.CURRENT
    assert len(status.skill_override_warnings) == 1
    warning = status.skill_override_warnings[0]
    assert warning.code is SkillOverrideWarningCode.OLDER_PACKAGED_SKILL
    assert warning.packaged_at_adoption_hash == content_hash(packaged_old)
    assert warning.packaged_now_hash == content_hash(packaged_new)
    assert warning.override_hash == override_hash
    assert any(
        "override written against an older packaged skill" in item for item in status.warnings
    )
    marker = plan.changes[0]
    assert marker.operation is FileOperation.VERIFY
    assert content_hash(packaged_old) in (marker.reason or "")
    assert content_hash(packaged_new) in (marker.reason or "")
    assert override_hash in (marker.reason or "")
    assert plan.blocked_reason is None
    assert plan.is_noop
    service.install(plan)
    installed = (context / ".claude" / "skills" / _SKILL_NAME / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "deliberately independent context sequence" in installed
    assert "packaged sequence version two" not in installed


def test_codex_adoption_and_removal_restore_packaged_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "codex-context"
    initialize_context(context, name="Codex Fictional", context_id="codex-fictional")
    packaged = _skill(_SKILL_NAME, "Use the packaged Codex behavior.")
    kit = tmp_path / "codex-kit"
    _write_packaged_kit(kit, {_SKILL_NAME: packaged})
    monkeypatch.setattr(sources.resources, "files", lambda _package: kit)
    service = _service()
    service.install(service.plan_install(context, AgentClient.CODEX))
    installed_path = context / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"
    assert installed_path.read_bytes() == packaged

    override = _write_override(
        context,
        name=_SKILL_NAME,
        packaged_at_adoption=packaged,
        body="Use the adopted Codex context behavior.",
    )
    adoption_plan = service.plan_install(context, AgentClient.CODEX)
    assert any(
        change.path == f".agents/skills/{_SKILL_NAME}/SKILL.md"
        and change.operation is FileOperation.REPLACE
        for change in adoption_plan.changes
    )
    service.install(adoption_plan)
    assert "adopted Codex context behavior" in installed_path.read_text(encoding="utf-8")
    manifest_path = context / "98_state" / "agent-adapters" / "codex" / "skill-manifest.json"
    installed_manifest = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    assert installed_manifest.skills[0].effective_mode == "generated"

    override.unlink()
    removal_plan = service.plan_install(context, AgentClient.CODEX)
    restoration = next(
        change
        for change in removal_plan.changes
        if change.path == f".agents/skills/{_SKILL_NAME}/SKILL.md"
    )
    assert restoration.operation is FileOperation.REPLACE
    assert "Restore the current packaged skill" in (restoration.reason or "")
    service.install(removal_plan)

    assert installed_path.read_bytes() == packaged
    restored_manifest = load_manifest(manifest_path.read_bytes()).require_producer_contract()
    assert restored_manifest.skills[0].effective_mode == "native-verified"
    assert service.status(context, AgentClient.CODEX).state is AdapterState.CURRENT


def test_override_size_and_secret_refusals_are_typed_and_content_free(
    tmp_path: Path,
) -> None:
    context = tmp_path / "bounded-context"
    context.mkdir()
    oversized = context / "06_overrides" / "skills" / "fictional-large" / "SKILL.md"
    oversized.parent.mkdir(parents=True)
    oversized.write_bytes(b"x" * (SKILL_OVERRIDE_MAX_BYTES + 1))

    with pytest.raises(SkillOverrideTooLargeError) as too_large:
        discover_skill_override_files(context)

    assert too_large.value.path == "06_overrides/skills/fictional-large/SKILL.md"
    assert too_large.value.size_bytes == SKILL_OVERRIDE_MAX_BYTES + 1
    oversized.unlink()
    secret_path = context / "06_overrides" / "skills" / "fictional-secret" / "SKILL.md"
    secret_path.parent.mkdir()
    secret_assignment = "api_" + 'key = "' + "sk-" + 'fictional-1234567890abcdef"'
    secret_path.write_text("# Fictional\n" + secret_assignment + "\n", encoding="utf-8")

    with pytest.raises(SkillOverrideSecretError) as secret:
        discover_skill_override_files(context)

    assert secret.value.line_number == 2
    assert str(secret.value) == "06_overrides/skills/fictional-secret/SKILL.md, line 2"
    assert str(context) not in str(secret.value)
    assert secret_assignment not in str(secret.value)


def test_unknown_skill_is_typed_warning_and_never_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "unknown-context"
    initialize_context(context, name="Unknown Fictional", context_id="unknown-fictional")
    packaged = _skill(_SKILL_NAME, "Use the packaged known behavior.")
    kit = tmp_path / "unknown-kit"
    _write_packaged_kit(kit, {_SKILL_NAME: packaged})
    monkeypatch.setattr(sources.resources, "files", lambda _package: kit)
    unknown = context / "06_overrides" / "skills" / "fictional-unknown" / "SKILL.md"
    unknown.parent.mkdir(parents=True)
    unknown.write_text("# Unknown but inert override\n", encoding="utf-8")
    service = _service()

    status = service.status(context, AgentClient.CLAUDE)
    plan = service.plan_install(context, AgentClient.CLAUDE)

    assert status.state is AdapterState.NOT_INSTALLED
    assert status.skill_override_warnings[0].code is SkillOverrideWarningCode.UNKNOWN_SKILL
    assert "Unknown skill override ignored" in str(status.skill_override_warnings[0])
    assert plan.skill_overrides[0].known is False
    assert plan.changes[0].path == "06_overrides/skills/fictional-unknown/SKILL.md"
    assert plan.changes[0].operation is FileOperation.VERIFY
    service.install(plan)


def test_override_content_reuses_packaged_skill_lint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = tmp_path / "lint-context"
    initialize_context(context, name="Lint Fictional", context_id="lint-fictional")
    packaged = _skill(_SKILL_NAME, "Use the packaged valid behavior.")
    kit = tmp_path / "lint-kit"
    _write_packaged_kit(kit, {_SKILL_NAME: packaged})
    monkeypatch.setattr(sources.resources, "files", lambda _package: kit)
    _write_override(
        context,
        name=_SKILL_NAME,
        packaged_at_adoption=packaged,
        body="Run `workctx secrets export`.",
    )

    with pytest.raises(InvalidAdapterStateError, match="unimplemented product command"):
        _service().plan_install(context, AgentClient.CLAUDE)


def test_repository_scope_does_not_discover_context_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "ordinary-repository"
    repository.mkdir()
    packaged = _skill(_SKILL_NAME, "Use the packaged repository behavior.")
    kit = tmp_path / "repository-kit"
    _write_packaged_kit(kit, {_SKILL_NAME: packaged})
    monkeypatch.setattr(sources.resources, "files", lambda _package: kit)
    candidate = repository / "06_overrides" / "skills" / "fictional-unknown" / "SKILL.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("# Repository-local candidate\n", encoding="utf-8")

    plan = _service().plan_install(repository, AgentClient.CLAUDE)

    assert plan.skill_overrides == ()
    assert all(not change.path.startswith("06_overrides/") for change in plan.changes)


def test_workspace_zone_validation_accepts_inert_override_directory(
    tmp_path: Path,
) -> None:
    context = tmp_path / "validation-context"
    initialize_context(context, name="Validation Fictional", context_id="validation-fictional")
    packaged = _skill(_SKILL_NAME, "Use the packaged validation behavior.")
    _write_override(
        context,
        name=_SKILL_NAME,
        packaged_at_adoption=packaged,
        body="Use a fictional validation-only override.",
    )

    report = validate_workspace(context)

    assert report.ok
    assert all(
        issue.path != f"06_overrides/skills/{_SKILL_NAME}/SKILL.md" for issue in report.issues
    )
