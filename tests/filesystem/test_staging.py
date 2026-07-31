from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from workctx.adapters.filesystem import lock as lock_module
from workctx.adapters.filesystem import staging as staging_module
from workctx.adapters.filesystem.lock import ContextLock, LockFenceError
from workctx.adapters.filesystem.staging import (
    InvalidIntentError,
    RecoverableReplaceError,
    RecoveryRequiredError,
    RecoveryState,
    ReplaceRetryPolicy,
    StagedReplacement,
    StagedWrite,
    atomic_replace_bytes,
)
from workctx.errors import ContextBoundaryError
from workctx.services.contexts import initialize_context


@pytest.fixture
def context_root(tmp_path: Path) -> Path:
    root = tmp_path / "context"
    initialize_context(root, name="Staging Context", context_id="staging-context")
    return root


def test_prepare_fsyncs_ordered_relative_intent_and_inspection_reports_prepared(
    context_root: Path,
) -> None:
    holder = ContextLock.acquire(context_root, session_id="prepare", tool_version="test")
    stager = StagedReplacement(context_root)

    intent = stager.prepare(
        "TXN-001",
        holder.nonce,
        [
            StagedWrite("02_knowledge/one.md", b"one\n"),
            StagedWrite("03_work/two.md", b"two\n"),
        ],
        lock=holder,
    )

    raw = json.loads((context_root / "98_state" / "staging" / "intent.json").read_text())
    assert raw["transaction_id"] == "TXN-001"
    assert [target["target"] for target in raw["targets"]] == [
        "02_knowledge/one.md",
        "03_work/two.md",
    ]
    assert all(not Path(target["staged"]).is_absolute() for target in raw["targets"])
    inspection = stager.inspect_recovery()
    assert inspection.state is RecoveryState.PREPARED
    assert inspection.intent == intent
    assert inspection.pending_targets == ("02_knowledge/one.md", "03_work/two.md")
    holder.release()


