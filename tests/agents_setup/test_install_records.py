from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from workctx.adapters.agents import _install_records as record_module
from workctx.adapters.agents._install_records import (
    INSTALL_RECORD_FILENAME,
    InstallRecordConflictError,
    InstallRecordError,
    RecoveryDisposition,
    TrustedInstallStore,
)
from workctx.adapters.agents._safe_fs import SafeFilesystemError, SafeRoot
from workctx.adapters.agents.models import AgentClient

_CLAUDE_MANIFEST = ".workctx/agent-adapters/claude/skill-manifest.json"
_GEMINI_MANIFEST = ".workctx/agent-adapters/gemini/skill-manifest.json"
_CODEX_MANIFEST = ".workctx/agent-adapters/codex/skill-manifest.json"


def _hash(character: str) -> str:
    return "sha256:" + character * 64


def _store(_tmp_path: Path) -> TrustedInstallStore:
    return TrustedInstallStore()


def _root(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    return root.resolve()


def _install_stable(
    store: TrustedInstallStore,
    root: Path,
    client: AgentClient,
    manifest_path: str,
    digest: str,
    operations_digest: str,
) -> None:
    observation = store.observe(root, client, manifest_path)
    pending = store.begin_transition(
        observation,
        next_manifest_digest=digest,
        operations_digest=operations_digest,
    )
    resolved = store.resolve_transition(
        pending,
        operations_digest=operations_digest,
        actual_manifest_digest=digest,
    )
    assert resolved.authenticates(digest)


def test_default_path_uses_fixed_platformdirs_user_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "outside-user-config"
    monkeypatch.setattr(
        record_module,
        "user_config_path",
        lambda *_args, **_kwargs: config,
    )

    store = TrustedInstallStore()

    assert store.path == config / INSTALL_RECORD_FILENAME


def test_initial_transition_authenticates_exact_postimage_and_is_canonical(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    desired = _hash("1")
    operations = _hash("a")
    observation = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)

    assert observation.record is None
    assert not observation.authenticates(desired)
    pending = store.begin_transition(
        observation,
        next_manifest_digest=desired,
        operations_digest=operations,
    )
    during = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)

    assert during.has_pending_transition
    assert during.pending == pending
    assert not during.authenticates(desired)
    assert (
        store.verify_recovery(
            pending,
            operations_digest=operations,
            actual_manifest_digest=None,
        )
        is RecoveryDisposition.PREIMAGE
    )
    assert (
        store.verify_recovery(
            pending,
            operations_digest=operations,
            actual_manifest_digest=desired,
        )
        is RecoveryDisposition.POSTIMAGE
    )

    resolved = store.resolve_transition(
        pending,
        operations_digest=operations,
        actual_manifest_digest=desired,
    )
    payload = store.path.read_bytes()
    decoded = json.loads(payload)

    assert resolved.authenticates(desired)
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert decoded == {
        "schema_version": 1,
        "projects": [
            {
                "root": str(root),
                "adapters": [
                    {
                        "adapter": "claude",
                        "manifest_path": _CLAUDE_MANIFEST,
                        "trusted_manifest_digest": desired,
                        "pending_transition": None,
                    }
                ],
            }
        ],
    }
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o077 == 0


def test_initial_transition_rollback_removes_uncommitted_entry(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    operations = _hash("a")
    pending = store.begin_transition(
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST),
        next_manifest_digest=_hash("1"),
        operations_digest=operations,
    )

    resolved = store.resolve_transition(
        pending,
        operations_digest=operations,
        actual_manifest_digest=None,
    )

    assert resolved.record is None
    assert store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST).record is None
    assert json.loads(store.path.read_text(encoding="utf-8"))["projects"] == []


def test_repair_transition_resolves_rollback_and_commit(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    before = _hash("1")
    after = _hash("2")
    _install_stable(store, root, AgentClient.CLAUDE, _CLAUDE_MANIFEST, before, _hash("a"))

    pending = store.begin_transition(
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST),
        next_manifest_digest=after,
        operations_digest=_hash("b"),
    )
    rolled_back = store.resolve_transition(
        pending,
        operations_digest=_hash("b"),
        actual_manifest_digest=before,
    )
    assert rolled_back.authenticates(before)

    pending = store.begin_transition(
        rolled_back,
        next_manifest_digest=after,
        operations_digest=_hash("c"),
    )
    committed = store.resolve_transition(
        pending,
        operations_digest=_hash("c"),
        actual_manifest_digest=after,
    )
    assert committed.authenticates(after)
    assert not committed.authenticates(before)


