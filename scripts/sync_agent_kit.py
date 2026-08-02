from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from workctx.adapters.agents.errors import InvalidAdapterStateError
from workctx.adapters.agents.sources import (
    _validate_resource_content,
    _validate_resource_path,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SKILLS = REPOSITORY_ROOT / ".agents" / "skills"
PACKAGED_AGENT_KIT = REPOSITORY_ROOT / "src" / "workctx" / "resources" / "agent_kit"

type TreeEntries = tuple[set[str], dict[str, bytes]]


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _tree_entries(
    root: Path,
    *,
    description: str,
    allow_missing: bool = False,
    ignore_runtime_cache: bool = False,
    skill_layout: str | None = None,
) -> TreeEntries:
    if _is_link(root):
        raise RuntimeError(f"{description} cannot be a symbolic link or junction: {root}")
    if not root.exists():
        if allow_missing:
            return set(), {}
        raise RuntimeError(f"{description} is missing: {root}")
    if not root.is_dir():
        raise RuntimeError(f"{description} is not a directory: {root}")

    directories: set[str] = set()
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        relative_path = path.relative_to(root)
        if ignore_runtime_cache and ("__pycache__" in relative_path.parts or path.suffix == ".pyc"):
            continue
        if _is_link(path):
            raise RuntimeError(f"{description} cannot contain symbolic links or junctions: {path}")
        relative = relative_path.as_posix()
        if path.is_dir():
            directories.add(relative)
        elif path.is_file():
            resource_parts: tuple[str, ...] | None = None
            if (
                skill_layout == "canonical"
                and len(relative_path.parts) >= 2
                and relative_path.parts[1:] != ("SKILL.md",)
            ):
                resource_parts = relative_path.parts[1:]
            resource_relative: str | None = None
            if resource_parts is not None:
                skill_index = 0
                try:
                    resource_relative = _validate_resource_path(
                        relative_path.parts[skill_index],
                        "/".join(resource_parts),
                    )
                except InvalidAdapterStateError as error:
                    raise RuntimeError(
                        f"{description} contains an invalid resource: {error}"
                    ) from error
            content = path.read_bytes()
            if resource_relative is not None:
                _validate_resource_content(
                    relative_path.parts[skill_index],
                    resource_relative,
                    content,
                )
            files[relative] = content
        else:
            raise RuntimeError(f"{description} contains an unsupported entry: {path}")
    return directories, files


def _expected_entries(canonical_skills: Path) -> TreeEntries:
    return _tree_entries(
        canonical_skills,
        description="Canonical skills tree",
        skill_layout="canonical",
    )


def _packaged_entries(packaged_agent_kit: Path) -> TreeEntries:
    if _is_link(packaged_agent_kit):
        raise RuntimeError(
            f"Packaged agent kit cannot be a symbolic link or junction: {packaged_agent_kit}"
        )
    if packaged_agent_kit.exists() and not packaged_agent_kit.is_dir():
        raise RuntimeError(f"Packaged agent kit is not a directory: {packaged_agent_kit}")
    return _tree_entries(
        packaged_agent_kit / "skills",
        description="Packaged agent skills tree",
        allow_missing=True,
        ignore_runtime_cache=True,
        skill_layout="canonical",
    )


def _differences(expected: TreeEntries, actual: TreeEntries) -> list[str]:
    expected_directories, expected_files = expected
    actual_directories, actual_files = actual
    return [
        *(
            f"missing directory: {path}"
            for path in sorted(expected_directories - actual_directories)
        ),
        *(f"extra directory: {path}" for path in sorted(actual_directories - expected_directories)),
        *(f"missing file: {path}" for path in sorted(expected_files.keys() - actual_files.keys())),
        *(f"extra file: {path}" for path in sorted(actual_files.keys() - expected_files.keys())),
        *(
            f"different file: {path}"
            for path in sorted(expected_files.keys() & actual_files.keys())
            if expected_files[path] != actual_files[path]
        ),
    ]


def agent_kit_differences(
    canonical_skills: Path = CANONICAL_SKILLS,
    packaged_agent_kit: Path = PACKAGED_AGENT_KIT,
) -> list[str]:
    """Report drift only for the canonically synchronized skills subtree."""

    expected = _expected_entries(canonical_skills)
    actual = _packaged_entries(packaged_agent_kit)
    return _differences(expected, actual)


def _write_tree(root: Path, entries: TreeEntries) -> None:
    directories, files = entries
    root.mkdir()
    for relative in sorted(directories, key=lambda value: (value.count("/"), value)):
        (root / relative).mkdir()
    for relative, content in sorted(files.items()):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def sync_agent_kit(
    canonical_skills: Path = CANONICAL_SKILLS,
    packaged_agent_kit: Path = PACKAGED_AGENT_KIT,
) -> bool:
    """Synchronize skills while preserving kit-authored bridges and package metadata."""

    expected = _expected_entries(canonical_skills)
    actual = _packaged_entries(packaged_agent_kit)
    if actual == expected:
        return False

    packaged_parent = packaged_agent_kit.parent
    if _is_link(packaged_parent):
        raise RuntimeError(
            f"Packaged agent kit parent cannot be a symbolic link or junction: {packaged_parent}"
        )
    if packaged_parent.exists() and not packaged_parent.is_dir():
        raise RuntimeError(f"Packaged agent kit parent is not a directory: {packaged_parent}")
    packaged_parent.mkdir(parents=True, exist_ok=True)
    if _is_link(packaged_agent_kit):
        raise RuntimeError(
            f"Packaged agent kit cannot be a symbolic link or junction: {packaged_agent_kit}"
        )
    if packaged_agent_kit.exists() and not packaged_agent_kit.is_dir():
        raise RuntimeError(f"Packaged agent kit is not a directory: {packaged_agent_kit}")
    packaged_agent_kit.mkdir(exist_ok=True)

    staging_container = Path(tempfile.mkdtemp(prefix=".agent-kit-sync-", dir=packaged_agent_kit))
    staged_skills = staging_container / "skills"
    previous_skills = staging_container / "previous"
    packaged_skills = packaged_agent_kit / "skills"
    cleanup_staging = True
    try:
        _write_tree(staged_skills, expected)
        staged_entries = _tree_entries(
            staged_skills,
            description="Staged packaged agent skills tree",
            skill_layout="canonical",
        )
        if staged_entries != expected:
            raise RuntimeError("Staged agent skills differ from the canonical sources")

        if packaged_skills.exists():
            packaged_skills.rename(previous_skills)
        try:
            staged_skills.rename(packaged_skills)
        except Exception:
            if previous_skills.exists() and not packaged_skills.exists():
                try:
                    previous_skills.rename(packaged_skills)
                except Exception:
                    cleanup_staging = False
                    raise
            raise
    finally:
        if cleanup_staging and staging_container.exists():
            shutil.rmtree(staging_container)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize the packaged agent kit from canonical repository sources."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without modifying the packaged agent kit.",
    )
    args = parser.parse_args()

    if args.check:
        differences = agent_kit_differences()
        if differences:
            for difference in differences:
                print(difference)
            return 1
        print("Agent kit is synchronized.")
        return 0

    changed = sync_agent_kit()
    differences = agent_kit_differences()
    if differences:
        for difference in differences:
            print(difference)
        return 1
    if changed:
        relative = (PACKAGED_AGENT_KIT / "skills").relative_to(REPOSITORY_ROOT).as_posix()
        print(f"Synchronized {relative}.")
    else:
        print("Agent kit is already synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
