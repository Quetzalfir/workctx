from __future__ import annotations

import hashlib
import json
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import workctx.usage as usage
from workctx.adapters.sqlite import SQLiteProjection
from workctx.usage import evaluate_usage, record, summarize, usage_status

from .support import REPO_URI, initialize_usage_context, write_task


def test_disabled_default_skips_recorder_and_creates_no_usage_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = initialize_usage_context(tmp_path / "disabled")
    write_task(
        root,
        "TASK-2026-001",
        updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    projection = SQLiteProjection(root)
    projection.rebuild()
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(usage, "record", lambda *args, **kwargs: calls.append(args))

    assert projection.usage_enabled is False
    assert projection.search("fictional")
    assert calls == []
    assert not (root / "98_state" / "usage").exists()

    record(root, "search", "must remain private")
    assert not (root / "98_state" / "usage").exists()
    assert evaluate_usage(root, now=datetime(2026, 8, 4, tzinfo=UTC)) == ()


def test_opt_in_records_uri_and_hashes_search_without_secret_or_body(tmp_path: Path) -> None:
    root = initialize_usage_context(tmp_path / "record", enabled=True)
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    query = "fictional-secret-token body text"

    record(root, "search", query, now=now)
    record(root, "resolve", REPO_URI, now=now + timedelta(seconds=1))
    path = root / "98_state" / "usage" / "usage.jsonl"
    payload = path.read_text(encoding="utf-8")
    lines: list[dict[str, Any]] = [json.loads(line) for line in payload.splitlines()]

    assert query not in payload
    assert "body text" not in payload
    assert lines == [
        {
            "timestamp": "2026-08-04T12:00:00Z",
            "api": "search",
            "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        },
        {
            "timestamp": "2026-08-04T12:00:01Z",
            "api": "resolve",
            "target_uri": REPO_URI,
        },
    ]


def test_search_like_uri_and_uri_with_query_parameters_are_hashed(tmp_path: Path) -> None:
    root = initialize_usage_context(tmp_path / "uri-privacy", enabled=True)
    now = datetime(2026, 8, 4, tzinfo=UTC)
    search_uri = "https://fictional.example/search"
    secret_uri = "https://fictional.example/item?token=fictional-secret"

    record(root, "search", search_uri, now=now)
    record(root, "mcp.ref_show", secret_uri, now=now)

    content = (root / "98_state" / "usage" / "usage.jsonl").read_text(encoding="utf-8")
    assert search_uri not in content
    assert secret_uri not in content
    assert "fictional-secret" not in content
    assert content.count("query_sha256") == 2


def test_rotation_retains_two_numbered_files_and_summary_reads_all(tmp_path: Path) -> None:
    root = initialize_usage_context(tmp_path / "rotation", enabled=True)
    now = datetime(2026, 8, 4, tzinfo=UTC)

    for index in range(5):
        record(
            root,
            "resolve",
            REPO_URI,
            now=now + timedelta(seconds=index),
            max_bytes=220,
            keep=2,
        )

    directory = root / "98_state" / "usage"
    assert (directory / "usage.jsonl").is_file()
    assert (directory / "usage.jsonl.1").is_file()
    assert (directory / "usage.jsonl.2").is_file()
    assert not (directory / "usage.jsonl.3").exists()
    summary = summarize(root, now=now + timedelta(minutes=1))
    assert summary.event_count == 3
    assert summary.targets[0].uses_7d == 3
    status = usage_status(root, now=now + timedelta(minutes=1))
    assert status.rotated_file_count == 2
    assert status.rotated_size_bytes > 0


def test_corrupt_line_is_skipped_and_does_not_block_later_recording(tmp_path: Path) -> None:
    root = initialize_usage_context(tmp_path / "corrupt", enabled=True)
    path = root / "98_state" / "usage" / "usage.jsonl"
    path.parent.mkdir()
    path.write_text("{not-json}\n", encoding="utf-8")

    record(root, "resolve", REPO_URI, now=datetime(2026, 8, 4, tzinfo=UTC))
    summary = summarize(root, now=datetime(2026, 8, 4, 1, tzinfo=UTC))

    assert summary.corrupt_line_count == 1
    assert summary.event_count == 1
    assert summary.targets[0].target_uri == REPO_URI


def test_unwritable_usage_file_warns_once_and_never_breaks_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = initialize_usage_context(tmp_path / "unwritable", enabled=True)
    write_task(
        root,
        "TASK-2026-002",
        updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    projection = SQLiteProjection(root)
    projection.rebuild()
    original_open = Path.open

    def deny_usage_append(path: Path, *args: object, **kwargs: object):
        mode = str(args[0]) if args else str(kwargs.get("mode", "r"))
        if path.name == "usage.jsonl" and "a" in mode:
            raise PermissionError("simulated unwritable usage file")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_usage_append)
    with pytest.warns(RuntimeWarning, match="read operation continued"):
        first = projection.search("fictional")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        second = projection.search("fictional")

    assert first == second
    assert first
    assert captured == []


def test_unwritable_usage_directory_never_breaks_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = initialize_usage_context(tmp_path / "unwritable-directory", enabled=True)
    write_task(
        root,
        "TASK-2026-003",
        updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    projection = SQLiteProjection(root)
    projection.rebuild()
    original_mkdir = Path.mkdir

    def deny_usage_directory(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "usage":
            raise PermissionError("simulated unwritable usage directory")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_usage_directory)
    with pytest.warns(RuntimeWarning, match="read operation continued"):
        hits = projection.search("fictional")

    assert hits
    assert not (root / "98_state" / "usage").exists()


def test_window_boundaries_are_inclusive_and_future_events_are_ignored(tmp_path: Path) -> None:
    root = initialize_usage_context(tmp_path / "windows", enabled=True)
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    offsets = (
        timedelta(0),
        timedelta(days=7),
        timedelta(days=7, seconds=1),
        timedelta(days=30),
        timedelta(days=30, seconds=1),
        timedelta(days=90),
        timedelta(days=90, seconds=1),
    )
    for offset in offsets:
        record(root, "resolve", REPO_URI, now=now - offset)
    record(root, "resolve", REPO_URI, now=now + timedelta(seconds=1))

    summary = summarize(root, now=now)
    target = summary.targets[0]
    assert (target.uses_7d, target.uses_30d, target.uses_90d) == (2, 4, 6)
    assert summary.event_count == 7
