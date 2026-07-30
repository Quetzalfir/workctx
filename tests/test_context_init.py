from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from workctx.errors import ContextAlreadyExistsError
from workctx.models.context import ContextKind, ContextProfile
from workctx.services.contexts import initialize_context, load_context_config
from workctx.validation.workspace import validate_workspace


def test_initialize_context_creates_valid_isolated_workspace(tmp_path: Path) -> None:
    target = tmp_path / "company-a"
    config = initialize_context(target, name="Company A", context_id="company-a")

    assert config.id == "company-a"
    assert (target / "00_inbox" / "raw").is_dir()
    assert (target / "98_state").is_dir()
    assert load_context_config(target).id == "company-a"
    assert "workctx://company-a/task" in (target / "99_meta" / "templates" / "task.md").read_text(
        encoding="utf-8"
    )

    raw = yaml.safe_load((target / "context.yaml").read_text(encoding="utf-8"))
    assert raw["languages"]["repository"] == "en"
    assert raw["languages"]["user_interaction"] == "en"
    assert raw["timezone"] == "UTC"
    assert validate_workspace(target).ok


def test_initialize_context_supports_existing_empty_directory(tmp_path: Path) -> None:
    target = tmp_path / "existing-empty"
    target.mkdir()

    config = initialize_context(target, name="Existing Empty", context_id="existing-empty")

    assert config.id == "existing-empty"
    assert (target / "context.yaml").is_file()


def test_initialize_context_rejects_non_empty_directory_without_modifying_it(
    tmp_path: Path,
) -> None:
    target = tmp_path / "not-empty"
    target.mkdir()
    existing = target / "existing.txt"
    existing.write_text("preserve me", encoding="utf-8")

    with pytest.raises(ContextAlreadyExistsError, match="not empty"):
        initialize_context(target, name="Not Empty", context_id="not-empty")

    assert existing.read_text(encoding="utf-8") == "preserve me"
    assert not (target / "context.yaml").exists()


def test_initialize_context_validates_inputs_before_creating_target(tmp_path: Path) -> None:
    target = tmp_path / "invalid-context"

    with pytest.raises(ValueError, match="at least two"):
        initialize_context(target, name="Invalid Context", context_id="!")

    assert not target.exists()


def test_initialize_context_validates_configuration_before_creating_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "invalid-configuration"

    with pytest.raises(ValueError):
        initialize_context(
            target,
            name="Invalid Configuration",
            context_id="invalid-configuration",
            user_language="invalid language value",
        )

    assert not target.exists()


def test_template_placeholders_do_not_modify_context_configuration(tmp_path: Path) -> None:
    target = tmp_path / "placeholder-name"
    name = "example-context 2000-01-01T00:00:00Z"

    initialize_context(target, name=name, context_id="placeholder-name")

    assert load_context_config(target).name == name


def test_initialize_context_honors_kind_profile_and_parameterizes_timestamps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2031, 2, 3, 4, 5, 6, tzinfo=UTC)
    monkeypatch.setattr("workctx.services.contexts._utc_now", lambda: fixed_now)
    target = tmp_path / "company-context"

    config = initialize_context(
        target,
        name="Fictional Company",
        context_id="fictional-company",
        kind=ContextKind.COMPANY,
        profile=ContextProfile.LIGHT,
    )

    loaded = load_context_config(target)
    assert config.kind is ContextKind.COMPANY
    assert config.profile is ContextProfile.LIGHT
    assert loaded.kind is ContextKind.COMPANY
    assert loaded.profile is ContextProfile.LIGHT

    raw = yaml.safe_load((target / "context.yaml").read_text(encoding="utf-8"))
    assert raw["kind"] == "company"
    assert raw["profile"] == "light"
    assert raw["created_at"] == "2031-02-03T04:05:06Z"
    assert raw["updated_at"] == "2031-02-03T04:05:06Z"

    for template in (target / "99_meta" / "templates").glob("*.md"):
        rendered = template.read_text(encoding="utf-8")
        assert "2000-01-01T00:00:00Z" not in rendered
        assert "2031-02-03T04:05:06Z" in rendered
        assert "example-context" not in rendered
