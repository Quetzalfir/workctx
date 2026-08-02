from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workctx.adapters.agents._lock import (
    AdapterLock,
    LockMetadata,
    inspect_adapter_lock,
)
from workctx.adapters.agents._safe_fs import SafeRoot
from workctx.adapters.agents.layout import derive_layout
from workctx.adapters.agents.models import AgentClient


def _lock_root(tmp_path: Path) -> tuple[Path, SafeRoot]:
    project = tmp_path / "project"
    project.mkdir()
    safe = SafeRoot(project)
    safe.ensure_directories(".workctx/agent-adapters")
    return project, safe


def test_same_host_dead_pid_makes_fresh_lock_stale(tmp_path: Path) -> None:
    project, safe = _lock_root(tmp_path)
    layout = derive_layout(project, AgentClient.CLAUDE)
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    metadata = LockMetadata(
        pid=2_147_483_647,
        hostname=socket.gethostname(),
        session_id="dead-owner",
        tool_version="test",
        acquired_at=now,
        heartbeat_at=now,
        nonce="a" * 32,
    )
    safe.write_exclusive(layout.lock_path, metadata.to_bytes())

    inspection = inspect_adapter_lock(layout, now=now)

    assert inspection.stale
    assert not inspection.live
    assert not inspection.invalid


def test_fresh_lock_from_another_host_is_not_probed_as_a_local_pid(tmp_path: Path) -> None:
    project, safe = _lock_root(tmp_path)
    layout = derive_layout(project, AgentClient.CLAUDE)
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    metadata = LockMetadata(
        pid=2_147_483_647,
        hostname="another-host.invalid",
        session_id="remote-owner",
        tool_version="test",
        acquired_at=now,
        heartbeat_at=now,
        nonce="b" * 32,
    )
    safe.write_exclusive(layout.lock_path, metadata.to_bytes())

    inspection = inspect_adapter_lock(layout, now=now)

    assert inspection.live
    assert not inspection.stale
    assert not inspection.invalid


def test_old_malformed_lock_is_archived_during_takeover(tmp_path: Path) -> None:
    project, safe = _lock_root(tmp_path)
    layout = derive_layout(project, AgentClient.CLAUDE)
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    safe.write_exclusive(layout.lock_path, b"malformed lock bytes\n")
    old = now - timedelta(minutes=11)
    lock_path = project / Path(layout.lock_path)
    os.utime(lock_path, (old.timestamp(), old.timestamp()))

    inspection = inspect_adapter_lock(layout, now=now)
    assert inspection.stale
    assert not inspection.invalid

    lock = AdapterLock.acquire(layout, session_id="new-owner", now=now)
    try:
        archives = tuple(lock_path.parent.glob("lock.stale-*.json"))
        assert len(archives) == 1
        assert archives[0].read_bytes() == b"malformed lock bytes\n"
    finally:
        lock.release()


def test_recent_malformed_lock_remains_invalid(tmp_path: Path) -> None:
    project, safe = _lock_root(tmp_path)
    layout = derive_layout(project, AgentClient.CLAUDE)
    now = datetime.now(UTC)
    safe.write_exclusive(layout.lock_path, b"malformed lock bytes\n")

    inspection = inspect_adapter_lock(layout, now=now)

    assert inspection.invalid
    assert not inspection.stale
