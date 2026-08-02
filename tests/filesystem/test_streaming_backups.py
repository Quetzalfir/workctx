"""Lead integration tests (D-035): bounded-memory preimage backups."""

from __future__ import annotations

from pathlib import Path

import pytest

from workctx.adapters.filesystem import staging as staging_module
from workctx.adapters.filesystem.lock import ContextLock
from workctx.adapters.filesystem.staging import StagedMove, StagedReplacement
from workctx.services.contexts import initialize_context


@pytest.fixture
def context_root(tmp_path: Path) -> Path:
    root = tmp_path / "context"
    initialize_context(root, name="Streaming Context", context_id="streaming-context")
    return root


def _lock(context_root: Path, session_id: str) -> ContextLock:
    return ContextLock.acquire(context_root, session_id=session_id, tool_version="test")


def test_move_prepare_streams_the_preimage_backup(
    context_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"x" * (4 * 1024 * 1024) + b"tail\n"
    source = context_root / "00_inbox" / "raw" / "big.bin"
    source.write_bytes(payload)

    def forbid_full_read(path: Path) -> bytes | None:
        raise AssertionError(f"full-file read during prepare: {path}")

    monkeypatch.setattr(staging_module, "_read_optional_regular_file", forbid_full_read)

    with _lock(context_root, "streaming-prepare") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-STREAMING",
            holder.nonce,
            [StagedMove("00_inbox/raw/big.bin", "00_inbox/quarantine/big.bin")],
            lock=holder,
        )
        (move,) = intent.targets
        assert move.backup is not None
        assert Path(context_root, move.backup).read_bytes() == payload
        stager.rollback(intent, lock=holder)

    assert source.read_bytes() == payload
    assert not (context_root / "00_inbox" / "quarantine" / "big.bin").exists()