def test_uninstall_removes_only_selected_client_record(tmp_path: Path) -> None:
    first = _root(tmp_path, "first")
    second = _root(tmp_path, "second")
    store = _store(tmp_path)
    _install_stable(
        store,
        first,
        AgentClient.CLAUDE,
        _CLAUDE_MANIFEST,
        _hash("1"),
        _hash("a"),
    )
    _install_stable(
        store,
        first,
        AgentClient.GEMINI,
        _GEMINI_MANIFEST,
        _hash("2"),
        _hash("b"),
    )
    _install_stable(
        store,
        second,
        AgentClient.CODEX,
        _CODEX_MANIFEST,
        _hash("3"),
        _hash("c"),
    )
    claude = store.observe(first, AgentClient.CLAUDE, _CLAUDE_MANIFEST)
    pending = store.begin_transition(
        claude,
        next_manifest_digest=None,
        operations_digest=_hash("d"),
    )

    removed = store.resolve_transition(
        pending,
        operations_digest=_hash("d"),
        actual_manifest_digest=None,
    )

    assert removed.record is None
    assert store.observe(first, AgentClient.CLAUDE, _CLAUDE_MANIFEST).record is None
    assert store.observe(first, AgentClient.GEMINI, _GEMINI_MANIFEST).authenticates(_hash("2"))
    assert store.observe(second, AgentClient.CODEX, _CODEX_MANIFEST).authenticates(_hash("3"))


def test_selected_entry_cas_rejects_stale_observation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    first = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)
    stale = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)
    store.begin_transition(
        first,
        next_manifest_digest=_hash("1"),
        operations_digest=_hash("a"),
    )

    with pytest.raises(InstallRecordConflictError, match="changed after the dry run"):
        store.begin_transition(
            stale,
            next_manifest_digest=_hash("2"),
            operations_digest=_hash("b"),
        )


def test_other_client_update_does_not_invalidate_selected_entry_cas(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    claude = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)
    _install_stable(
        store,
        root,
        AgentClient.GEMINI,
        _GEMINI_MANIFEST,
        _hash("2"),
        _hash("b"),
    )

    pending = store.begin_transition(
        claude,
        next_manifest_digest=_hash("1"),
        operations_digest=_hash("a"),
    )

    assert pending.transition.to_manifest_digest == _hash("1")
    assert store.observe(root, AgentClient.GEMINI, _GEMINI_MANIFEST).authenticates(_hash("2"))


def test_recovery_rejects_wrong_operations_and_unknown_manifest_without_write(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    pending = store.begin_transition(
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST),
        next_manifest_digest=_hash("1"),
        operations_digest=_hash("a"),
    )
    before = store.path.read_bytes()

    with pytest.raises(InstallRecordConflictError, match="operation set"):
        store.resolve_transition(
            pending,
            operations_digest=_hash("b"),
            actual_manifest_digest=_hash("1"),
        )
    with pytest.raises(InstallRecordConflictError, match="neither trusted"):
        store.resolve_transition(
            pending,
            operations_digest=_hash("a"),
            actual_manifest_digest=_hash("9"),
        )

    assert store.path.read_bytes() == before
    assert store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST).has_pending_transition


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"schema_version":1,"projects":[]}\n',
        b'{"schema_version":true,"projects":[]}\n',
        b'{"schema_version":1,"projects":[],"extra":false}\n',
        (
            b'{"schema_version":1,"projects":[{"root":"relative","adapters":'
            b'[{"adapter":"claude","manifest_path":".workctx/agent-adapters/claude/'
            b'skill-manifest.json","trusted_manifest_digest":"sha256:'
            + b"0" * 64
            + b'","pending_transition":null}]}]}\n'
        ),
    ],
)
def test_malformed_record_store_fails_closed(
    tmp_path: Path,
    payload: bytes,
) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(payload)

    with pytest.raises(InstallRecordError, match="malformed"):
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)


def test_duplicate_adapter_and_pending_invariant_fail_closed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    adapter = {
        "adapter": "claude",
        "manifest_path": _CLAUDE_MANIFEST,
        "trusted_manifest_digest": _hash("1"),
        "pending_transition": None,
    }
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": [{"root": str(root), "adapters": [adapter, adapter]}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(InstallRecordError, match="malformed"):
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)

    adapter["pending_transition"] = {
        "from_manifest_digest": _hash("2"),
        "to_manifest_digest": _hash("3"),
        "operations_digest": _hash("a"),
    }
    store.path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": [{"root": str(root), "adapters": [adapter]}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(InstallRecordError, match="malformed"):
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)


