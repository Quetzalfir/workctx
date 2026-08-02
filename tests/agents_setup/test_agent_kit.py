import importlib.resources
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]
CANONICAL_SKILLS = ROOT / ".agents" / "skills"
PACKAGED_AGENT_KIT = ROOT / "src" / "workctx" / "resources" / "agent_kit"
SYNC_SCRIPT = ROOT / "scripts" / "sync_agent_kit.py"
BRIDGE_NAMES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
BRIDGE_SKILL_PATHS = {
    "AGENTS.md": ".agents/skills/",
    "CLAUDE.md": ".claude/skills/",
    "GEMINI.md": ".gemini/skills/",
}

type TreeEntries = tuple[set[str], dict[str, bytes]]


def _tree(root: Path) -> TreeEntries:
    directories = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir() and "__pycache__" not in path.relative_to(root).parts
    }
    files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.relative_to(root).parts
        and path.suffix != ".pyc"
    }
    return directories, files


def _load_sync_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_agent_kit", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load agent-kit sync script: {SYNC_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_fixture(tmp_path: Path) -> Path:
    canonical_root = tmp_path / "canonical"
    canonical_skills = canonical_root / ".agents" / "skills"
    (canonical_skills / "sample-skill" / "references").mkdir(parents=True)
    (canonical_skills / "README.md").write_bytes(b"canonical skills\n")
    (canonical_skills / "registry.yaml").write_bytes(b"schema_version: 1\n")
    (canonical_skills / "sample-skill" / "SKILL.md").write_bytes(b"---\nname: sample-skill\n")
    return canonical_skills


def _write_kit_authored_files(packaged_agent_kit: Path) -> dict[str, bytes]:
    packaged_agent_kit.mkdir(parents=True)
    authored = {"__init__.py": b'"""Authored package metadata."""\n'}
    bridges = packaged_agent_kit / "bridges"
    bridges.mkdir()
    for bridge_name in BRIDGE_NAMES:
        relative = f"bridges/{bridge_name}"
        authored[relative] = f"authored {bridge_name}\n".encode()
    for relative, content in authored.items():
        target = packaged_agent_kit / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return authored


def _assert_no_staging(packaged_agent_kit: Path) -> None:
    assert not list(packaged_agent_kit.glob(".agent-kit-sync-*"))


def test_packaged_skills_are_a_byte_exact_tree_mirror() -> None:
    assert _tree(PACKAGED_AGENT_KIT / "skills") == _tree(CANONICAL_SKILLS)


@pytest.mark.parametrize("bridge_name", BRIDGE_NAMES)
def test_packaged_bridge_is_target_flavored_and_kit_authored(bridge_name: str) -> None:
    content = (PACKAGED_AGENT_KIT / "bridges" / bridge_name).read_text(encoding="utf-8")

    assert content.encode() != (ROOT / bridge_name).read_bytes()
    assert BRIDGE_SKILL_PATHS[bridge_name] in content
    assert "authentication credentials" in content
    assert "user-global authentication files" in content
    assert "START-HERE.md" not in content
    assert ".agents/plan/" not in content
    if bridge_name != "AGENTS.md":
        assert "When `AGENTS.md` exists" in content
        assert not content.startswith("@AGENTS.md")


def test_agent_kit_sync_check_passes_without_writing() -> None:
    before = _tree(PACKAGED_AGENT_KIT)

    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "Agent kit is synchronized."
    assert _tree(PACKAGED_AGENT_KIT) == before


def test_agent_kit_sync_repairs_drift_and_is_content_idempotent(tmp_path: Path) -> None:
    module = _load_sync_module()
    canonical_skills = _canonical_fixture(tmp_path)
    packaged_agent_kit = tmp_path / "package" / "agent_kit"
    _write_kit_authored_files(packaged_agent_kit)
    (packaged_agent_kit / "stale.txt").write_bytes(b"stale\n")

    assert module.sync_agent_kit(canonical_skills, packaged_agent_kit)
    assert module.agent_kit_differences(canonical_skills, packaged_agent_kit) == []
    synchronized = _tree(packaged_agent_kit)

    assert not module.sync_agent_kit(canonical_skills, packaged_agent_kit)
    assert _tree(packaged_agent_kit) == synchronized
    _assert_no_staging(packaged_agent_kit)


def test_agent_kit_sync_preserves_kit_authored_files(tmp_path: Path) -> None:
    module = _load_sync_module()
    canonical_skills = _canonical_fixture(tmp_path)
    packaged_agent_kit = tmp_path / "package" / "agent_kit"
    authored = _write_kit_authored_files(packaged_agent_kit)

    assert module.sync_agent_kit(canonical_skills, packaged_agent_kit)

    assert {
        relative: (packaged_agent_kit / relative).read_bytes() for relative in authored
    } == authored
    assert _tree(packaged_agent_kit / "skills") == _tree(canonical_skills)
    _assert_no_staging(packaged_agent_kit)


