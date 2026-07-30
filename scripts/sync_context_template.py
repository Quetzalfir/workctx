from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TEMPLATE = REPOSITORY_ROOT / "src" / "workctx" / "resources" / "context_template"
GENERATED_MIRROR = REPOSITORY_ROOT / "templates" / "context"


def _tree_entries(root: Path) -> tuple[set[str], dict[str, bytes]]:
    if root.is_symlink():
        raise RuntimeError(f"Template tree root cannot be a symbolic link: {root}")
    directories: set[str] = set()
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Template trees cannot contain symbolic links: {path}")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            directories.add(relative)
        elif path.is_file():
            files[relative] = path.read_bytes()
    return directories, files


def template_differences(
    canonical_template: Path = CANONICAL_TEMPLATE,
    generated_mirror: Path = GENERATED_MIRROR,
) -> list[str]:
    canonical_directories, canonical_files = _tree_entries(canonical_template)
    mirror_directories, mirror_files = _tree_entries(generated_mirror)
    differences = [
        *(
            f"missing directory: {path}"
            for path in sorted(canonical_directories - mirror_directories)
        ),
        *(
            f"extra directory: {path}"
            for path in sorted(mirror_directories - canonical_directories)
        ),
        *(f"missing file: {path}" for path in sorted(canonical_files.keys() - mirror_files.keys())),
        *(f"extra file: {path}" for path in sorted(mirror_files.keys() - canonical_files.keys())),
        *(
            f"different file: {path}"
            for path in sorted(canonical_files.keys() & mirror_files.keys())
            if canonical_files[path] != mirror_files[path]
        ),
    ]
    return differences


def sync_context_template(
    canonical_template: Path = CANONICAL_TEMPLATE,
    generated_mirror: Path = GENERATED_MIRROR,
) -> None:
    if canonical_template.is_symlink():
        raise RuntimeError(f"Canonical template cannot be a symbolic link: {canonical_template}")
    if not canonical_template.is_dir():
        raise RuntimeError(f"Canonical template tree is missing: {canonical_template}")
    canonical_entries = _tree_entries(canonical_template)
    if generated_mirror.is_symlink():
        raise RuntimeError(f"Generated mirror cannot be a symbolic link: {generated_mirror}")
    if generated_mirror.exists() and not generated_mirror.is_dir():
        raise RuntimeError(f"Generated mirror is not a directory: {generated_mirror}")

    staging_container = Path(
        tempfile.mkdtemp(prefix=".context-template-sync-", dir=generated_mirror.parent)
    )
    staged_mirror = staging_container / "context"
    previous_mirror = staging_container / "previous"
    cleanup_staging = True
    try:
        shutil.copytree(
            canonical_template,
            staged_mirror,
            symlinks=True,
            copy_function=shutil.copyfile,
        )
        if _tree_entries(staged_mirror) != canonical_entries:
            raise RuntimeError("Staged context template differs from the canonical tree")

        if generated_mirror.exists():
            generated_mirror.rename(previous_mirror)
        try:
            staged_mirror.rename(generated_mirror)
        except Exception:
            if previous_mirror.exists() and not generated_mirror.exists():
                try:
                    previous_mirror.rename(generated_mirror)
                except Exception:
                    cleanup_staging = False
                    raise
            raise
    finally:
        if cleanup_staging and staging_container.exists():
            shutil.rmtree(staging_container)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize the public context template from the packaged canonical tree."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without modifying the generated mirror.",
    )
    args = parser.parse_args()

    if args.check:
        differences = template_differences()
        if differences:
            for difference in differences:
                print(difference)
            return 1
        print("Context template mirror is synchronized.")
        return 0

    sync_context_template()
    differences = template_differences()
    if differences:
        for difference in differences:
            print(difference)
        return 1
    print(f"Synchronized {GENERATED_MIRROR.relative_to(REPOSITORY_ROOT).as_posix()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