def test_record_leaf_symlink_is_rejected_without_reading_destination(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    canary = tmp_path / "auth-canary.json"
    canary.write_bytes(b"AUTH-CANARY")
    try:
        store.path.symlink_to(canary)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(InstallRecordError, match="unsafe"):
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)

    assert canary.read_bytes() == b"AUTH-CANARY"


def test_guard_symlink_blocks_mutation_without_touching_destination(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    canary = tmp_path / "guard-canary"
    canary.write_bytes(b"GUARD-CANARY")
    guard = store.path.parent / f".{INSTALL_RECORD_FILENAME}.lock"
    try:
        guard.symlink_to(canary)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")
    observation = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)

    with pytest.raises(InstallRecordError, match="guard"):
        store.begin_transition(
            observation,
            next_manifest_digest=_hash("1"),
            operations_digest=_hash("a"),
        )

    assert canary.read_bytes() == b"GUARD-CANARY"
    assert not store.path.exists()


def test_platform_store_inside_project_is_rejected_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    record_file = root / "config" / INSTALL_RECORD_FILENAME
    monkeypatch.setattr(
        record_module,
        "user_config_path",
        lambda *_args, **_kwargs: record_file.parent,
    )
    store = TrustedInstallStore()

    with pytest.raises(InstallRecordError, match="outside every project root"):
        store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)

    assert not record_file.parent.exists()


def test_record_listing_its_own_parent_as_project_is_rejected(tmp_path: Path) -> None:
    current = _root(tmp_path, "current")
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": [
                    {
                        "root": str(store.path.parent.resolve()),
                        "adapters": [
                            {
                                "adapter": "claude",
                                "manifest_path": _CLAUDE_MANIFEST,
                                "trusted_manifest_digest": _hash("1"),
                                "pending_transition": None,
                            }
                        ],
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(InstallRecordError, match="outside every project root"):
        store.observe(current, AgentClient.CLAUDE, _CLAUDE_MANIFEST)


def test_atomic_save_failure_preserves_previous_bytes_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    _install_stable(
        store,
        root,
        AgentClient.CLAUDE,
        _CLAUDE_MANIFEST,
        _hash("1"),
        _hash("a"),
    )
    observation = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)
    before = store.path.read_bytes()
    original_replace = SafeRoot.replace

    def fail_record_replace(
        self: SafeRoot,
        source_relative_path: str,
        target_relative_path: str,
        **kwargs: Any,
    ) -> object:
        if target_relative_path == INSTALL_RECORD_FILENAME:
            raise SafeFilesystemError("injected record replace failure")
        return original_replace(
            self,
            source_relative_path,
            target_relative_path,
            **kwargs,
        )

    monkeypatch.setattr(SafeRoot, "replace", fail_record_replace)

    with pytest.raises(InstallRecordError, match="atomically save"):
        store.begin_transition(
            observation,
            next_manifest_digest=_hash("2"),
            operations_digest=_hash("b"),
        )

    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob(f".{INSTALL_RECORD_FILENAME}.*.tmp"))


def test_noncooperating_record_edit_between_load_and_replace_fails_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    store = _store(tmp_path)
    _install_stable(
        store,
        root,
        AgentClient.CLAUDE,
        _CLAUDE_MANIFEST,
        _hash("1"),
        _hash("a"),
    )
    observation = store.observe(root, AgentClient.CLAUDE, _CLAUDE_MANIFEST)
    concurrent = store.path.read_bytes() + b" "
    original_inspect = SafeRoot.inspect_file
    record_reads = 0

    def edit_before_save_inspection(self: SafeRoot, relative_path: str) -> object:
        nonlocal record_reads
        snapshot = original_inspect(self, relative_path)
        if relative_path == INSTALL_RECORD_FILENAME:
            record_reads += 1
            if record_reads == 2:
                store.path.write_bytes(concurrent)
                snapshot = original_inspect(self, relative_path)
        return snapshot

    monkeypatch.setattr(SafeRoot, "inspect_file", edit_before_save_inspection)

    with pytest.raises(InstallRecordConflictError, match="changed during mutation"):
        store.begin_transition(
            observation,
            next_manifest_digest=_hash("2"),
            operations_digest=_hash("b"),
        )

    assert store.path.read_bytes() == concurrent
    assert not list(store.path.parent.glob(f".{INSTALL_RECORD_FILENAME}.*.tmp"))


def test_store_path_cannot_be_overridden(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        TrustedInstallStore(tmp_path / "auth.json")  # type: ignore[call-arg]