def test_prepare_fsyncs_transaction_parent_before_publishing_intent(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = ContextLock.acquire(context_root, session_id="parent-fsync", tool_version="test")
    observed: list[Path] = []
    original_fsync = staging_module._fsync_directory

    def record_fsync(path: Path) -> None:
        observed.append(path)
        original_fsync(path)

    monkeypatch.setattr(staging_module, "_fsync_directory", record_fsync)
    intent = StagedReplacement(context_root).prepare(
        "TXN-PARENT-FSYNC",
        holder.nonce,
        [StagedWrite("02_knowledge/parent-fsync.md", b"new\n")],
        lock=holder,
    )

    transaction_parent = Path(context_root, intent.targets[0].staged).parent.parent
    assert transaction_parent in observed
    holder.release()


def test_injected_mid_sequence_failure_retains_intent_and_reports_exact_partial_state(
    context_root: Path,
) -> None:
    first = context_root / "02_knowledge" / "first.md"
    second = context_root / "03_work" / "second.md"
    first.write_bytes(b"old-first\n")
    second.write_bytes(b"old-second\n")
    calls = 0

    def fail_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected crash between replacements")
        os.replace(source, target)

    holder = ContextLock.acquire(context_root, session_id="partial", tool_version="test")
    stager = StagedReplacement(context_root, replace_function=fail_second)
    intent = stager.prepare(
        "TXN-PARTIAL",
        holder.nonce,
        [
            StagedWrite("02_knowledge/first.md", b"new-first\n"),
            StagedWrite("03_work/second.md", b"new-second\n"),
        ],
        lock=holder,
    )

    with pytest.raises(OSError, match="injected crash"):
        stager.apply(intent, lock=holder)

    inspection = stager.inspect_recovery()
    assert inspection.state is RecoveryState.PARTIALLY_APPLIED
    assert inspection.applied_targets == ("02_knowledge/first.md",)
    assert inspection.pending_targets == ("03_work/second.md",)
    assert first.read_bytes() == b"new-first\n"
    assert second.read_bytes() == b"old-second\n"
    assert (context_root / "98_state" / "staging" / "intent.json").is_file()
    assert Path(context_root, intent.targets[1].staged).is_file()
    holder.release()


def test_permission_error_retries_are_bounded_and_leave_recoverable_state(
    context_root: Path,
) -> None:
    target = context_root / "02_knowledge" / "blocked.md"
    target.write_bytes(b"old\n")
    attempts = 0
    delays: list[float] = []

    def always_blocked(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("injected sharing violation")

    policy = ReplaceRetryPolicy(max_attempts=10, initial_delay_seconds=0.01, multiplier=2)
    holder = ContextLock.acquire(context_root, session_id="retry", tool_version="test")
    stager = StagedReplacement(
        context_root,
        retry_policy=policy,
        replace_function=always_blocked,
        sleep_function=delays.append,
    )
    intent = stager.prepare(
        "TXN-RETRY",
        holder.nonce,
        [StagedWrite("02_knowledge/blocked.md", b"new\n")],
        lock=holder,
    )

    with pytest.raises(RecoverableReplaceError, match="10 attempts"):
        stager.apply(intent, lock=holder)

    assert attempts == 10
    assert delays == pytest.approx([0.01 * (2**index) for index in range(9)])
    assert sum(delays) == pytest.approx(5.11)
    assert target.read_bytes() == b"old\n"
    assert stager.inspect_recovery().state is RecoveryState.PREPARED
    holder.release()


def test_transient_permission_error_retries_then_succeeds(context_root: Path) -> None:
    target = context_root / "02_knowledge" / "transient.md"
    target.write_bytes(b"old\n")
    attempts = 0
    delays: list[float] = []

    def transient(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("transient")
        os.replace(source, destination)

    holder = ContextLock.acquire(context_root, session_id="transient", tool_version="test")
    stager = StagedReplacement(
        context_root,
        replace_function=transient,
        sleep_function=delays.append,
    )
    intent = stager.prepare(
        "TXN-TRANSIENT",
        holder.nonce,
        [StagedWrite("02_knowledge/transient.md", b"new\n")],
        lock=holder,
    )
    stager.apply(intent, lock=holder)

    assert attempts == 3
    assert delays == [0.01, 0.02]
    assert target.read_bytes() == b"new\n"
    assert stager.inspect_recovery().state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
    holder.release()


def test_successful_apply_keeps_intent_until_explicit_post_audit_finalize(
    context_root: Path,
) -> None:
    holder = ContextLock.acquire(context_root, session_id="finalize", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-FINAL",
        holder.nonce,
        [StagedWrite("02_knowledge/final.md", b"postimage\n")],
        lock=holder,
    )
    stager.apply(intent, lock=holder)

    assert stager.inspect_recovery().state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
    stager.finalize_after_audit("TXN-FINAL", lock=holder)

    inspection = stager.inspect_recovery()
    assert inspection.state is RecoveryState.CLEAN
    assert inspection.orphan_staging == ()
    assert not (context_root / "98_state" / "staging" / "intent.json").exists()
    holder.release()


def test_takeover_before_apply_fence_aborts_without_replacing_targets(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "02_knowledge" / "fenced.md"
    target.write_bytes(b"old\n")
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-FENCED",
        old.nonce,
        [StagedWrite("02_knowledge/fenced.md", b"new\n")],
        lock=old,
    )
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
    new = ContextLock.acquire(context_root, session_id="new", tool_version="test")

    with pytest.raises(LockFenceError):
        stager.apply(intent, lock=old)

    assert target.read_bytes() == b"old\n"
    assert stager.inspect_recovery().state is RecoveryState.PREPARED
    new.release()


def test_normal_apply_rejects_successor_but_explicit_recovery_can_complete(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "02_knowledge" / "successor.md"
    target.write_bytes(b"old\n")
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-SUCCESSOR",
        old.nonce,
        [StagedWrite("02_knowledge/successor.md", b"new\n")],
        lock=old,
    )
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
    successor = ContextLock.acquire(context_root, session_id="successor", tool_version="test")

    with pytest.raises(LockFenceError, match="intent nonce"):
        stager.apply(intent, lock=successor)

    stager.complete_recovery(intent, lock=successor)
    assert target.read_bytes() == b"new\n"
    stager.finalize_recovery_after_audit("TXN-SUCCESSOR", lock=successor)
    assert stager.inspect_recovery().state is RecoveryState.CLEAN
    successor.release()


def test_takeover_mid_sequence_is_detectable_from_old_nonce_and_partial_intent(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = context_root / "02_knowledge" / "first-after-fence.md"
    second = context_root / "03_work" / "second-after-fence.md"
    first.write_bytes(b"old-first\n")
    second.write_bytes(b"old-second\n")
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    successor: ContextLock | None = None
    calls = 0

    def take_over_between_replaces(source: Path, target: Path) -> None:
        nonlocal calls, successor
        calls += 1
        if calls == 1:
            os.replace(source, target)
            monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
            successor = ContextLock.acquire(context_root, session_id="new", tool_version="test")
            return
        raise OSError("old holder resumed after takeover")

    stager = StagedReplacement(context_root, replace_function=take_over_between_replaces)
    intent = stager.prepare(
        "TXN-MID-TAKEOVER",
        old.nonce,
        [
            StagedWrite("02_knowledge/first-after-fence.md", b"new-first\n"),
            StagedWrite("03_work/second-after-fence.md", b"new-second\n"),
        ],
        lock=old,
    )

    with pytest.raises(LockFenceError):
        stager.apply(intent, lock=old)

    assert successor is not None
    assert calls == 1
    inspection = stager.inspect_recovery()
    assert inspection.state is RecoveryState.PARTIALLY_APPLIED
    assert inspection.intent is not None
    assert inspection.intent.nonce == old.nonce
    assert inspection.intent.nonce != successor.nonce
    assert inspection.applied_targets == ("02_knowledge/first-after-fence.md",)
    assert inspection.pending_targets == ("03_work/second-after-fence.md",)
    successor.release()


def test_recovery_reports_conflict_when_staged_postimage_is_lost(context_root: Path) -> None:
    holder = ContextLock.acquire(context_root, session_id="conflict", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-CONFLICT",
        holder.nonce,
        [StagedWrite("02_knowledge/conflict.md", b"new\n")],
        lock=holder,
    )
    Path(context_root, intent.targets[0].staged).unlink()

    inspection = stager.inspect_recovery()

    assert inspection.state is RecoveryState.RECOVERY_CONFLICT
    assert inspection.conflicted_targets == ("02_knowledge/conflict.md",)
    holder.release()


def test_intent_retains_verified_preimages_and_supports_partial_rollback(
    context_root: Path,
) -> None:
    existing = context_root / "02_knowledge" / "rollback-existing.md"
    created = context_root / "03_work" / "rollback-created.md"
    existing.write_bytes(b"old-existing\n")
    calls = 0

    def fail_second_once(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected partial apply")
        os.replace(source, target)

    holder = ContextLock.acquire(context_root, session_id="rollback", tool_version="test")
    stager = StagedReplacement(context_root, replace_function=fail_second_once)
    intent = stager.prepare(
        "TXN-ROLLBACK",
        holder.nonce,
        [
            StagedWrite("02_knowledge/rollback-existing.md", b"new-existing\n"),
            StagedWrite("03_work/rollback-created.md", b"new-created\n"),
        ],
        lock=holder,
    )

    assert intent.targets[0].preimage_hash is not None
    assert intent.targets[0].backup is not None
    assert Path(context_root, intent.targets[0].backup).read_bytes() == b"old-existing\n"
    assert intent.targets[1].preimage_hash is None
    assert intent.targets[1].backup is None

    with pytest.raises(OSError, match="partial apply"):
        stager.apply(intent, lock=holder)
    assert stager.inspect_recovery().state is RecoveryState.PARTIALLY_APPLIED

    stager.rollback(intent, lock=holder)
    assert existing.read_bytes() == b"old-existing\n"
    assert not created.exists()
    assert stager.inspect_recovery().state is RecoveryState.PREPARED

    stager.finalize_rollback_after_audit("TXN-ROLLBACK", lock=holder)
    assert stager.inspect_recovery().state is RecoveryState.CLEAN
    holder.release()


def test_rollback_recreates_consumed_postimages_for_a_later_completion(
    context_root: Path,
) -> None:
    existing = context_root / "02_knowledge" / "roundtrip-existing.md"
    created = context_root / "03_work" / "roundtrip-created.md"
    existing.write_bytes(b"old\n")
    holder = ContextLock.acquire(context_root, session_id="roundtrip", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-ROUNDTRIP",
        holder.nonce,
        [
            StagedWrite("02_knowledge/roundtrip-existing.md", b"new\n"),
            StagedWrite("03_work/roundtrip-created.md", b"created\n"),
        ],
        lock=holder,
    )
    stager.apply(intent, lock=holder)

    assert all(not Path(context_root, target.staged).exists() for target in intent.targets)
    stager.rollback(intent, lock=holder)
    assert existing.read_bytes() == b"old\n"
    assert not created.exists()
    assert all(Path(context_root, target.staged).is_file() for target in intent.targets)

    stager.apply(intent, lock=holder)
    assert existing.read_bytes() == b"new\n"
    assert created.read_bytes() == b"created\n"
    holder.release()


def test_corrupt_preimage_backup_is_a_recovery_conflict(context_root: Path) -> None:
    target = context_root / "02_knowledge" / "backup-conflict.md"
    target.write_bytes(b"old\n")
    holder = ContextLock.acquire(context_root, session_id="backup-conflict", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-BACKUP-CONFLICT",
        holder.nonce,
        [StagedWrite("02_knowledge/backup-conflict.md", b"new\n")],
        lock=holder,
    )
    backup = intent.targets[0].backup
    assert backup is not None
    Path(context_root, backup).write_bytes(b"corrupt\n")

    assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
    with pytest.raises(InvalidIntentError, match="Preimage backup"):
        stager.apply(intent, lock=holder)
    assert target.read_bytes() == b"old\n"
    holder.release()


def test_target_drift_after_prepare_is_not_overwritten(context_root: Path) -> None:
    target = context_root / "02_knowledge" / "manual-edit.md"
    target.write_bytes(b"old\n")
    holder = ContextLock.acquire(context_root, session_id="manual-edit", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-MANUAL-EDIT",
        holder.nonce,
        [StagedWrite("02_knowledge/manual-edit.md", b"new\n")],
        lock=holder,
    )
    target.write_bytes(b"manual\n")

    with pytest.raises(RecoveryRequiredError, match="both recorded images"):
        stager.apply(intent, lock=holder)
    assert target.read_bytes() == b"manual\n"
    assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
    holder.release()


def test_target_edit_during_permission_retry_is_not_overwritten(context_root: Path) -> None:
    target = context_root / "02_knowledge" / "retry-edit.md"
    target.write_bytes(b"old\n")
    attempts = 0

    def edit_then_block(_source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        destination.write_bytes(b"manual\n")
        raise PermissionError("injected sharing violation")

    holder = ContextLock.acquire(context_root, session_id="retry-edit", tool_version="test")
    stager = StagedReplacement(
        context_root,
        replace_function=edit_then_block,
        sleep_function=lambda _seconds: None,
    )
    intent = stager.prepare(
        "TXN-RETRY-EDIT",
        holder.nonce,
        [StagedWrite("02_knowledge/retry-edit.md", b"new\n")],
        lock=holder,
    )

    with pytest.raises(RecoveryRequiredError, match="changed during atomic replacement"):
        stager.apply(intent, lock=holder)
    assert attempts == 1
    assert target.read_bytes() == b"manual\n"
    assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
    holder.release()


def test_takeover_during_replace_retry_rejects_old_holder(context_root: Path) -> None:
    target = context_root / "02_knowledge" / "retry-takeover.md"
    target.write_bytes(b"old\n")
    old = ContextLock.acquire(context_root, session_id="retry-old", tool_version="test")
    successor: ContextLock | None = None
    attempts = 0

    def take_over_then_block(_source: Path, _destination: Path) -> None:
        nonlocal attempts, successor
        attempts += 1
        monkeypatch_pid = lock_module._pid_is_alive
        lock_module._pid_is_alive = lambda _pid: False
        try:
            successor = ContextLock.acquire(
                context_root,
                session_id="retry-successor",
                tool_version="test",
            )
        finally:
            lock_module._pid_is_alive = monkeypatch_pid
        raise PermissionError("injected sharing violation")

    stager = StagedReplacement(
        context_root,
        replace_function=take_over_then_block,
        sleep_function=lambda _seconds: None,
    )
    intent = stager.prepare(
        "TXN-RETRY-TAKEOVER",
        old.nonce,
        [StagedWrite("02_knowledge/retry-takeover.md", b"new\n")],
        lock=old,
    )

    with pytest.raises(LockFenceError):
        stager.apply(intent, lock=old)
    assert successor is not None
    assert attempts == 1
    assert target.read_bytes() == b"old\n"
    assert stager.inspect_recovery().state is RecoveryState.PREPARED
    successor.release()


def test_fence_is_rechecked_after_source_hash_before_replace(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "02_knowledge" / "hash-window-takeover.md"
    target.write_bytes(b"old\n")
    old = ContextLock.acquire(context_root, session_id="hash-old", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-HASH-WINDOW",
        old.nonce,
        [StagedWrite("02_knowledge/hash-window-takeover.md", b"new\n")],
        lock=old,
    )
    staged = Path(context_root, intent.targets[0].staged)
    original_require_source = staging_module._require_source_hash
    successor: ContextLock | None = None

    def hash_then_take_over(source: Path, expected_hash: str) -> None:
        nonlocal successor
        original_require_source(source, expected_hash)
        if source == staged and successor is None:
            monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
            successor = ContextLock.acquire(
                context_root,
                session_id="hash-successor",
                tool_version="test",
            )

    monkeypatch.setattr(staging_module, "_require_source_hash", hash_then_take_over)
    with pytest.raises(LockFenceError):
        stager.apply(intent, lock=old)

    assert successor is not None
    assert target.read_bytes() == b"old\n"
    successor.release()


def test_staged_source_edit_during_retry_is_not_published(context_root: Path) -> None:
    target = context_root / "02_knowledge" / "retry-source.md"
    target.write_bytes(b"old\n")
    attempts = 0

    def corrupt_source_then_block(source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        source.write_bytes(b"tampered\n")
        raise PermissionError("injected sharing violation")

    holder = ContextLock.acquire(context_root, session_id="retry-source", tool_version="test")
    stager = StagedReplacement(
        context_root,
        replace_function=corrupt_source_then_block,
        sleep_function=lambda _seconds: None,
    )
    intent = stager.prepare(
        "TXN-RETRY-SOURCE",
        holder.nonce,
        [StagedWrite("02_knowledge/retry-source.md", b"new\n")],
        lock=holder,
    )

    with pytest.raises(InvalidIntentError, match="Staged source changed"):
        stager.apply(intent, lock=holder)
    assert attempts == 1
    assert target.read_bytes() == b"old\n"
    assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
    holder.release()


def test_single_file_retry_rejects_holder_after_takeover(context_root: Path) -> None:
    target = context_root / "02_knowledge" / "single-retry-takeover.md"
    target.write_bytes(b"old\n")
    old = ContextLock.acquire(context_root, session_id="single-old", tool_version="test")
    successor: ContextLock | None = None
    attempts = 0

    def take_over_then_block(_source: Path, _destination: Path) -> None:
        nonlocal attempts, successor
        attempts += 1
        original_pid_check = lock_module._pid_is_alive
        lock_module._pid_is_alive = lambda _pid: False
        try:
            successor = ContextLock.acquire(
                context_root,
                session_id="single-successor",
                tool_version="test",
            )
        finally:
            lock_module._pid_is_alive = original_pid_check
        raise PermissionError("injected sharing violation")

    with pytest.raises(LockFenceError):
        atomic_replace_bytes(
            context_root,
            "02_knowledge/single-retry-takeover.md",
            b"new\n",
            nonce=old.nonce,
            lock=old,
            replace_function=take_over_then_block,
            sleep_function=lambda _seconds: None,
        )

    assert successor is not None
    assert attempts == 1
    assert target.read_bytes() == b"old\n"
    successor.release()


def test_rollback_rebuild_is_atomic_and_resumable_after_torn_temp_write(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "02_knowledge" / "torn-rebuild.md"
    target.write_bytes(b"old\n")
    holder = ContextLock.acquire(context_root, session_id="torn-rebuild", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-TORN-REBUILD",
        holder.nonce,
        [StagedWrite("02_knowledge/torn-rebuild.md", b"new\n")],
        lock=holder,
    )
    stager.apply(intent, lock=holder)
    staged = Path(context_root, intent.targets[0].staged)
    assert not staged.exists()

    original_write = staging_module._write_fsynced
    injected = False

    def tear_rebuild(path: Path, payload: bytes, *, exclusive: bool) -> None:
        nonlocal injected
        if ".rebuild-" in path.name and not injected:
            injected = True
            path.write_bytes(payload[:1])
            raise OSError("injected torn reconstruction")
        original_write(path, payload, exclusive=exclusive)

    monkeypatch.setattr(staging_module, "_write_fsynced", tear_rebuild)
    with pytest.raises(OSError, match="torn reconstruction"):
        stager.rollback(intent, lock=holder)

    assert target.read_bytes() == b"new\n"
    assert not staged.exists()
    assert stager.inspect_recovery().state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT

    monkeypatch.setattr(staging_module, "_write_fsynced", original_write)
    stager.rollback(intent, lock=holder)
    assert target.read_bytes() == b"old\n"
    assert staged.read_bytes() == b"new\n"
    holder.release()


def test_rollback_rebuild_retry_rejects_tampered_source(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "02_knowledge" / "tampered-rebuild.md"
    target.write_bytes(b"old\n")
    holder = ContextLock.acquire(context_root, session_id="tampered-rebuild", tool_version="test")
    stager = StagedReplacement(context_root, sleep_function=lambda _seconds: None)
    intent = stager.prepare(
        "TXN-TAMPERED-REBUILD",
        holder.nonce,
        [StagedWrite("02_knowledge/tampered-rebuild.md", b"new\n")],
        lock=holder,
    )
    stager.apply(intent, lock=holder)
    original_replace = staging_module.os.replace
    attempts = 0

    def tamper_rebuild_then_block(source: Path, destination: Path) -> None:
        nonlocal attempts
        if ".rebuild-" in source.name and attempts == 0:
            attempts += 1
            source.write_bytes(b"tampered\n")
            raise PermissionError("injected rebuild sharing violation")
        original_replace(source, destination)

    monkeypatch.setattr(staging_module.os, "replace", tamper_rebuild_then_block)
    with pytest.raises(InvalidIntentError, match="Staged source changed"):
        stager.rollback(intent, lock=holder)

    assert attempts == 1
    assert target.read_bytes() == b"new\n"
    assert stager.inspect_recovery().state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT

    monkeypatch.setattr(staging_module.os, "replace", original_replace)
    stager.rollback(intent, lock=holder)
    assert target.read_bytes() == b"old\n"
    holder.release()


def test_rollback_removal_rechecks_target_during_permission_retry(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "03_work" / "rollback-unlink-edit.md"
    holder = ContextLock.acquire(context_root, session_id="unlink-edit", tool_version="test")
    stager = StagedReplacement(context_root, sleep_function=lambda _seconds: None)
    intent = stager.prepare(
        "TXN-UNLINK-EDIT",
        holder.nonce,
        [StagedWrite("03_work/rollback-unlink-edit.md", b"created\n")],
        lock=holder,
    )
    stager.apply(intent, lock=holder)
    original_unlink = Path.unlink
    attempts = 0

    def edit_then_block_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal attempts
        if path == target and attempts == 0:
            attempts += 1
            path.write_bytes(b"manual\n")
            raise PermissionError("injected unlink sharing violation")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", edit_then_block_unlink)
    with pytest.raises(RecoveryRequiredError, match="changed during atomic replacement"):
        stager.rollback(intent, lock=holder)

    assert attempts == 1
    assert target.read_bytes() == b"manual\n"
    assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
    holder.release()


def test_rollback_unlink_retry_rejects_old_holder_after_takeover(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "03_work" / "rollback-unlink-takeover.md"
    old = ContextLock.acquire(context_root, session_id="unlink-old", tool_version="test")
    stager = StagedReplacement(context_root, sleep_function=lambda _seconds: None)
    intent = stager.prepare(
        "TXN-UNLINK-TAKEOVER",
        old.nonce,
        [StagedWrite("03_work/rollback-unlink-takeover.md", b"created\n")],
        lock=old,
    )
    stager.apply(intent, lock=old)
    original_unlink = Path.unlink
    successor: ContextLock | None = None
    attempts = 0

    def take_over_then_block_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal attempts, successor
        if path == target and attempts == 0:
            attempts += 1
            monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
            successor = ContextLock.acquire(
                context_root,
                session_id="unlink-successor",
                tool_version="test",
            )
            raise PermissionError("injected unlink sharing violation")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", take_over_then_block_unlink)
    with pytest.raises(LockFenceError):
        stager.rollback(intent, lock=old)

    assert successor is not None
    assert attempts == 1
    assert target.read_bytes() == b"created\n"
    successor.release()


def test_missing_target_parent_is_recovery_conflict_not_invalid_intent(
    context_root: Path,
) -> None:
    parent = context_root / "02_knowledge" / "disappearing"
    parent.mkdir()
    holder = ContextLock.acquire(context_root, session_id="missing-parent", tool_version="test")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-MISSING-PARENT",
        holder.nonce,
        [StagedWrite("02_knowledge/disappearing/target.md", b"new\n")],
        lock=holder,
    )
    parent.rmdir()

    assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
    with pytest.raises(RecoveryRequiredError, match="parent is unavailable"):
        stager.apply(intent, lock=holder)
    holder.release()


def test_prepare_rejects_wrong_nonce_and_non_lock_fence(context_root: Path) -> None:
    holder = ContextLock.acquire(context_root, session_id="wrong-fence", tool_version="test")
    stager = StagedReplacement(context_root)

    with pytest.raises(LockFenceError, match="intent nonce"):
        stager.prepare(
            "TXN-WRONG-NONCE",
            "0" * 32 if holder.nonce != "0" * 32 else "1" * 32,
            [StagedWrite("02_knowledge/wrong-nonce.md", b"new\n")],
            lock=holder,
        )
    with pytest.raises(LockFenceError, match="requires an acquired context lock"):
        stager.prepare(
            "TXN-NOOP-FENCE",
            holder.nonce,
            [StagedWrite("02_knowledge/noop.md", b"new\n")],
            lock=object(),  # type: ignore[arg-type]
        )
    holder.release()


def test_unparseable_intent_is_reported_without_mutation(context_root: Path) -> None:
    staging = context_root / "98_state" / "staging"
    staging.mkdir()
    intent = staging / "intent.json"
    intent.write_bytes(b'{"transaction_id":')
    before = intent.read_bytes()

    inspection = StagedReplacement(context_root).inspect_recovery()

    assert inspection.state is RecoveryState.INVALID_INTENT
    assert intent.read_bytes() == before


def test_dangling_intent_symlink_is_invalid_not_clean(
    context_root: Path,
) -> None:
    staging = context_root / "98_state" / "staging"
    staging.mkdir()
    intent = staging / "intent.json"
    try:
        intent.symlink_to(staging / "missing-intent.json")
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable for this test user: {exc}")

    inspection = StagedReplacement(context_root).inspect_recovery()

    assert inspection.state is RecoveryState.INVALID_INTENT
    assert intent.is_symlink()
    assert not intent.exists()


def test_clean_recovery_inspection_does_not_create_runtime_directories(context_root: Path) -> None:
    staging = context_root / "98_state" / "staging"
    assert not staging.exists()

    inspection = StagedReplacement(context_root).inspect_recovery()

    assert inspection.state is RecoveryState.CLEAN
    assert not staging.exists()


@pytest.mark.parametrize(
    "target",
    ["../outside.md", "02_knowledge/../../outside.md", "C:outside.md", "C:/outside.md"],
)
def test_staging_rejects_target_traversal(context_root: Path, target: str) -> None:
    holder = ContextLock.acquire(context_root, session_id="boundary", tool_version="test")
    stager = StagedReplacement(context_root)

    with pytest.raises(ContextBoundaryError):
        stager.prepare(
            "TXN-ESCAPE",
            holder.nonce,
            [StagedWrite(target, b"escape")],
            lock=holder,
        )

    holder.release()
