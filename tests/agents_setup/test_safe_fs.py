from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from workctx.adapters.agents import _safe_fs
from workctx.adapters.agents._safe_fs import (
    FileIdentity,
    SafeRoot,
    UnsafeFilesystemError,
    UnsafePathError,
    collision_key,
    snapshot_matches,
    validate_relative_path,
)


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "..",
        "/absolute",
        "//server/share",
        "C:/drive",
        "C:drive",
        "folder\\file",
        "folder//file",
        "folder/./file",
        "folder/../file",
        "folder/",
        "name.",
        "name ",
        "folder/name. ",
        "nul",
        "NUL.txt",
        "con.config",
        "CON .txt",
        "NUL...txt",
        "aux",
        "prn.md",
        "clock$",
        "conin$",
        "CONOUT$.txt",
        "COM1.log",
        "com\N{SUPERSCRIPT TWO}.txt",
        "LPT9",
        "lpt\N{SUPERSCRIPT THREE}.txt",
        "bad:name",
        "bad?name",
        "control\x1f",
        "nul\0byte",
    ],
)
def test_validate_relative_path_rejects_unsafe_and_nonportable_paths(path: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_relative_path(path)


def test_validate_relative_path_accepts_strict_forward_slash_paths() -> None:
    assert validate_relative_path(
        ".agents/skills/caf\N{LATIN SMALL LETTER E WITH ACUTE}/SKILL.md"
    ) == (".agents/skills/caf\N{LATIN SMALL LETTER E WITH ACUTE}/SKILL.md")
    with pytest.raises(UnsafePathError):
        validate_relative_path(Path("relative"))  # type: ignore[arg-type]


def test_collision_key_uses_complete_path_nfc_and_full_casefold() -> None:
    composed = "Skills/Caf\N{LATIN SMALL LETTER E WITH ACUTE}/STRASSE.md"
    decomposed = "skills/cafe\N{COMBINING ACUTE ACCENT}/stra\N{LATIN SMALL LETTER SHARP S}e.md"
    assert collision_key(composed) == collision_key(decomposed)


def test_directory_methods_alone_accept_dot_for_the_physical_root(tmp_path: Path) -> None:
    safe = SafeRoot(tmp_path)

    identity = safe.require_directory(".")

    assert isinstance(identity, FileIdentity)
    assert safe.ensure_directories(".") == identity
    assert safe.list_directory(".") == ()
    safe.fsync_directory(".")
    with pytest.raises(UnsafePathError):
        safe.inspect_file(".")


def test_inspect_reads_regular_file_from_verified_handle_and_reports_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested = tmp_path / "safe"
    nested.mkdir()
    target = nested / "payload.bin"
    target.write_bytes(b"verified bytes")
    safe = SafeRoot(tmp_path)

    def forbid_path_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("ordinary Path reads must not be used")

    monkeypatch.setattr(Path, "read_bytes", forbid_path_read)
    snapshot = safe.inspect_file("safe/payload.bin")

    assert snapshot.exists
    assert snapshot.identity is not None
    assert snapshot.size == len(b"verified bytes")
    assert snapshot.content == b"verified bytes"
    assert snapshot.content_hash == f"sha256:{hashlib.sha256(b'verified bytes').hexdigest()}"
    assert snapshot.matches(snapshot.identity, snapshot.content_hash)
    assert snapshot_matches(snapshot, snapshot.identity, snapshot.content_hash)

    missing_leaf = safe.inspect_file("safe/missing.txt")
    missing_ancestor = safe.inspect_file("absent/missing.txt")
    assert not missing_leaf.exists
    assert not missing_ancestor.exists
    assert snapshot_matches(missing_leaf, None, None)
    assert missing_leaf.content is None


def test_inspect_entry_reads_only_verified_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested = tmp_path / "safe"
    nested.mkdir()
    target = nested / "payload.bin"
    target.write_bytes(b"bytes must not be read")
    safe = SafeRoot(tmp_path)

    def forbid_content_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("entry inspection must not read file contents")

    monkeypatch.setattr(_safe_fs, "_read_descriptor", forbid_content_read)
    monkeypatch.setattr(_safe_fs, "_windows_read_handle", forbid_content_read)

    file_entry = safe.inspect_entry("safe/payload.bin")
    directory_entry = safe.inspect_entry("safe")

    assert file_entry is not None
    assert file_entry.is_file
    assert file_entry.path == "safe/payload.bin"
    assert file_entry.size == len(b"bytes must not be read")
    assert directory_entry is not None
    assert directory_entry.is_directory
    assert directory_entry.path == "safe"
    assert safe.inspect_entry("safe/missing.bin") is None
    assert safe.inspect_entry("missing/payload.bin") is None


def test_list_directory_returns_sorted_safe_metadata(tmp_path: Path) -> None:
    (tmp_path / "z-directory").mkdir()
    (tmp_path / "A-file.txt").write_bytes(b"a")
    safe = SafeRoot(tmp_path)

    entries = safe.list_directory()

    assert [entry.name for entry in entries] == ["A-file.txt", "z-directory"]
    assert entries[0].is_file
    assert not entries[0].is_directory
    assert entries[0].path == "A-file.txt"
    assert entries[0].size == 1
    assert entries[1].is_directory
    assert not entries[1].is_file


@pytest.mark.skipif(os.name == "nt", reason="case-colliding names cannot coexist on Windows")
def test_list_directory_rejects_nfc_casefold_collisions(tmp_path: Path) -> None:
    (tmp_path / "Name.txt").write_bytes(b"one")
    (tmp_path / "name.TXT").write_bytes(b"two")
    if len(list(tmp_path.iterdir())) < 2:
        # Case-insensitive filesystems (NTFS, default APFS) collapse the pair, so
        # the on-disk collision this guard protects against cannot exist here.
        pytest.skip("filesystem collapses casefold-colliding names")

    with pytest.raises(UnsafeFilesystemError, match="colliding"):
        SafeRoot(tmp_path).list_directory()


def test_create_replace_move_unlink_and_remove_empty_directory(tmp_path: Path) -> None:
    safe = SafeRoot(tmp_path)
    safe.ensure_directories("stage/nested")
    assert safe.require_directory("stage/nested")

    original = safe.write_exclusive("target.txt", b"old")
    assert original.content == b"old"
    with pytest.raises(FileExistsError):
        safe.write_exclusive("target.txt", b"must not overwrite")
    assert safe.inspect_file("target.txt").content == b"old"

    safe.write_exclusive("stage/replacement.tmp", b"new")
    replaced = safe.replace("stage/replacement.tmp", "target.txt")
    assert replaced.content == b"new"
    assert not safe.inspect_file("stage/replacement.tmp").exists

    moved = safe.move("target.txt", "stage/backup.bin")
    assert moved.content == b"new"
    assert not safe.inspect_file("target.txt").exists
    assert safe.inspect_file("stage/backup.bin").content == b"new"

    safe.write_exclusive("occupied.txt", b"owner")
    with pytest.raises(FileExistsError):
        safe.move("stage/backup.bin", "occupied.txt")
    assert safe.inspect_file("stage/backup.bin").content == b"new"
    assert safe.inspect_file("occupied.txt").content == b"owner"

    assert safe.unlink("stage/backup.bin")
    assert not safe.unlink("stage/backup.bin")
    assert safe.remove_empty_directory("stage/nested")
    assert not safe.remove_empty_directory("stage/nested")
    assert safe.remove_empty_directory("stage")


def test_failed_move_cleanup_cannot_delete_a_swapped_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = SafeRoot(tmp_path)
    safe.write_exclusive("source.txt", b"source")
    original_unlink = SafeRoot.unlink
    swapped = False

    def fail_replace(*_args: object, **_kwargs: object) -> object:
        raise OSError("injected replacement failure")

    def swap_before_cleanup(
        self: SafeRoot,
        path: str,
        **preconditions: object,
    ) -> bool:
        nonlocal swapped
        if path == "target.txt" and not swapped:
            swapped = True
            original_unlink(self, path)
            self.write_exclusive(path, b"concurrent owner")
        return original_unlink(self, path, **preconditions)  # type: ignore[arg-type]

    monkeypatch.setattr(SafeRoot, "_replace_bound", fail_replace)
    monkeypatch.setattr(SafeRoot, "unlink", swap_before_cleanup)

    with pytest.raises(UnsafeFilesystemError, match="Unlink precondition changed"):
        safe.move("source.txt", "target.txt")

    assert safe.inspect_file("source.txt").content == b"source"
    assert safe.inspect_file("target.txt").content == b"concurrent owner"


def test_remove_empty_directory_never_recurses(tmp_path: Path) -> None:
    (tmp_path / "owned").mkdir()
    (tmp_path / "owned" / "keep.txt").write_bytes(b"keep")
    safe = SafeRoot(tmp_path)

    with pytest.raises(OSError):
        safe.remove_empty_directory("owned")

    assert (tmp_path / "owned" / "keep.txt").read_bytes() == b"keep"


def test_safe_root_requires_an_existing_directory(tmp_path: Path) -> None:
    file_root = tmp_path / "not-a-directory"
    file_root.write_bytes(b"file")
    with pytest.raises(UnsafeFilesystemError):
        SafeRoot(file_root)

    with pytest.raises(UnsafeFilesystemError):
        SafeRoot(tmp_path / "missing")


def test_symlink_file_leaf_is_rejected_before_bytes(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"external secret-like bytes")
    link = tmp_path / "root" / "linked.txt"
    link.parent.mkdir()
    _create_symlink_or_skip(outside, link, is_directory=False)

    safe = SafeRoot(link.parent)
    with pytest.raises(UnsafeFilesystemError):
        safe.inspect_entry("linked.txt")
    with pytest.raises(UnsafeFilesystemError):
        safe.inspect_file("linked.txt")
    with pytest.raises(UnsafeFilesystemError):
        safe.write_exclusive("linked.txt", b"overwrite")
    with pytest.raises(UnsafeFilesystemError):
        safe.unlink("linked.txt")

    safe.write_exclusive("source.txt", b"replacement")
    with pytest.raises(UnsafeFilesystemError):
        safe.replace("source.txt", "linked.txt")
    assert safe.inspect_file("source.txt").content == b"replacement"
    assert outside.read_bytes() == b"external secret-like bytes"


def test_linked_ancestor_blocks_reads_and_all_external_writes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "existing.txt").write_bytes(b"external")
    link = root / "escape"
    _create_directory_link_or_skip(outside, link)
    safe = SafeRoot(root)

    with pytest.raises(UnsafeFilesystemError):
        safe.inspect_entry("escape/existing.txt")
    with pytest.raises(UnsafeFilesystemError):
        safe.inspect_file("escape/existing.txt")
    with pytest.raises(UnsafeFilesystemError):
        safe.write_exclusive("escape/created.txt", b"must stay inside")
    with pytest.raises(UnsafeFilesystemError):
        safe.ensure_directories("escape/new-directory")

    assert (outside / "existing.txt").read_bytes() == b"external"
    assert not (outside / "created.txt").exists()
    assert not (outside / "new-directory").exists()


