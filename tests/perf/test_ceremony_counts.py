from __future__ import annotations

import builtins
import io
import os
import sqlite3
import sys
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import workctx.adapters.sqlite.projection as projection_module
import workctx.transactions.engine as transaction_engine
from workctx.adapters.filesystem.lock import ContextLock
from workctx.adapters.sqlite import SQLiteProjection
from workctx.ingestion import DuplicatePolicy, IngestionService, RegisterRequest
from workctx.services.contexts import initialize_context

pytestmark = pytest.mark.perf

_FIXED_NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
_FILE_OPEN_DEPTH: ContextVar[int] = ContextVar("workctx_perf_file_open_depth", default=0)


@dataclass(slots=True)
class _CeremonyCounts:
    file_opens: int = 0
    path_resolutions: int = 0
    windows_final_path_calls: int = 0
    lock_acquisitions: int = 0
    heartbeat_spans: int = 0
    projection_rebuilds: int = 0
    sqlite_connections: int = 0
    schema_initializations: int = 0
    heartbeat_writes: int = 0
    fsyncs: int = 0


def _count_calls(
    counts: _CeremonyCounts,
    field: str,
    operation: Callable[..., Any],
) -> Callable[..., Any]:
    def counted(*args: object, **kwargs: object) -> Any:
        setattr(counts, field, getattr(counts, field) + 1)
        return operation(*args, **kwargs)

    return counted


def _count_file_opens(
    counts: _CeremonyCounts,
    operation: Callable[..., Any],
) -> Callable[..., Any]:
    def counted(*args: object, **kwargs: object) -> Any:
        depth = _FILE_OPEN_DEPTH.get()
        token = _FILE_OPEN_DEPTH.set(depth + 1)
        try:
            if depth == 0:
                counts.file_opens += 1
            return operation(*args, **kwargs)
        finally:
            _FILE_OPEN_DEPTH.reset(token)

    return counted


def _install_ceremony_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> _CeremonyCounts:
    counts = _CeremonyCounts()
    original_acquire = ContextLock.acquire

    def counted_acquire(cls: type[ContextLock], /, *args: Any, **kwargs: Any) -> ContextLock:
        del cls
        counts.lock_acquisitions += 1
        return original_acquire(*args, **kwargs)

    monkeypatch.setattr(
        Path,
        "open",
        _count_file_opens(counts, Path.open),
    )
    monkeypatch.setattr(
        builtins,
        "open",
        _count_file_opens(counts, builtins.open),
    )
    monkeypatch.setattr(
        io,
        "open",
        _count_file_opens(counts, io.open),
    )
    monkeypatch.setattr(
        os,
        "fsync",
        _count_calls(counts, "fsyncs", os.fsync),
    )
    monkeypatch.setattr(
        projection_module.sqlite3,
        "connect",
        _count_calls(counts, "sqlite_connections", sqlite3.connect),
    )
    monkeypatch.setattr(
        projection_module,
        "create_schema",
        _count_calls(counts, "schema_initializations", projection_module.create_schema),
    )
    monkeypatch.setattr(
        ContextLock,
        "heartbeat",
        _count_calls(counts, "heartbeat_writes", ContextLock.heartbeat),
    )
    monkeypatch.setattr(ContextLock, "acquire", classmethod(counted_acquire))
    monkeypatch.setattr(
        transaction_engine._HeartbeatLease,
        "start",
        _count_calls(
            counts,
            "heartbeat_spans",
            transaction_engine._HeartbeatLease.start,
        ),
    )
    monkeypatch.setattr(
        SQLiteProjection,
        "rebuild",
        _count_calls(counts, "projection_rebuilds", SQLiteProjection.rebuild),
    )
    monkeypatch.setattr(
        Path,
        "resolve",
        _count_calls(counts, "path_resolutions", Path.resolve),
    )

    if sys.platform == "win32":
        import ntpath

        # One Path.resolve call may invoke the Windows primitive more than once;
        # keep that lower-level number as a diagnostic instead of double-counting
        # the contract's Path.resolve metric.
        monkeypatch.setattr(
            ntpath,
            "_getfinalpathname",
            _count_calls(
                counts,
                "windows_final_path_calls",
                ntpath._getfinalpathname,
            ),
        )

    return counts


def test_single_file_registration_has_bounded_ceremony(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Ceremony Lab", context_id="ceremony-test")
    SQLiteProjection(root).rebuild()
    raw = root / "00_inbox" / "raw" / "planning-note.txt"
    raw.write_bytes(b"Fictional planning note.\n")
    service = IngestionService(root, clock=lambda: _FIXED_NOW)
    request = RegisterRequest(
        path="00_inbox/raw/planning-note.txt",
        source_type="note",
        source_origin="fictional://local-drop",
        event_at="2026-08-02T08:00:00-04:00",
        language="en",
        classification="internal",
        duplicate_policy=DuplicatePolicy.REFUSE,
    )

    counts = _install_ceremony_counters(monkeypatch)
    result = service.register(request, session_id="wp610-ceremony")

    assert result.artifact.manifest.id == "ART-20260803-planning-note-01"
    assert counts.lock_acquisitions == 1, counts
    assert counts.heartbeat_spans == 1, counts
    assert counts.projection_rebuilds == 2, counts
    assert counts.file_opens < 150, counts
    assert counts.path_resolutions < 330, counts
    assert 1 <= counts.sqlite_connections <= 2, counts
    assert 1 <= counts.schema_initializations <= 2, counts
    assert counts.heartbeat_writes <= 12, counts
    assert counts.fsyncs <= 266, counts


def test_three_file_batch_registration_amortizes_fixed_ceremony(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Batch Ceremony Lab", context_id="batch-ceremony")
    SQLiteProjection(root).rebuild()
    requests: list[RegisterRequest] = []
    for index in range(3):
        relative_path = f"00_inbox/raw/planning-note-{index}.txt"
        root.joinpath(*relative_path.split("/")).write_bytes(
            f"Fictional planning note {index}.\n".encode()
        )
        requests.append(
            RegisterRequest(
                path=relative_path,
                source_type="note",
                source_origin="fictional://local-drop",
                event_at="2026-08-02T08:00:00-04:00",
                language="en",
                classification="internal",
                duplicate_policy=DuplicatePolicy.REFUSE,
            )
        )

    service = IngestionService(root, clock=lambda: _FIXED_NOW)
    counts = _install_ceremony_counters(monkeypatch)

    batch = service.register_batch(requests, session_id="wp660-batch-ceremony")

    assert batch.failure is None
    assert batch.registration_count == 3
    assert counts.lock_acquisitions == 1, counts
    assert counts.heartbeat_spans == 1, counts
    assert counts.projection_rebuilds == 1, counts
    assert counts.file_opens < 370, counts
    assert counts.path_resolutions < 970, counts
    assert 1 <= counts.sqlite_connections <= 2, counts
    assert 1 <= counts.schema_initializations <= 2, counts
    assert counts.heartbeat_writes <= 12, counts
    assert counts.fsyncs <= 266, counts
