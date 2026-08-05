from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from workctx.connectors import (
    LAST_SYNC_STATE_PATH,
    ConnectorSyncFailureKind,
    SnapshotSchedule,
    status,
    sync,
    sync_all,
)
from workctx.connectors import state as connector_state

from .support import (
    FIXED_NOW,
    initialize_connector_context,
    manifest_payload,
    write_manifest,
)


def _state_path(root: Path) -> Path:
    return root.joinpath(*LAST_SYNC_STATE_PATH.split("/"))


def _state_payload(root: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_state_path(root).read_text(encoding="utf-8"))
    return payload


def _success_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": "application/json"},
        content=f'{{"path":"{request.url.path}"}}'.encode(),
        request=request,
    )


def test_manual_sync_runs_when_not_due_and_atomically_records_last_success(
    connector_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = initialize_connector_context(connector_tmp_path / "context")
    write_manifest(
        root,
        manifest_payload(
            snapshots=[
                {
                    "id": "current-items",
                    "path": "/items",
                    "schedule": "daily",
                }
            ]
        ),
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _success_response(request)

    replacements: list[tuple[Path, Path]] = []
    real_replace = connector_state._replace

    def tracking_replace(source: Path, destination: Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(connector_state, "_replace", tracking_replace)

    sync(
        root,
        "fictional-service",
        transport=httpx.MockTransport(handler),
        clock=lambda: FIXED_NOW,
    )
    second_time = FIXED_NOW + timedelta(minutes=5)
    sync(
        root,
        "fictional-service",
        transport=httpx.MockTransport(handler),
        clock=lambda: second_time,
    )

    assert requests == ["/items", "/items"]
    assert len(replacements) == 2
    assert all(source.parent == destination.parent for source, destination in replacements)
    assert all(destination == _state_path(root) for _source, destination in replacements)
    assert all(not source.exists() for source, _destination in replacements)
    assert _state_payload(root) == {
        "schema_version": 1,
        "connectors": {
            "fictional-service": {
                "current-items": "2026-08-03T23:25:26.123456Z",
            }
        },
    }


@pytest.mark.parametrize(
    ("schedule", "interval", "expected_schedule"),
    (
        ("hourly", timedelta(hours=1), SnapshotSchedule.HOURLY),
        ("daily", timedelta(days=1), SnapshotSchedule.DAILY),
        ("weekly", timedelta(days=7), SnapshotSchedule.WEEKLY),
    ),
)
def test_due_math_uses_injected_clock_at_each_exact_schedule_boundary(
    connector_tmp_path: Path,
    schedule: str,
    interval: timedelta,
    expected_schedule: SnapshotSchedule,
) -> None:
    root = initialize_connector_context(connector_tmp_path / "context")
    write_manifest(
        root,
        manifest_payload(
            snapshots=[
                {
                    "id": "current-items",
                    "path": "/items",
                    "schedule": schedule,
                }
            ]
        ),
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _success_response(request)

    transport = httpx.MockTransport(handler)
    sync(root, "fictional-service", transport=transport, clock=lambda: FIXED_NOW)
    requests.clear()

    just_before = FIXED_NOW + interval - timedelta(microseconds=1)
    before_status = status(root, clock=lambda: just_before)
    before_batch = sync_all(
        root,
        due_only=True,
        transport=transport,
        clock=lambda: just_before,
    )

    assert before_status.snapshots[0].schedule is expected_schedule
    assert before_status.snapshots[0].last_success == FIXED_NOW
    assert before_status.snapshots[0].due_now is False
    assert before_batch.outcomes[0].attempted is False
    assert requests == []

    boundary = FIXED_NOW + interval
    boundary_status = status(root, clock=lambda: boundary)
    boundary_batch = sync_all(
        root,
        due_only=True,
        transport=transport,
        clock=lambda: boundary,
    )

    assert boundary_status.snapshots[0].due_now is True
    assert boundary_batch.outcomes[0].succeeded is True
    assert boundary_batch.outcomes[0].snapshot_ids == ("current-items",)
    assert requests == ["/items"]


@pytest.mark.parametrize("state_kind", ("missing", "corrupt"))
def test_missing_or_corrupt_state_makes_all_scheduled_snapshots_due_without_error(
    connector_tmp_path: Path,
    state_kind: str,
) -> None:
    root = initialize_connector_context(connector_tmp_path / "context")
    write_manifest(
        root,
        manifest_payload(
            snapshots=[
                {"id": "hourly-items", "path": "/hourly", "schedule": "hourly"},
                {"id": "daily-items", "path": "/daily", "schedule": "daily"},
                {"id": "manual-items", "path": "/manual"},
            ]
        ),
    )
    if state_kind == "corrupt":
        _state_path(root).parent.mkdir(parents=True)
        _state_path(root).write_text("{not-json", encoding="utf-8")

    reported = status(root, clock=lambda: FIXED_NOW)

    assert [item.due_now for item in reported.snapshots] == [True, True, False]
    assert all(item.last_success is None for item in reported.snapshots)

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return _success_response(request)

    batch = sync_all(
        root,
        due_only=True,
        transport=httpx.MockTransport(handler),
        clock=lambda: FIXED_NOW,
    )

    assert requested == ["/hourly", "/daily"]
    assert batch.ok is True
    assert batch.outcomes[0].snapshot_ids == ("hourly-items", "daily-items")
    assert set(_state_payload(root)["connectors"]["fictional-service"]) == {
        "daily-items",
        "hourly-items",
    }


def test_sync_all_continues_after_failure_and_records_only_successful_connector(
    connector_tmp_path: Path,
) -> None:
    root = initialize_connector_context(connector_tmp_path / "context")
    write_manifest(
        root,
        manifest_payload(
            name="a-failing",
            snapshots=[{"id": "failed-items", "path": "/fail", "schedule": "hourly"}],
        ),
    )
    write_manifest(
        root,
        manifest_payload(
            name="b-successful",
            snapshots=[{"id": "good-items", "path": "/success", "schedule": "hourly"}],
        ),
    )
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/fail":
            return httpx.Response(503, content=b"fictional failure", request=request)
        return _success_response(request)

    batch = sync_all(
        root,
        due_only=True,
        transport=httpx.MockTransport(handler),
        clock=lambda: FIXED_NOW,
    )

    assert requested == ["/fail", "/success"]
    assert batch.ok is False
    assert batch.failure_count == 1
    assert [outcome.connector_name for outcome in batch.outcomes] == [
        "a-failing",
        "b-successful",
    ]
    failed, succeeded = batch.outcomes
    assert failed.failed is True
    assert failed.error is not None
    assert failed.error.kind is ConnectorSyncFailureKind.STATUS
    assert failed.error.snapshot_id == "failed-items"
    assert "503" in failed.error.message
    assert succeeded.succeeded is True
    assert succeeded.result is not None
    assert succeeded.result.snapshots[0].snapshot_id == "good-items"
    assert _state_payload(root)["connectors"] == {
        "b-successful": {"good-items": "2026-08-03T23:20:26.123456Z"}
    }