@pytest.mark.skipif(os.name == "nt", reason="descriptor-relative race test is POSIX-specific")
def test_inspect_entry_rejects_ancestor_swap_after_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    parent = root / "marker-parent"
    parent.mkdir(parents=True)
    (parent / "marker").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker").write_bytes(b"outside must not be inspected")
    displaced = root / "displaced"
    safe = SafeRoot(root)
    original_open = SafeRoot._try_open_verified_posix_directory
    swapped = False

    def swap_after_parent_open(
        self: SafeRoot,
        parts: tuple[str, ...],
    ) -> int | None:
        nonlocal swapped
        descriptor = original_open(self, parts)
        if parts == ("marker-parent",) and not swapped:
            swapped = True
            parent.rename(displaced)
            parent.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(SafeRoot, "_try_open_verified_posix_directory", swap_after_parent_open)

    with pytest.raises(UnsafeFilesystemError):
        safe.inspect_entry("marker-parent/marker")

    assert swapped
    assert (outside / "marker").read_bytes() == b"outside must not be inspected"


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows reparse-point form")
def test_windows_junction_is_rejected_as_an_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    junction = root / "junction"
    _create_windows_junction_or_skip(outside, junction)

    with pytest.raises(UnsafeFilesystemError):
        SafeRoot(root).inspect_file("junction/file.txt")