def test_agent_kit_sync_preserves_package_when_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical_skills = _canonical_fixture(tmp_path)
    packaged_agent_kit = tmp_path / "package" / "agent_kit"
    _write_kit_authored_files(packaged_agent_kit)
    (packaged_agent_kit / "skills").mkdir()
    (packaged_agent_kit / "skills" / "stale.txt").write_bytes(b"stale skills\n")
    (packaged_agent_kit / "existing.txt").write_bytes(b"preserve\n")
    before = _tree(packaged_agent_kit)

    def fail_staging(*args: object, **kwargs: object) -> None:
        raise OSError("simulated staging failure")

    monkeypatch.setattr(module, "_write_tree", fail_staging)

    with pytest.raises(OSError, match="simulated staging failure"):
        module.sync_agent_kit(canonical_skills, packaged_agent_kit)

    assert _tree(packaged_agent_kit) == before
    _assert_no_staging(packaged_agent_kit)


def test_agent_kit_sync_restores_package_when_replacement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical_skills = _canonical_fixture(tmp_path)
    packaged_agent_kit = tmp_path / "package" / "agent_kit"
    _write_kit_authored_files(packaged_agent_kit)
    (packaged_agent_kit / "skills").mkdir()
    (packaged_agent_kit / "skills" / "stale.txt").write_bytes(b"stale skills\n")
    (packaged_agent_kit / "existing.txt").write_bytes(b"preserve\n")
    before = _tree(packaged_agent_kit)
    original_rename = Path.rename

    def fail_staged_replacement(path: Path, target: Path) -> Path:
        if (
            path.name == "skills"
            and path.parent.name.startswith(".agent-kit-sync-")
            and target == packaged_agent_kit / "skills"
        ):
            raise OSError("simulated replacement failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staged_replacement)

    with pytest.raises(OSError, match="simulated replacement failure"):
        module.sync_agent_kit(canonical_skills, packaged_agent_kit)

    assert _tree(packaged_agent_kit) == before
    _assert_no_staging(packaged_agent_kit)


def test_agent_kit_sync_rejects_linked_canonical_root_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical_skills = _canonical_fixture(tmp_path)
    packaged_agent_kit = tmp_path / "package" / "agent_kit"
    _write_kit_authored_files(packaged_agent_kit)
    (packaged_agent_kit / "existing.txt").write_bytes(b"preserve\n")
    before = _tree(packaged_agent_kit)
    original_is_symlink = Path.is_symlink

    def treat_canonical_root_as_link(path: Path) -> bool:
        return path == canonical_skills or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", treat_canonical_root_as_link)

    with pytest.raises(RuntimeError, match="Canonical skills tree cannot be"):
        module.sync_agent_kit(canonical_skills, packaged_agent_kit)

    assert _tree(packaged_agent_kit) == before
    _assert_no_staging(packaged_agent_kit)


def test_agent_kit_sync_rejects_linked_canonical_entry_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical_skills = _canonical_fixture(tmp_path)
    linked_entry = canonical_skills / "sample-skill" / "SKILL.md"
    packaged_agent_kit = tmp_path / "package" / "agent_kit"
    _write_kit_authored_files(packaged_agent_kit)
    (packaged_agent_kit / "existing.txt").write_bytes(b"preserve\n")
    before = _tree(packaged_agent_kit)
    original_is_symlink = Path.is_symlink

    def treat_canonical_entry_as_link(path: Path) -> bool:
        return path == linked_entry or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", treat_canonical_entry_as_link)

    with pytest.raises(RuntimeError, match="Canonical skills tree cannot contain"):
        module.sync_agent_kit(canonical_skills, packaged_agent_kit)

    assert _tree(packaged_agent_kit) == before
    _assert_no_staging(packaged_agent_kit)


def test_agent_kit_sync_rejects_credential_resource_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_sync_module()
    canonical_skills = _canonical_fixture(tmp_path)
    sensitive = canonical_skills / "sample-skill" / "token.txt"
    sensitive.write_bytes(b"SYNC-RESOURCE-CANARY")
    packaged_agent_kit = tmp_path / "package" / "agent_kit"
    _write_kit_authored_files(packaged_agent_kit)
    original_read = Path.read_bytes

    def reject_sensitive_read(path: Path) -> bytes:
        if path == sensitive:
            raise AssertionError("sync read credential-capable resource bytes")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", reject_sensitive_read)

    with pytest.raises(RuntimeError, match="credential-capable resource path"):
        module.sync_agent_kit(canonical_skills, packaged_agent_kit)

    _assert_no_staging(packaged_agent_kit)


def test_agent_kit_is_available_through_importlib_resources() -> None:
    resource_root = importlib.resources.files("workctx.resources.agent_kit")

    assert (
        resource_root.joinpath("skills", "registry.yaml").read_bytes()
        == (CANONICAL_SKILLS / "registry.yaml").read_bytes()
    )
    for bridge_name in BRIDGE_NAMES:
        content = resource_root.joinpath("bridges", bridge_name).read_text(encoding="utf-8")
        assert BRIDGE_SKILL_PATHS[bridge_name] in content
        assert content.encode() == (PACKAGED_AGENT_KIT / "bridges" / bridge_name).read_bytes()
