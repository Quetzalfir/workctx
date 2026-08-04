from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from workctx.connectors import (
    ConnectorConnectionError,
    ConnectorRedirectError,
    ConnectorSecretExposureError,
    ConnectorSecretResolutionError,
    ConnectorSizeLimitError,
    ConnectorStatusError,
    ConnectorTimeoutError,
    SnapshotSyncDisposition,
    sync,
)
from workctx.secrets import store

from .support import (
    FICTIONAL_TOKEN,
    FIXED_NOW,
    SECRET_REF,
    initialize_connector_context,
    manifest_payload,
    workspace_file_bytes,
    write_manifest,
)


class TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.yield_count = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.yield_count += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def _authenticated_context(root: Path, *, max_bytes: int = 10 * 1024 * 1024) -> Path:
    initialize_connector_context(root)
    store(SECRET_REF, FICTIONAL_TOKEN)
    write_manifest(
        root,
        manifest_payload(
            secret_ref=SECRET_REF,
            auth_style="bearer",
            max_bytes=max_bytes,
            snapshots=[{"id": "secure-snapshot", "path": "/secure"}],
        ),
    )
    return root


def _assert_secret_free_failure(
    error: Exception,
    root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    surfaces = (
        str(error),
        repr(error),
        json.dumps(vars(error), default=str, sort_keys=True),
        "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        caplog.text,
    )
    assert all(FICTIONAL_TOKEN not in surface for surface in surfaces)
    assert error.__cause__ is None
    assert error.__context__ is None
    current_traceback = error.__traceback__
    while current_traceback is not None:
        frame_path = current_traceback.tb_frame.f_code.co_filename.replace("\\", "/")
        if "/src/workctx/connectors/" in frame_path:
            assert FICTIONAL_TOKEN not in repr(current_traceback.tb_frame.f_locals)
        current_traceback = current_traceback.tb_next
    assert all(FICTIONAL_TOKEN.encode() not in content for content in workspace_file_bytes(root))


def test_size_cap_streams_and_aborts_without_partial_snapshot(
    connector_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    root = _authenticated_context(connector_tmp_path / "context", max_bytes=5)
    stream = TrackingStream((b"1234", b"5678", b"must-not-be-read"))
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            stream=stream,
            request=request,
        )

    with pytest.raises(ConnectorSizeLimitError) as caught:
        sync(
            root,
            "fictional-service",
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_NOW,
        )

    assert captured[0].headers["Authorization"] == f"Bearer {FICTIONAL_TOKEN}"
    assert stream.yield_count == 2
    assert stream.closed is True
    assert not list((root / "00_inbox").rglob("fictional-service-*"))
    _assert_secret_free_failure(caught.value, root, caplog)


def test_cross_host_redirect_is_refused_before_secret_can_reach_target(
    connector_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    root = _authenticated_context(connector_tmp_path / "context")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            302,
            headers={
                "Location": (f"https://other.example.test/private?reflected={FICTIONAL_TOKEN}")
            },
            request=request,
        )

    with pytest.raises(ConnectorRedirectError) as caught:
        sync(
            root,
            "fictional-service",
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_NOW,
        )

    assert len(captured) == 1
    assert captured[0].url.host == "api.example.test"
    assert captured[0].headers["Authorization"] == f"Bearer {FICTIONAL_TOKEN}"
    _assert_secret_free_failure(caught.value, root, caplog)


def test_same_host_redirect_reinjects_authentication_and_succeeds(
    connector_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    root = _authenticated_context(connector_tmp_path / "context")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/secure":
            return httpx.Response(307, headers={"Location": "/final"}, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b'{"fictional":"redirected"}',
            request=request,
        )

    result = sync(
        root,
        "fictional-service",
        transport=httpx.MockTransport(handler),
        clock=lambda: FIXED_NOW,
    )

    assert [request.url.path for request in captured] == ["/secure", "/final"]
    assert all(
        request.headers["Authorization"] == f"Bearer {FICTIONAL_TOKEN}" for request in captured
    )
    assert result.snapshots[0].disposition is SnapshotSyncDisposition.REGISTERED
    assert FICTIONAL_TOKEN not in caplog.text


def test_redirect_limit_is_three(connector_tmp_path: Path) -> None:
    root = _authenticated_context(connector_tmp_path / "context")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            302,
            headers={"Location": f"/redirect-{len(captured)}"},
            request=request,
        )

    with pytest.raises(ConnectorRedirectError):
        sync(
            root,
            "fictional-service",
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_NOW,
        )

    assert len(captured) == 4


@pytest.mark.parametrize(
    ("failure_mode", "error_type"),
    (
        ("connection", ConnectorConnectionError),
        ("timeout", ConnectorTimeoutError),
        ("status", ConnectorStatusError),
    ),
)
def test_transport_failures_are_typed_content_free_and_body_free(
    connector_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failure_mode: str,
    error_type: type[Exception],
) -> None:
    caplog.set_level(logging.DEBUG)
    root = _authenticated_context(connector_tmp_path / "context")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if failure_mode == "connection":
            raise httpx.ConnectError(
                f"fictional backend echoed {FICTIONAL_TOKEN}",
                request=request,
            )
        if failure_mode == "timeout":
            raise httpx.ReadTimeout(
                f"fictional timeout echoed {FICTIONAL_TOKEN}",
                request=request,
            )
        return httpx.Response(
            503,
            headers={"X-Fictional-Debug": FICTIONAL_TOKEN},
            content=f"hostile status body {FICTIONAL_TOKEN}".encode(),
            extensions={
                "http_version": FICTIONAL_TOKEN.encode(),
                "reason_phrase": FICTIONAL_TOKEN.encode(),
            },
            request=request,
        )

    with pytest.raises(error_type) as caught:
        sync(
            root,
            "fictional-service",
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_NOW,
        )

    assert captured[0].headers["Authorization"] == f"Bearer {FICTIONAL_TOKEN}"
    assert "fictional-service" in str(caught.value)
    assert "secure-snapshot" in str(caught.value)
    if failure_mode == "status":
        assert isinstance(caught.value, ConnectorStatusError)
        assert caught.value.status_code == 503
    _assert_secret_free_failure(caught.value, root, caplog)


def test_success_response_reflecting_secret_is_refused_before_any_write(
    connector_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    root = _authenticated_context(connector_tmp_path / "context")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=f"reflected credential: {FICTIONAL_TOKEN}".encode(),
            request=request,
        )

    with pytest.raises(ConnectorSecretExposureError) as caught:
        sync(
            root,
            "fictional-service",
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_NOW,
        )

    assert captured[0].headers["Authorization"] == f"Bearer {FICTIONAL_TOKEN}"
    assert not list((root / "00_inbox").rglob("fictional-service-*"))
    _assert_secret_free_failure(caught.value, root, caplog)


def test_missing_secret_is_typed_and_makes_no_request(
    connector_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    root = initialize_connector_context(connector_tmp_path / "context")
    write_manifest(
        root,
        manifest_payload(
            secret_ref=SECRET_REF,
            auth_style="bearer",
            snapshots=[{"id": "secure-snapshot", "path": "/secure"}],
        ),
    )
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=request)

    with pytest.raises(ConnectorSecretResolutionError) as caught:
        sync(
            root,
            "fictional-service",
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_NOW,
        )

    assert called is False
    _assert_secret_free_failure(caught.value, root, caplog)
