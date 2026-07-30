from pathlib import Path

import yaml

from workctx.services.contexts import initialize_context, load_context_config
from workctx.validation.workspace import validate_workspace


def test_initialize_context_creates_valid_isolated_workspace(tmp_path: Path) -> None:
    target = tmp_path / "company-a"
    config = initialize_context(target, name="Company A", context_id="company-a")

    assert config.id == "company-a"
    assert (target / "00_inbox" / "raw").is_dir()
    assert (target / "98_state").is_dir()
    assert load_context_config(target).id == "company-a"
    assert "workctx://company-a/task" in (
        target / "99_meta" / "templates" / "task.md"
    ).read_text(encoding="utf-8")

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
