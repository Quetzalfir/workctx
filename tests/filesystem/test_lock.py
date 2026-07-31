from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from workctx.adapters.filesystem import lock as lock_module
from workctx.adapters.filesystem.lock import (
    ContextLock,
    LockError,
    LockFenceError,
    LockHeldError,
    inspect_context_lock,
    verify_lock_fence,
)
from workctx.errors import ContextBoundaryError
from workctx.services.contexts import initialize_context


@pytest.fixture
def context_root(tmp_path: Path) -> Path:
    root = tmp_path / "context"
    initialize_context(root, name="Lock Context", context_id="lock-context")
    return root


def test_exclusive_acquisition_records_complete_owner_and_blocks_contender(
    context_root: Path,
) -> None:
    holder = ContextLock.acquire(context_root, session_id="session-one", tool_version="test")
    lock_path = context_root / "98_state" / "lock.json"
    before = lock_path.read_bytes()

    with pytest.raises(LockHeldError):
        ContextLock.acquire(context_root, session_id="session-two", tool_version="test")

    payload = json.loads(before)
    assert list(payload) == [
        "pid",
        "hostname",
        "session_id",
        "tool_version",
        "acquired_at",
        "heartbeat_at",
        "nonce",
    ]
    assert len(payload["nonce"]) == 32
    assert lock_path.read_bytes() == before
    holder.release()


def test_simultaneous_contenders_have_exactly_one_winner(context_root: Path) -> None:
    barrier = Barrier(2)

    def acquire(session_id: str) -> ContextLock | None:
        barrier.wait()
        try:
            return ContextLock.acquire(context_root, session_id=session_id, tool_version="test")
        except LockHeldError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        leases = list(executor.map(acquire, ("one", "two")))

    winners = [lease for lease in leases if lease is not None]
    assert len(winners) == 1
    winners[0].release()


def test_heartbeat_changes_only_timestamp_and_uses_atomic_replace(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    later = start + timedelta(minutes=1)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start)
    holder = ContextLock.acquire(context_root, session_id="heartbeat", tool_version="test")
    before = holder.metadata
    monkeypatch.setattr(lock_module, "_utc_now", lambda: later)

    refreshed = holder.heartbeat()

    assert refreshed.heartbeat_at == later
    assert refreshed.nonce == before.nonce
    assert refreshed.pid == before.pid
    assert refreshed.hostname == before.hostname
    assert refreshed.session_id == before.session_id
    assert refreshed.tool_version == before.tool_version
    assert refreshed.acquired_at == before.acquired_at
    assert not (context_root / "98_state" / "lock.json.tmp").exists()
    holder.release()


