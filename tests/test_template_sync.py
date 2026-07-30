import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
CANONICAL_TEMPLATE = ROOT / "src" / "workctx" / "resources" / "context_template"
GENERATED_MIRROR = ROOT / "templates" / "context"
SYNC_SCRIPT = ROOT / "scripts" / "sync_context_template.py"


def _tree(root: Path) -> tuple[set[str], dict[str, bytes]]:
    directories = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()}
    files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    return directories, files


def _load_sync_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_context_template", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load template sync script: {SYNC_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_context_template_mirror_matches_canonical_tree() -> None:
    assert _tree(GENERATED_MIRROR) == _tree(CANONICAL_TEMPLATE)


def test_context_template_sync_check_passes_without_writing() -> None:
    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "Context template mirror is synchronized."


def test_context_template_sync_repairs_drift_and_is_idempotent(tmp_path: Path) -> None:
    module = _load_sync_module()
    canonical = tmp_path / "canonical"
    mirror = tmp_path / "mirror"
    (canonical / "nested").mkdir(parents=True)
    (canonical / "nested" / "expected.txt").write_text("expected", encoding="utf-8")
    mirror.mkdir()
    (mirror / "stale.txt").write_text("stale", encoding="utf-8")

    module.sync_context_template(canonical, mirror)

    assert _tree(mirror) == _tree(canonical)

    module.sync_context_template(canonical, mirror)

    assert _tree(mirror) == _tree(canonical)


def test_context_template_sync_preserves_mirror_when_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical = tmp_path / "canonical"
    mirror = tmp_path / "mirror"
    canonical.mkdir()
    mirror.mkdir()
    (canonical / "new.txt").write_text("new", encoding="utf-8")
    existing = mirror / "existing.txt"
    existing.write_text("preserve", encoding="utf-8")

    def fail_copytree(*args: object, **kwargs: object) -> None:
        raise OSError("simulated staging failure")

    monkeypatch.setattr(module.shutil, "copytree", fail_copytree)

    with pytest.raises(OSError, match="simulated staging failure"):
        module.sync_context_template(canonical, mirror)

    assert existing.read_text(encoding="utf-8") == "preserve"
    assert _tree(mirror) == (set(), {"existing.txt": b"preserve"})
    assert not list(tmp_path.glob(".context-template-sync-*"))


def test_context_template_sync_restores_mirror_when_replacement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical = tmp_path / "canonical"
    mirror = tmp_path / "mirror"
    canonical.mkdir()
    mirror.mkdir()
    (canonical / "new.txt").write_text("new", encoding="utf-8")
    existing = mirror / "existing.txt"
    existing.write_text("preserve", encoding="utf-8")
    original_rename = Path.rename

    def fail_staged_replacement(path: Path, target: Path) -> Path:
        if (
            path.name == "context"
            and path.parent.name.startswith(".context-template-sync-")
            and target == mirror
        ):
            raise OSError("simulated replacement failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staged_replacement)

    with pytest.raises(OSError, match="simulated replacement failure"):
        module.sync_context_template(canonical, mirror)

    assert existing.read_text(encoding="utf-8") == "preserve"
    assert _tree(mirror) == (set(), {"existing.txt": b"preserve"})
    assert not list(tmp_path.glob(".context-template-sync-*"))


def test_context_template_sync_preserves_recovery_copy_when_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical = tmp_path / "canonical"
    mirror = tmp_path / "mirror"
    canonical.mkdir()
    mirror.mkdir()
    (canonical / "new.txt").write_text("new", encoding="utf-8")
    (mirror / "existing.txt").write_text("preserve", encoding="utf-8")
    original_rename = Path.rename

    def fail_replacement_and_restore(path: Path, target: Path) -> Path:
        if path.parent.name.startswith(".context-template-sync-") and target == mirror:
            if path.name == "context":
                raise OSError("simulated replacement failure")
            if path.name == "previous":
                raise OSError("simulated restoration failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_replacement_and_restore)

    with pytest.raises(OSError, match="simulated restoration failure"):
        module.sync_context_template(canonical, mirror)

    assert not mirror.exists()
    staging_containers = list(tmp_path.glob(".context-template-sync-*"))
    assert len(staging_containers) == 1
    recovery_copy = staging_containers[0] / "previous" / "existing.txt"
    assert recovery_copy.read_text(encoding="utf-8") == "preserve"


def test_context_template_sync_rejects_canonical_root_symlink_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical = tmp_path / "canonical"
    mirror = tmp_path / "mirror"
    canonical.mkdir()
    mirror.mkdir()
    existing = mirror / "existing.txt"
    existing.write_text("preserve", encoding="utf-8")
    original_is_symlink = Path.is_symlink

    def treat_canonical_as_symlink(path: Path) -> bool:
        return path == canonical or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", treat_canonical_as_symlink)

    with pytest.raises(RuntimeError, match="Canonical template cannot be a symbolic link"):
        module.sync_context_template(canonical, mirror)

    assert existing.read_text(encoding="utf-8") == "preserve"
    assert not list(tmp_path.glob(".context-template-sync-*"))
