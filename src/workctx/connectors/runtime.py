"""Generic declarative connector snapshot runtime."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, quote_plus

import httpx

from workctx.adapters.filesystem._paths import canonical_context_root, resolve_context_path
from workctx.connectors.errors import (
    ConnectorConnectionError,
    ConnectorManifestError,
    ConnectorNotFoundError,
    ConnectorRedirectError,
    ConnectorRegistrationError,
    ConnectorSecretExposureError,
    ConnectorSecretResolutionError,
    ConnectorSizeLimitError,
    ConnectorSnapshotNotFoundError,
    ConnectorStatusError,
    ConnectorTimeoutError,
    ConnectorWriteError,
)
from workctx.connectors.manifests import load_manifests
from workctx.connectors.models import (
    ConnectorManifest,
    ProvenanceSecretRef,
    SnapshotManifest,
    SnapshotProvenance,
    SnapshotSyncDisposition,
    SnapshotSyncResult,
    SyncResult,
    _parse_auth_style,
)
from workctx.domain.artifacts import ArtifactSourceType
from workctx.ingestion import IngestionService, RegisterRequest, RegistrationDisposition
from workctx.secrets import SecretValue, resolve

Clock = Callable[[], datetime]

_RAW_DIRECTORY = "00_inbox/raw"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 3
_MAX_CONTENT_TYPE_LENGTH = 1024


class _FailureKind(StrEnum):
    MANIFEST = "manifest"
    NOT_FOUND = "not_found"
    SNAPSHOT_NOT_FOUND = "snapshot_not_found"
    SECRET = "secret"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    STATUS = "status"
    SIZE = "size"
    REDIRECT = "redirect"
    SECRET_EXPOSURE = "secret_exposure"
    WRITE = "write"
    REGISTRATION = "registration"


@dataclass(frozen=True, slots=True)
class _Failure:
    kind: _FailureKind
    connector_name: str
    snapshot_id: str = "snapshot"
    path: str = "07_connectors"
    status_code: int | None = None
    max_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class _FetchPayload:
    body: bytes
    status_code: int
    content_type: str | None


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    payload: _FetchPayload | None = None
    failure: _Failure | None = None


@dataclass(frozen=True, slots=True)
class _WrittenSnapshot:
    snapshot: SnapshotManifest
    primary_path: str
    sidecar_path: str
    content_type: str | None
    byte_count: int
    duration_ms: int
    retrieved_at: datetime
    created_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _OperationOutcome:
    result: SyncResult | None = None
    failure: _Failure | None = None


class _RequestBoundaryTransport(httpx.BaseTransport):
    """Reveal authentication only into the request handed to the real transport.

    HTTPX logs the request passed to ``Client.send`` at INFO level.  Keeping that
    logical request credential-free and cloning it at the transport boundary
    prevents query authentication from entering library logs.
    """

    def __init__(
        self,
        inner: httpx.BaseTransport,
        auth_style: str | None,
        secret: SecretValue | None,
    ) -> None:
        self._inner = inner
        self._auth_style = auth_style
        self._secret = secret
        self._last_outgoing_request: httpx.Request | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._last_outgoing_request = None
        outgoing = httpx.Request(
            request.method,
            request.url,
            headers=request.headers,
            stream=request.stream,
            extensions=dict(request.extensions),
        )
        if not _inject_authentication(outgoing, self._auth_style, self._secret):
            raise RuntimeError("connector authentication configuration is invalid")
        self._last_outgoing_request = outgoing
        response = self._inner.handle_request(outgoing)
        _sanitize_response_logging_metadata(response)
        return response

    def take_outgoing_request(self) -> httpx.Request | None:
        request = self._last_outgoing_request
        self._last_outgoing_request = None
        return request

    def clear_secret(self) -> None:
        self._secret = None
        self._last_outgoing_request = None

    def close(self) -> None:
        self._inner.close()


def sync(
    root: Path,
    name: str,
    *,
    snapshot_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
    clock: Clock | None = None,
) -> SyncResult:
    """Fetch and register selected snapshots for one declarative connector."""

    outcome = _run_sync(
        root,
        name,
        snapshot_id=snapshot_id,
        transport=transport,
        clock=clock or _utc_now,
    )
    if outcome.failure is not None:
        failure = outcome.failure
        del transport, clock
        _raise_failure(failure)
    result = outcome.result
    if result is None:
        failure = _Failure(_FailureKind.REGISTRATION, name, snapshot_id or "snapshot")
        del transport, clock
        _raise_failure(failure)
    return result


def _run_sync(
    root: Path,
    name: str,
    *,
    snapshot_id: str | None,
    transport: httpx.BaseTransport | None,
    clock: Clock,
) -> _OperationOutcome:
    try:
        manifests = load_manifests(root)
    except ConnectorManifestError as error:
        path = getattr(error, "path", "07_connectors")
        return _OperationOutcome(failure=_Failure(_FailureKind.MANIFEST, name, path=path))
    except Exception:
        return _OperationOutcome(failure=_Failure(_FailureKind.MANIFEST, name))

    manifest = next((candidate for candidate in manifests if candidate.name == name), None)
    if manifest is None:
        return _OperationOutcome(failure=_Failure(_FailureKind.NOT_FOUND, name))

    selected = _select_snapshots(manifest, snapshot_id)
    if selected is None:
        return _OperationOutcome(
            failure=_Failure(_FailureKind.SNAPSHOT_NOT_FOUND, name, snapshot_id or "snapshot")
        )
    first_snapshot_id = selected[0].id
    started_at = _read_clock(clock)
    if started_at is None:
        return _OperationOutcome(failure=_Failure(_FailureKind.WRITE, name, first_snapshot_id))

    secret: SecretValue | None = None
    if manifest.secret_ref is not None:
        resolution_failed = False
        try:
            secret = resolve(manifest.secret_ref)
        except Exception:
            resolution_failed = True
        if resolution_failed:
            return _OperationOutcome(failure=_Failure(_FailureKind.SECRET, name, first_snapshot_id))

    client: httpx.Client | None = None
    request_transport: _RequestBoundaryTransport | None = None
    client_failed = False
    try:
        request_transport = _RequestBoundaryTransport(
            transport or httpx.HTTPTransport(trust_env=False),
            manifest.auth_style,
            secret,
        )
        client = httpx.Client(
            timeout=httpx.Timeout(manifest.timeout_seconds),
            follow_redirects=False,
            transport=request_transport,
            trust_env=False,
        )
    except Exception:
        client_failed = True
    if client_failed or client is None:
        if request_transport is not None:
            request_transport.clear_secret()
        return _OperationOutcome(failure=_Failure(_FailureKind.CONNECTION, name, first_snapshot_id))
    assert request_transport is not None

    written: list[_WrittenSnapshot] = []
    operation_failure: _Failure | None = None
    for snapshot in selected:
        snapshot_started = _read_clock(clock)
        if snapshot_started is None:
            operation_failure = _Failure(_FailureKind.WRITE, name, snapshot.id)
            break
        fetch = _fetch_snapshot(client, request_transport, manifest, snapshot)
        if fetch.failure is not None:
            operation_failure = fetch.failure
            break
        if fetch.payload is None:
            operation_failure = _Failure(_FailureKind.CONNECTION, name, snapshot.id)
            break

        retrieved_at = _read_clock(clock)
        if retrieved_at is None:
            operation_failure = _Failure(_FailureKind.WRITE, name, snapshot.id)
            break
        provenance: SnapshotProvenance | None = None
        try:
            provenance = _provenance(manifest, snapshot, fetch.payload, retrieved_at)
        except Exception:
            operation_failure = _Failure(_FailureKind.WRITE, name, snapshot.id)
        if provenance is None:
            break
        write = _write_snapshot(
            root,
            manifest,
            snapshot,
            fetch.payload,
            provenance,
            retrieved_at,
            _duration_ms(snapshot_started, retrieved_at),
        )
        if write is None:
            operation_failure = _Failure(_FailureKind.WRITE, name, snapshot.id)
            break
        written.append(write)

    close_failed = _close_client(client)
    client = None
    request_transport.clear_secret()
    request_transport = None
    secret = None
    if operation_failure is None and close_failed:
        operation_failure = _Failure(_FailureKind.CONNECTION, name, first_snapshot_id)
    if operation_failure is not None:
        _cleanup_created(written)
        return _OperationOutcome(failure=operation_failure)

    registration = _register_snapshots(root, manifest, tuple(written), clock)
    if registration.failure is not None:
        return registration

    ended_at = _read_clock(clock) or started_at
    if registration.result is None:
        return _OperationOutcome(
            failure=_Failure(_FailureKind.REGISTRATION, name, first_snapshot_id)
        )
    return _OperationOutcome(
        result=registration.result.model_copy(
            update={"duration_ms": _duration_ms(started_at, ended_at)}
        )
    )


def _select_snapshots(
    manifest: ConnectorManifest,
    snapshot_id: str | None,
) -> tuple[SnapshotManifest, ...] | None:
    if snapshot_id is None:
        return manifest.snapshots
    selected = tuple(snapshot for snapshot in manifest.snapshots if snapshot.id == snapshot_id)
    return selected or None


def _fetch_snapshot(
    client: httpx.Client,
    request_transport: _RequestBoundaryTransport,
    manifest: ConnectorManifest,
    snapshot: SnapshotManifest,
) -> _FetchOutcome:
    try:
        base_url = httpx.URL(manifest.base_url)
        current_url = base_url.join(snapshot.path)
    except Exception:
        return _FetchOutcome(failure=_Failure(_FailureKind.CONNECTION, manifest.name, snapshot.id))
    if base_url.host is None:
        return _FetchOutcome(failure=_Failure(_FailureKind.CONNECTION, manifest.name, snapshot.id))

    include_manifest_query = True
    redirect_count = 0
    while True:
        request = _build_request(
            client,
            current_url,
            snapshot,
            include_manifest_query=include_manifest_query,
        )
        if request is None:
            return _FetchOutcome(
                failure=_Failure(_FailureKind.CONNECTION, manifest.name, snapshot.id)
            )

        response, outgoing_request, failure = _send_request(
            client,
            request_transport,
            request,
            manifest.name,
            snapshot.id,
        )
        if failure is not None:
            return _FetchOutcome(failure=failure)
        if response is None or outgoing_request is None:
            return _FetchOutcome(
                failure=_Failure(_FailureKind.CONNECTION, manifest.name, snapshot.id)
            )

        if response.status_code in _REDIRECT_STATUSES:
            if redirect_count >= _MAX_REDIRECTS:
                _close_response(response)
                return _FetchOutcome(
                    failure=_Failure(_FailureKind.REDIRECT, manifest.name, snapshot.id)
                )
            target = _redirect_target(response, request.url, base_url.host)
            close_failed = _close_response(response)
            if target is None or close_failed:
                return _FetchOutcome(
                    failure=_Failure(
                        _FailureKind.CONNECTION if close_failed else _FailureKind.REDIRECT,
                        manifest.name,
                        snapshot.id,
                    )
                )
            current_url = target
            include_manifest_query = False
            redirect_count += 1
            continue

        if not 200 <= response.status_code < 300:
            status_code = response.status_code
            close_failed = _close_response(response)
            if close_failed:
                return _FetchOutcome(
                    failure=_Failure(_FailureKind.CONNECTION, manifest.name, snapshot.id)
                )
            return _FetchOutcome(
                failure=_Failure(
                    _FailureKind.STATUS,
                    manifest.name,
                    snapshot.id,
                    status_code=status_code,
                )
            )

        return _read_response(response, outgoing_request, manifest, snapshot)


def _build_request(
    client: httpx.Client,
    url: httpx.URL,
    snapshot: SnapshotManifest,
    *,
    include_manifest_query: bool,
) -> httpx.Request | None:
    request: httpx.Request | None = None
    try:
        request = client.build_request(
            "GET",
            url,
            params=snapshot.query if include_manifest_query else None,
            headers={"Accept": snapshot.accept},
        )
        return request
    except Exception:
        request = None
        return None


def _inject_authentication(
    request: httpx.Request,
    auth_style: str | None,
    secret: SecretValue | None,
) -> bool:
    try:
        if auth_style is None:
            return secret is None
        if secret is None:
            return False
        kind, parameter = _parse_auth_style(auth_style)
        if kind == "bearer":
            request.headers["Authorization"] = f"Bearer {secret.reveal()}"
            return True
        if kind == "header" and parameter is not None:
            request.headers[parameter] = secret.reveal()
            return True
        if kind == "query" and parameter is not None:
            request.url = request.url.copy_set_param(parameter, secret.reveal())
            return True
        return False
    except Exception:
        return False


def _sanitize_response_logging_metadata(response: httpx.Response) -> None:
    """Discard untrusted response fields that HTTPX logs before returning."""

    response.extensions["reason_phrase"] = b""
    if response.extensions.get("http_version") not in {
        None,
        b"HTTP/1.0",
        b"HTTP/1.1",
        b"HTTP/2",
        b"HTTP/3",
    }:
        response.extensions["http_version"] = b"HTTP"


def _send_request(
    client: httpx.Client,
    request_transport: _RequestBoundaryTransport,
    request: httpx.Request,
    connector_name: str,
    snapshot_id: str,
) -> tuple[httpx.Response | None, httpx.Request | None, _Failure | None]:
    response: httpx.Response | None = None
    failure_kind: _FailureKind | None = None
    try:
        response = client.send(request, stream=True)
    except httpx.TimeoutException:
        failure_kind = _FailureKind.TIMEOUT
    except Exception:
        failure_kind = _FailureKind.CONNECTION
    outgoing_request = request_transport.take_outgoing_request()
    if failure_kind is not None:
        outgoing_request = None
        return None, None, _Failure(failure_kind, connector_name, snapshot_id)
    return response, outgoing_request, None


def _read_response(
    response: httpx.Response,
    request: httpx.Request,
    manifest: ConnectorManifest,
    snapshot: SnapshotManifest,
) -> _FetchOutcome:
    content_type = _content_type(response)
    body = bytearray()
    failure_kind: _FailureKind | None = None
    try:
        if response.is_stream_consumed:
            buffered = response.content
            if len(buffered) > manifest.max_bytes:
                failure_kind = _FailureKind.SIZE
            else:
                body.extend(buffered)
        else:
            for chunk in response.iter_raw():
                if len(body) + len(chunk) > manifest.max_bytes:
                    failure_kind = _FailureKind.SIZE
                    break
                body.extend(chunk)
    except httpx.TimeoutException:
        failure_kind = _FailureKind.TIMEOUT
    except Exception:
        failure_kind = _FailureKind.CONNECTION

    close_failed = _close_response(response)
    if failure_kind is None and close_failed:
        failure_kind = _FailureKind.CONNECTION
    if failure_kind is not None:
        body.clear()
        return _FetchOutcome(
            failure=_Failure(
                failure_kind,
                manifest.name,
                snapshot.id,
                max_bytes=manifest.max_bytes if failure_kind is _FailureKind.SIZE else None,
            )
        )

    immutable_body = bytes(body)
    body.clear()
    if _response_reflects_authentication(
        request,
        immutable_body,
        content_type,
        manifest.auth_style,
    ):
        immutable_body = b""
        return _FetchOutcome(
            failure=_Failure(_FailureKind.SECRET_EXPOSURE, manifest.name, snapshot.id)
        )
    return _FetchOutcome(
        payload=_FetchPayload(
            body=immutable_body,
            status_code=response.status_code,
            content_type=content_type,
        )
    )


def _redirect_target(
    response: httpx.Response,
    current_url: httpx.URL,
    allowed_host: str,
) -> httpx.URL | None:
    try:
        location = response.headers.get("location")
        if location is None or len(location) > 8192:
            return None
        target = current_url.join(location)
        if (
            target.scheme not in {"http", "https"}
            or target.host.casefold() != allowed_host.casefold()
            or target.userinfo
        ):
            return None
        if current_url.scheme == "https" and target.scheme != "https":
            return None
        return target
    except Exception:
        return None


def _content_type(response: httpx.Response) -> str | None:
    try:
        value = response.headers.get("content-type")
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    if len(value) > _MAX_CONTENT_TYPE_LENGTH or any(ord(character) < 32 for character in value):
        return None
    return value


def _response_reflects_authentication(
    request: httpx.Request,
    body: bytes,
    content_type: str | None,
    auth_style: str | None,
) -> bool:
    if auth_style is None:
        return False
    try:
        kind, parameter = _parse_auth_style(auth_style)
        material = ""
        if kind == "bearer":
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                material = authorization.removeprefix("Bearer ")
        elif kind == "header" and parameter is not None:
            material = request.headers.get(parameter, "")
        elif kind == "query" and parameter is not None:
            material = request.url.params.get(parameter, "")
        if not material:
            return False
        representations = {
            material,
            quote(material, safe=""),
            quote_plus(material, safe=""),
        }
        return any(
            representation.encode("utf-8") in body
            or (content_type is not None and representation in content_type)
            for representation in representations
            if representation
        )
    except Exception:
        return True


def _provenance(
    manifest: ConnectorManifest,
    snapshot: SnapshotManifest,
    response: _FetchPayload,
    retrieved_at: datetime,
) -> SnapshotProvenance:
    query: dict[str, object] = dict(snapshot.query)
    if manifest.auth_style is not None and manifest.secret_ref is not None:
        kind, parameter = _parse_auth_style(manifest.auth_style)
        if kind == "query" and parameter is not None:
            query[parameter] = ProvenanceSecretRef(secret_ref=manifest.secret_ref)
    return SnapshotProvenance.model_validate(
        {
            "schema_version": 1,
            "system": manifest.name,
            "base_url": manifest.base_url,
            "path": snapshot.path,
            "query": query,
            "http_status": response.status_code,
            "response_content_type": response.content_type,
            "byte_count": len(response.body),
            "retrieved_at": retrieved_at,
        }
    )


def _write_snapshot(
    root: Path,
    manifest: ConnectorManifest,
    snapshot: SnapshotManifest,
    response: _FetchPayload,
    provenance: SnapshotProvenance,
    retrieved_at: datetime,
    duration_ms: int,
) -> _WrittenSnapshot | None:
    created: list[Path] = []
    try:
        context_root = canonical_context_root(root)
        raw_dir = resolve_context_path(
            context_root,
            _RAW_DIRECTORY,
            allowed_prefixes=(_RAW_DIRECTORY,),
        )
        if not raw_dir.exists():
            if not raw_dir.parent.is_dir() or raw_dir.parent.is_symlink():
                return None
            raw_dir.mkdir()
        if not raw_dir.is_dir() or raw_dir.is_symlink() or _is_junction(raw_dir):
            return None

        extension = _extension_for_content_type(response.content_type)
        timestamp = retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{manifest.name}-{snapshot.id}-{timestamp}.{extension}"
        primary_relative = f"{_RAW_DIRECTORY}/{filename}"
        sidecar_relative = f"{primary_relative}.provenance.json"
        primary = resolve_context_path(
            context_root,
            primary_relative,
            allowed_prefixes=(_RAW_DIRECTORY,),
        )
        sidecar = resolve_context_path(
            context_root,
            sidecar_relative,
            allowed_prefixes=(_RAW_DIRECTORY,),
        )
        sidecar_bytes = (
            json.dumps(
                provenance.model_dump(mode="json"),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )

        primary_ok, primary_created = _write_or_match(primary, response.body)
        if not primary_ok:
            return None
        if primary_created:
            created.append(primary)
        sidecar_ok, sidecar_created = _write_or_match(sidecar, sidecar_bytes)
        if not sidecar_ok:
            for path in reversed(created):
                _safe_unlink(path)
            return None
        if sidecar_created:
            created.append(sidecar)
        return _WrittenSnapshot(
            snapshot=snapshot,
            primary_path=primary_relative,
            sidecar_path=sidecar_relative,
            content_type=response.content_type,
            byte_count=len(response.body),
            duration_ms=duration_ms,
            retrieved_at=retrieved_at,
            created_paths=tuple(created),
        )
    except Exception:
        for path in reversed(created):
            _safe_unlink(path)
        return None


def _write_or_match(path: Path, content: bytes) -> tuple[bool, bool]:
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return True, True
    except FileExistsError:
        try:
            if path.is_symlink() or _is_junction(path) or not path.is_file():
                return False, False
            return path.read_bytes() == content, False
        except OSError:
            return False, False
    except OSError:
        _safe_unlink(path)
        return False, False


def _register_snapshots(
    root: Path,
    manifest: ConnectorManifest,
    written: tuple[_WrittenSnapshot, ...],
    clock: Clock,
) -> _OperationOutcome:
    if not written:
        return _OperationOutcome(
            result=SyncResult(connector_name=manifest.name, snapshots=(), duration_ms=0)
        )
    try:
        service = IngestionService(root, clock=clock)
        requests = tuple(
            RegisterRequest(
                path=item.primary_path,
                source_type=ArtifactSourceType.EXTERNAL_SNAPSHOT,
                source_origin=f"connector:{manifest.name}",
                media_type=item.content_type,
                event_at=item.retrieved_at,
                sidecars=(item.sidecar_path,),
            )
            for item in written
        )
        batch = service.register_batch(
            requests,
            session_id=f"connector-{manifest.name}-{written[0].retrieved_at:%Y%m%dT%H%M%S%fZ}",
        )
    except Exception:
        return _OperationOutcome(
            failure=_Failure(_FailureKind.REGISTRATION, manifest.name, written[0].snapshot.id)
        )

    results: list[SnapshotSyncResult] = []
    try:
        paired_outcomes = zip(written, batch.outcomes, strict=True)
        for item, outcome in paired_outcomes:
            if outcome.registration is not None:
                registered = outcome.registration
                disposition = _registration_disposition(registered.disposition)
                results.append(
                    SnapshotSyncResult(
                        snapshot_id=item.snapshot.id,
                        disposition=disposition,
                        artifact_id=registered.artifact.manifest.id,
                        artifact_ref=registered.artifact.reference,
                        byte_count=item.byte_count,
                        duration_ms=item.duration_ms,
                        retrieved_at=item.retrieved_at,
                        diagnostics=tuple(
                            diagnostic.reason.value for diagnostic in registered.diagnostics
                        ),
                    )
                )
                continue
            if outcome.duplicate is not None:
                results.append(
                    SnapshotSyncResult(
                        snapshot_id=item.snapshot.id,
                        disposition=SnapshotSyncDisposition.DUPLICATE,
                        artifact_id=outcome.duplicate.manifest.id,
                        artifact_ref=outcome.duplicate.reference,
                        byte_count=item.byte_count,
                        duration_ms=item.duration_ms,
                        retrieved_at=item.retrieved_at,
                    )
                )
                continue
            return _OperationOutcome(
                failure=_Failure(_FailureKind.REGISTRATION, manifest.name, item.snapshot.id)
            )
    except Exception:
        return _OperationOutcome(
            failure=_Failure(_FailureKind.REGISTRATION, manifest.name, written[0].snapshot.id)
        )

    return _OperationOutcome(
        result=SyncResult(connector_name=manifest.name, snapshots=tuple(results), duration_ms=0)
    )


def _registration_disposition(
    disposition: RegistrationDisposition,
) -> SnapshotSyncDisposition:
    return {
        RegistrationDisposition.REGISTERED: SnapshotSyncDisposition.REGISTERED,
        RegistrationDisposition.ALREADY_REGISTERED: SnapshotSyncDisposition.ALREADY_REGISTERED,
        RegistrationDisposition.DUPLICATE_LINKED: SnapshotSyncDisposition.DUPLICATE_LINKED,
        RegistrationDisposition.QUARANTINED: SnapshotSyncDisposition.QUARANTINED,
    }[disposition]


def _extension_for_content_type(content_type: str | None) -> str:
    media_type = (content_type or "").partition(";")[0].strip().lower()
    if media_type == "application/json" or media_type.endswith("+json"):
        return "json"
    if media_type in {"application/xml", "text/xml"} or media_type.endswith("+xml"):
        return "xml"
    if media_type.startswith("text/"):
        return "txt"
    return "bin"


def _close_response(response: httpx.Response) -> bool:
    failed = False
    try:
        response.close()
    except Exception:
        failed = True
    return failed


def _close_client(client: httpx.Client) -> bool:
    failed = False
    try:
        client.close()
    except Exception:
        failed = True
    return failed


def _cleanup_created(written: list[_WrittenSnapshot]) -> None:
    for item in reversed(written):
        for path in reversed(item.created_paths):
            _safe_unlink(path)


def _safe_unlink(path: Path) -> None:
    try:
        if path.is_file() and not path.is_symlink() and not _is_junction(path):
            path.unlink()
    except OSError:
        pass


def _is_junction(path: Path) -> bool:
    return hasattr(path, "is_junction") and path.is_junction()


def _read_clock(clock: Clock) -> datetime | None:
    try:
        value = clock()
    except Exception:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _duration_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _raise_failure(failure: _Failure) -> NoReturn:
    if failure.kind is _FailureKind.MANIFEST:
        raise ConnectorManifestError(failure.path)
    if failure.kind is _FailureKind.NOT_FOUND:
        raise ConnectorNotFoundError(failure.connector_name)
    if failure.kind is _FailureKind.SNAPSHOT_NOT_FOUND:
        raise ConnectorSnapshotNotFoundError(failure.connector_name, failure.snapshot_id)
    if failure.kind is _FailureKind.SECRET:
        raise ConnectorSecretResolutionError(failure.connector_name, failure.snapshot_id)
    if failure.kind is _FailureKind.TIMEOUT:
        raise ConnectorTimeoutError(failure.connector_name, failure.snapshot_id)
    if failure.kind is _FailureKind.STATUS:
        raise ConnectorStatusError(
            failure.connector_name,
            failure.snapshot_id,
            failure.status_code or 500,
        )
    if failure.kind is _FailureKind.SIZE:
        raise ConnectorSizeLimitError(
            failure.connector_name,
            failure.snapshot_id,
            failure.max_bytes or 1,
        )
    if failure.kind is _FailureKind.REDIRECT:
        raise ConnectorRedirectError(failure.connector_name, failure.snapshot_id)
    if failure.kind is _FailureKind.SECRET_EXPOSURE:
        raise ConnectorSecretExposureError(failure.connector_name, failure.snapshot_id)
    if failure.kind is _FailureKind.WRITE:
        raise ConnectorWriteError(failure.connector_name, failure.snapshot_id)
    if failure.kind is _FailureKind.REGISTRATION:
        raise ConnectorRegistrationError(failure.connector_name, failure.snapshot_id)
    raise ConnectorConnectionError(failure.connector_name, failure.snapshot_id)


__all__ = ["Clock", "sync"]