def test_failed_heartbeat_replace_leaves_original_lock_parseable(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = ContextLock.acquire(context_root, session_id="torn", tool_version="test")
    lock_path = context_root / "98_state" / "lock.json"
    before = lock_path.read_bytes()
    attempts = 0
    delays: list[float] = []

    def fail_replace(source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("injected sharing violation")

    monkeypatch.setattr(lock_module, "_replace", fail_replace)
    monkeypatch.setattr(lock_module, "_sleep", delays.append)

    with pytest.raises(LockError, match="10 attempts"):
        holder.heartbeat()

    assert attempts == 10
    assert sum(delays) == pytest.approx(5.11)
    assert lock_path.read_bytes() == before
    assert json.loads(lock_path.read_text(encoding="utf-8"))["nonce"] == holder.nonce
    monkeypatch.undo()
    holder.release()


def test_expired_heartbeat_is_archived_before_takeover(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start)
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    old_bytes = old.lock_path.read_bytes()
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start + timedelta(minutes=11))

    new = ContextLock.acquire(context_root, session_id="new", tool_version="test")

    archives = list((context_root / "98_state").glob("lock.stale-*.json"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == old_bytes
    assert new.nonce != old.nonce
    with pytest.raises(LockFenceError):
        old.verify_fence()
    new.release()


def test_simultaneous_stale_takeovers_archive_only_the_original_lock(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start)
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    old_bytes = old.lock_path.read_bytes()
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start + timedelta(minutes=11))
    barrier = Barrier(2)

    def take_over(session_id: str) -> ContextLock | None:
        barrier.wait()
        try:
            return ContextLock.acquire(context_root, session_id=session_id, tool_version="test")
        except LockHeldError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        leases = list(executor.map(take_over, ("one", "two")))

    winners = [lease for lease in leases if lease is not None]
    assert len(winners) == 1
    archives = list((context_root / "98_state").glob("lock.stale-*.json"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == old_bytes
    assert verify_lock_fence(context_root, winners[0].nonce).nonce == winners[0].nonce
    winners[0].release()


def test_missing_same_host_pid_is_stale_even_with_fresh_heartbeat(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)

    new = ContextLock.acquire(context_root, session_id="new", tool_version="test")

    assert new.nonce != old.nonce
    assert list((context_root / "98_state").glob("lock.stale-*.json"))
    new.release()


def test_remote_fresh_lock_never_probes_local_pid(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = ContextLock.acquire(context_root, session_id="remote", tool_version="test")
    payload = json.loads(holder.lock_path.read_text(encoding="utf-8"))
    payload["hostname"] = "another-host"
    holder.lock_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        lock_module,
        "_pid_is_alive",
        lambda _pid: pytest.fail("remote PID must not be inspected"),
    )

    with pytest.raises(LockHeldError):
        ContextLock.acquire(context_root, session_id="contender", tool_version="test")


def test_unparseable_lock_uses_strict_mtime_threshold(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: now)
    lock_path = context_root / "98_state" / "lock.json"
    corrupted = b'{"pid":'
    lock_path.write_bytes(corrupted)
    exactly_threshold = (now - timedelta(minutes=10)).timestamp()
    os.utime(lock_path, (exactly_threshold, exactly_threshold))

    with pytest.raises(LockHeldError):
        ContextLock.acquire(context_root, session_id="young", tool_version="test")

    old = (now - timedelta(minutes=10, seconds=1)).timestamp()
    os.utime(lock_path, (old, old))
    acquired = ContextLock.acquire(context_root, session_id="takeover", tool_version="test")

    archive = next((context_root / "98_state").glob("lock.stale-*.json"))
    assert archive.read_bytes() == corrupted
    acquired.release()


def test_old_holder_cannot_heartbeat_or_release_after_takeover(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
    new = ContextLock.acquire(context_root, session_id="new", tool_version="test")
    new_bytes = new.lock_path.read_bytes()

    with pytest.raises(LockFenceError):
        old.heartbeat()
    with pytest.raises(LockFenceError):
        old.release()

    assert new.lock_path.read_bytes() == new_bytes
    new.release()


def test_mutation_guard_blocks_takeover_during_heartbeat_temp_write(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    stale = start + timedelta(minutes=11)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start)
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    original_write = lock_module._write_fsynced
    takeover_attempted = False

    def write_then_take_over(path: Path, payload: bytes) -> None:
        nonlocal takeover_attempted
        original_write(path, payload)
        takeover_attempted = True
        monkeypatch.setattr(lock_module, "_utc_now", lambda: stale)
        with pytest.raises(LockHeldError, match="mutation guard"):
            ContextLock.acquire(context_root, session_id="new", tool_version="test")
        monkeypatch.setattr(lock_module, "_utc_now", lambda: start)

    monkeypatch.setattr(lock_module, "_write_fsynced", write_then_take_over)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)

    old.heartbeat()

    assert takeover_attempted
    assert verify_lock_fence(context_root, old.nonce).nonce == old.nonce
    old.release()


def test_takeover_before_old_heartbeat_guard_cannot_contaminate_successor_temp(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    original_acquire_guard = lock_module._acquire_mutation_guard
    intercept_old = True
    successor: ContextLock | None = None

    def take_over_before_old_guard(root: Path):
        nonlocal intercept_old, successor
        if intercept_old:
            intercept_old = False
            monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
            successor = ContextLock.acquire(root, session_id="successor", tool_version="test")
            successor.heartbeat()
        return original_acquire_guard(root)

    monkeypatch.setattr(lock_module, "_acquire_mutation_guard", take_over_before_old_guard)

    with pytest.raises(LockFenceError):
        old.heartbeat()

    assert successor is not None
    assert verify_lock_fence(context_root, successor.nonce).nonce == successor.nonce
    assert json.loads(successor.lock_path.read_bytes())["nonce"] == successor.nonce
    successor.release()


def test_mutation_guard_blocks_takeover_after_final_heartbeat_fence(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    later = start + timedelta(minutes=11)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start)
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    holder = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    original_replace = lock_module._replace
    takeover_attempted = False

    def attempt_takeover_after_fence(source: Path, target: Path) -> None:
        nonlocal takeover_attempted
        takeover_attempted = True
        with pytest.raises(LockHeldError, match="mutation guard"):
            ContextLock.acquire(context_root, session_id="new", tool_version="test")
        original_replace(source, target)

    monkeypatch.setattr(lock_module, "_utc_now", lambda: later)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(lock_module, "_replace", attempt_takeover_after_fence)

    holder.heartbeat()

    assert takeover_attempted
    assert verify_lock_fence(context_root, holder.nonce).nonce == holder.nonce
    assert not list((context_root / "98_state").glob("lock.stale-*.json"))
    holder.release()


def test_fence_rejects_missing_malformed_and_mismatched_lock(context_root: Path) -> None:
    holder = ContextLock.acquire(context_root, session_id="fence", tool_version="test")
    assert verify_lock_fence(context_root, holder.nonce).nonce == holder.nonce

    with pytest.raises(LockFenceError):
        verify_lock_fence(context_root, "0" * 32)
    holder.lock_path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(LockFenceError):
        verify_lock_fence(context_root, holder.nonce)
    holder.lock_path.unlink()
    with pytest.raises(LockFenceError):
        verify_lock_fence(context_root, holder.nonce)


def test_context_manager_releases_and_double_release_is_idempotent(context_root: Path) -> None:
    with ContextLock.acquire(context_root, session_id="managed", tool_version="test") as holder:
        assert inspect_context_lock(context_root).exists

    assert not inspect_context_lock(context_root).exists
    holder.release()


def test_mutation_guard_blocks_takeover_between_release_fence_and_unlink(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    later = start + timedelta(minutes=11)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start)
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    holder = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    lock_path = holder.lock_path
    original_unlink = Path.unlink
    takeover_attempted = False

    def attempt_takeover_before_unlink(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        nonlocal takeover_attempted
        if path == lock_path and not takeover_attempted:
            takeover_attempted = True
            with pytest.raises(LockHeldError, match="mutation guard"):
                ContextLock.acquire(context_root, session_id="new", tool_version="test")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(lock_module, "_utc_now", lambda: later)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(Path, "unlink", attempt_takeover_before_unlink)

    holder.release()

    assert takeover_attempted
    successor = ContextLock.acquire(context_root, session_id="successor", tool_version="test")
    assert verify_lock_fence(context_root, successor.nonce).nonce == successor.nonce
    successor.release()


def test_valid_guard_is_never_reclaimed_by_age_but_dead_owner_is_recovered(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = lock_module._acquire_mutation_guard(context_root)
    original = guard.path.read_bytes()
    os.utime(guard.path, (1, 1))
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)

    with pytest.raises(LockHeldError, match="mutation guard"):
        ContextLock.acquire(context_root, session_id="blocked", tool_version="test")

    assert guard.path.read_bytes() == original
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
    holder = ContextLock.acquire(context_root, session_id="recovered", tool_version="test")
    assert not guard.path.exists()
    assert not list((context_root / "98_state" / "staging").glob("lock.guard.*.json"))
    holder.release()


def test_guard_entry_publication_sharing_violation_is_retried(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = lock_module._acquire_mutation_guard(context_root)
    original_read = lock_module._read_snapshot
    busy_reads = 0

    def transient_busy_read(path: Path) -> lock_module._LockSnapshot | None:
        nonlocal busy_reads
        if path == guard.path and busy_reads < 3:
            busy_reads += 1
            raise PermissionError("injected publication sharing violation")
        return original_read(path)

    monkeypatch.setattr(lock_module, "_read_snapshot", transient_busy_read)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)

    with pytest.raises(LockHeldError, match="mutation guard"):
        ContextLock.acquire(context_root, session_id="contender", tool_version="test")

    assert busy_reads == 3
    assert guard.path.is_file()
    lock_module._release_mutation_guard(guard)


def test_malformed_guard_uses_conservative_mtime_recovery(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    guard_path = (
        context_root
        / "98_state"
        / "staging"
        / "lock.guard.ticket-00000000000000000000000000000000.json"
    )
    guard_path.parent.mkdir()
    guard_path.write_bytes(b'{"pid":')
    monkeypatch.setattr(lock_module, "_utc_now", lambda: now)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)
    recent = now.timestamp()
    os.utime(guard_path, (recent, recent))

    with pytest.raises(LockHeldError, match="mutation guard"):
        ContextLock.acquire(context_root, session_id="blocked", tool_version="test")

    old = (now - timedelta(hours=1, seconds=1)).timestamp()
    os.utime(guard_path, (old, old))
    holder = ContextLock.acquire(context_root, session_id="recovered", tool_version="test")
    assert not guard_path.exists()
    holder.release()


def test_ticket_is_cleaned_if_choosing_release_raises(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_release = lock_module._release_mutation_guard
    injected = False

    def fail_first_choosing_release(guard: lock_module._MutationGuard) -> None:
        nonlocal injected
        if ".choosing-" in guard.path.name and not injected:
            injected = True
            raise lock_module.LockError("injected choosing cleanup failure")
        original_release(guard)

    monkeypatch.setattr(lock_module, "_release_mutation_guard", fail_first_choosing_release)
    with pytest.raises(lock_module.LockError, match="choosing cleanup"):
        ContextLock.acquire(context_root, session_id="cleanup-failure", tool_version="test")

    assert injected
    assert not list((context_root / "98_state" / "staging").glob("lock.guard.*.json"))
    holder = ContextLock.acquire(context_root, session_id="after-cleanup", tool_version="test")
    holder.release()


def test_choosing_file_is_cleaned_if_publication_fsync_is_interrupted(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = context_root / "98_state" / "staging"
    original_fsync = lock_module._fsync_directory
    injected = False

    def interrupt_first_choosing_fsync(path: Path) -> None:
        nonlocal injected
        if path == staging and not injected and list(staging.glob("lock.guard.choosing-*.json")):
            injected = True
            raise KeyboardInterrupt("injected choosing publication interruption")
        original_fsync(path)

    monkeypatch.setattr(lock_module, "_fsync_directory", interrupt_first_choosing_fsync)
    with pytest.raises(KeyboardInterrupt, match="choosing publication"):
        ContextLock.acquire(context_root, session_id="publication-failure", tool_version="test")

    assert injected
    assert not list(staging.glob("lock.guard.*.json"))
    holder = ContextLock.acquire(context_root, session_id="after-publication", tool_version="test")
    holder.release()


def test_unremovable_unique_ticket_is_cancelled_without_stranding_process(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = lock_module._unlink_with_retry
    blocked_ticket: Path | None = None

    def block_first_ticket(path: Path) -> None:
        nonlocal blocked_ticket
        if ".ticket-" in path.name and blocked_ticket is None:
            blocked_ticket = path
        if path == blocked_ticket:
            raise lock_module.LockError("injected persistent sharing violation")
        original_unlink(path)

    monkeypatch.setattr(lock_module, "_unlink_with_retry", block_first_ticket)
    holder = ContextLock.acquire(context_root, session_id="cancel-ticket", tool_version="test")

    assert blocked_ticket is not None
    nonce = blocked_ticket.stem.removeprefix("lock.guard.ticket-")
    cancellation = blocked_ticket.with_name(f"lock.guard.cancelled-ticket-{nonce}.json")
    assert blocked_ticket.is_file()
    assert cancellation.is_file()

    holder.heartbeat()
    holder.release()
    assert not inspect_context_lock(context_root).exists


def test_unremovable_choosing_and_ticket_are_cancelled_independently(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = Path.unlink
    blocked: list[Path] = []

    def guard_nonce(path: Path) -> str:
        for prefix in ("lock.guard.choosing-", "lock.guard.ticket-"):
            if path.stem.startswith(prefix):
                return path.stem.removeprefix(prefix)
        return ""

    def block_first_guard_pair(path: Path, missing_ok: bool = False) -> None:
        nonce = guard_nonce(path)
        if nonce and (not blocked or nonce == guard_nonce(blocked[0])):
            if path not in blocked:
                blocked.append(path)
            raise PermissionError("injected persistent sharing violation")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(Path, "unlink", block_first_guard_pair)
    first = ContextLock.acquire(context_root, session_id="cancel-pair", tool_version="test")

    assert len(blocked) == 2
    for path in blocked:
        kind = "choosing" if ".choosing-" in path.name else "ticket"
        nonce = guard_nonce(path)
        assert path.is_file()
        assert path.with_name(f"lock.guard.cancelled-{kind}-{nonce}.json").is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    first.release()
    staging = context_root / "98_state" / "staging"
    assert not list(staging.glob("lock.guard.*.json"))
    second = ContextLock.acquire(context_root, session_id="after-pair", tool_version="test")
    second.release()


def test_guard_cancellation_publication_sharing_violation_is_retried(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = lock_module._acquire_mutation_guard(context_root)
    cancellation = lock_module._guard_cancellation_path(guard.path, guard.metadata)
    lock_module._write_exclusive(cancellation, lock_module._encode_guard_metadata(guard.metadata))
    original_read = lock_module._read_snapshot
    busy_reads = 0

    def transient_busy_read(path: Path) -> lock_module._LockSnapshot | None:
        nonlocal busy_reads
        if path == cancellation and busy_reads < 3:
            busy_reads += 1
            raise PermissionError("injected cancellation sharing violation")
        return original_read(path)

    monkeypatch.setattr(lock_module, "_read_snapshot", transient_busy_read)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)

    holder = ContextLock.acquire(context_root, session_id="after-cancel", tool_version="test")

    assert busy_reads == 3
    holder.release()
    assert not guard.path.exists()
    assert not cancellation.exists()


def test_stale_partial_guard_cancellation_from_crash_is_recovered(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = lock_module._acquire_mutation_guard(context_root)
    cancellation = lock_module._guard_cancellation_path(guard.path, guard.metadata)
    cancellation.write_bytes(b'{"pid":')
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(cancellation, (old, old))
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)

    holder = ContextLock.acquire(context_root, session_id="after-crash", tool_version="test")

    assert not cancellation.exists()
    assert not guard.path.exists()
    holder.release()


def test_mutation_guard_release_read_sharing_violation_is_retried(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = lock_module._acquire_mutation_guard(context_root)
    original_read = lock_module._read_snapshot
    busy_reads = 0

    def transient_busy_read(path: Path) -> lock_module._LockSnapshot | None:
        nonlocal busy_reads
        if path == guard.path and busy_reads < 3:
            busy_reads += 1
            raise PermissionError("injected release sharing violation")
        return original_read(path)

    monkeypatch.setattr(lock_module, "_read_snapshot", transient_busy_read)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)

    lock_module._release_mutation_guard(guard)

    assert busy_reads == 3
    assert not guard.path.exists()


def test_unreadable_mutation_guard_release_is_cancelled(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = lock_module._acquire_mutation_guard(context_root)
    original_read = lock_module._read_snapshot
    busy_reads = 0

    def busy_until_retry_exhaustion(path: Path) -> lock_module._LockSnapshot | None:
        nonlocal busy_reads
        if path == guard.path and busy_reads < lock_module._GUARD_ATTEMPTS:
            busy_reads += 1
            raise PermissionError("injected persistent release sharing violation")
        return original_read(path)

    monkeypatch.setattr(lock_module, "_read_snapshot", busy_until_retry_exhaustion)
    monkeypatch.setattr(lock_module, "_sleep", lambda _seconds: None)

    lock_module._release_mutation_guard(guard)

    cancellation = lock_module._guard_cancellation_path(guard.path, guard.metadata)
    assert busy_reads == lock_module._GUARD_ATTEMPTS
    assert guard.path.is_file()
    assert cancellation.is_file()

    holder = ContextLock.acquire(context_root, session_id="after-unreadable", tool_version="test")
    assert not guard.path.exists()
    assert not cancellation.exists()
    holder.release()


def test_stale_archive_collision_never_overwrites_existing_evidence(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2030, 1, 1, tzinfo=UTC)
    later = start + timedelta(minutes=11)
    monkeypatch.setattr(lock_module, "_utc_now", lambda: start)
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = ContextLock.acquire(context_root, session_id="old", tool_version="test")
    old_bytes = old.lock_path.read_bytes()
    monkeypatch.setattr(lock_module, "_utc_now", lambda: later)
    timestamp = later.strftime("%Y%m%dT%H%M%S.%fZ")
    collision = context_root / "98_state" / f"lock.stale-{timestamp}.json"
    collision.write_bytes(b"existing evidence\n")

    successor = ContextLock.acquire(context_root, session_id="new", tool_version="test")

    assert collision.read_bytes() == b"existing evidence\n"
    archived = context_root / "98_state" / f"lock.stale-{timestamp}-1.json"
    assert archived.read_bytes() == old_bytes
    successor.release()


def test_lock_path_symlink_escape_is_rejected_without_external_write(
    context_root: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-lock.json"
    outside.write_text("preserve\n", encoding="utf-8")
    lock_path = context_root / "98_state" / "lock.json"
    try:
        lock_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable for this test user: {exc}")

    with pytest.raises(ContextBoundaryError):
        ContextLock.acquire(context_root, session_id="escape", tool_version="test")

    assert outside.read_text(encoding="utf-8") == "preserve\n"