@pytest.mark.skipif(os.name == "nt", reason="FIFO creation is POSIX-specific")
def test_posix_nonregular_leaf_and_ancestor_are_rejected(tmp_path: Path) -> None:
    leaf = tmp_path / "pipe"
    os.mkfifo(leaf)
    safe = SafeRoot(tmp_path)

    with pytest.raises(UnsafeFilesystemError):
        safe.inspect_file("pipe")
    with pytest.raises(UnsafeFilesystemError):
        safe.inspect_file("pipe/child")
    with pytest.raises(UnsafeFilesystemError):
        safe.list_directory()


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create these invalid names")
@pytest.mark.parametrize("name", ["NUL", "trailing.", "trailing "])
def test_posix_listing_rejects_existing_windows_invalid_names(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"unmanaged")

    with pytest.raises(UnsafeFilesystemError, match="unsafe entry name"):
        SafeRoot(tmp_path).list_directory()


@pytest.mark.skipif(os.name != "nt", reason="Windows has bounded sharing-violation retries")
def test_windows_replace_retries_permission_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = SafeRoot(tmp_path)
    safe.write_exclusive("source", b"new")
    safe.write_exclusive("target", b"old")
    real_replace = os.replace
    calls = 0

    def flaky_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError("sharing violation")
        real_replace(source, target)

    monkeypatch.setattr(_safe_fs.os, "replace", flaky_replace)
    monkeypatch.setattr(_safe_fs.time, "sleep", lambda _delay: None)

    assert safe.replace("source", "target").content == b"new"
    assert calls == 3


def _create_symlink_or_skip(target: Path, link: Path, *, is_directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")


def _create_directory_link_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")
        _create_windows_junction_or_skip(target, link)


def _create_windows_junction_or_skip(target: Path, junction: Path) -> None:
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {completed.stderr or completed.stdout}")
