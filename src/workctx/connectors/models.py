"""Typed connector manifest, provenance, and synchronization records."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from workctx.secrets import SecretRef

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_MAX_BYTES = 100 * 1024 * 1024

_KEBAB_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_QUERY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._~-]+$")

QueryValue = str | int | float | bool
DurationMilliseconds = Annotated[int, Field(ge=0)]
ByteCount = Annotated[int, Field(ge=0)]


class _ConnectorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotManifest(_ConnectorRecord):
    """One GET endpoint declared by an operator-authored connector manifest."""

    id: str
    path: str
    query: dict[str, QueryValue] = Field(default_factory=dict)
    accept: str = "application/json"
    schedule: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_kebab(value, label="snapshot id")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError("snapshot path must contain 1-2048 characters")
        if any(ord(character) < 32 for character in value) or "\\" in value:
            raise ValueError("snapshot path must be printable and use forward slashes")
        parsed = urlsplit(value)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or value.startswith("//")
        ):
            raise ValueError("snapshot path must not be an absolute URL or contain a query")
        if any(part in {".", ".."} for part in value.lstrip("/").split("/")):
            raise ValueError("snapshot path must not contain traversal segments")
        return value

    @field_validator("query", mode="before")
    @classmethod
    def validate_query(cls, value: object) -> object:
        if not isinstance(value, dict):
            raise ValueError("snapshot query must be a mapping")
        for key, item in value.items():
            if not isinstance(key, str) or _QUERY_NAME_PATTERN.fullmatch(key) is None:
                raise ValueError("query parameter names must use URL-safe characters")
            if type(item) not in {str, int, float, bool}:
                raise ValueError("query values must be strings, numbers, or booleans")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("query number values must be finite")
            if isinstance(item, str) and len(item) > 4096:
                raise ValueError("query string values must not exceed 4096 characters")
        return value

    @field_validator("accept")
    @classmethod
    def validate_accept(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 256
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("accept must be a printable non-empty HTTP header value")
        return value.strip()

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or len(value) > 256 or any(ord(character) < 32 for character in value):
            raise ValueError("schedule must be printable metadata")
        return value.strip()


class ConnectorManifest(_ConnectorRecord):
    """Versioned declarative contract for one generic snapshot connector."""

    schema_version: Literal[1]
    name: str
    base_url: str
    allow_insecure_http: StrictBool = False
    secret_ref: str | None = None
    auth_style: str | None = None
    snapshots: tuple[SnapshotManifest, ...] = Field(min_length=1)
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=MAX_TIMEOUT_SECONDS)
    max_bytes: int = Field(default=DEFAULT_MAX_BYTES, ge=1, le=MAX_MAX_BYTES)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_kebab(value, label="connector name")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError("base_url must contain 1-2048 characters")
        try:
            parsed = httpx.URL(value)
        except Exception as error:
            raise ValueError("base_url must be a valid absolute HTTP URL") from error
        if not parsed.is_absolute_url or parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("base_url must be a valid absolute HTTP URL")
        if parsed.userinfo or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, a query, or a fragment")
        return value

    @field_validator("secret_ref")
    @classmethod
    def validate_secret_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return SecretRef.parse(value).name

    @field_validator("auth_style")
    @classmethod
    def validate_auth_style(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _parse_auth_style(value)
        return value

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def validate_timeout_type(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("timeout_seconds must be a number")
        return value

    @field_validator("max_bytes", mode="before")
    @classmethod
    def validate_max_bytes_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("max_bytes must be an integer")
        return value

    @model_validator(mode="after")
    def validate_relational_contract(self) -> Self:
        if self.base_url.startswith("http://") and not self.allow_insecure_http:
            raise ValueError("plain HTTP requires allow_insecure_http: true")
        if (self.secret_ref is None) != (self.auth_style is None):
            raise ValueError("secret_ref and auth_style must be declared together")
        snapshot_ids = tuple(snapshot.id for snapshot in self.snapshots)
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("snapshot ids must be unique within a connector")
        if self.auth_style is not None:
            kind, parameter = _parse_auth_style(self.auth_style)
            if (
                kind == "query"
                and parameter is not None
                and any(parameter in snapshot.query for snapshot in self.snapshots)
            ):
                raise ValueError("an auth query parameter cannot also be declared in query")
        return self


class ProvenanceSecretRef(_ConnectorRecord):
    """Names-only marker replacing a secret-bearing query parameter value."""

    secret_ref: str


ProvenanceQueryValue = QueryValue | ProvenanceSecretRef


class SnapshotProvenance(_ConnectorRecord):
    """Persisted content-free provenance sidecar for one connector response."""

    schema_version: Literal[1] = 1
    system: str
    base_url: str
    path: str
    query: dict[str, ProvenanceQueryValue]
    http_status: int = Field(ge=100, le=599)
    response_content_type: str | None
    byte_count: ByteCount
    retrieved_at: AwareDatetime


class SnapshotSyncDisposition(StrEnum):
    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"
    DUPLICATE = "duplicate"
    DUPLICATE_LINKED = "duplicate_linked"
    QUARANTINED = "quarantined"


class SnapshotSyncResult(_ConnectorRecord):
    """Envelope-safe outcome for one selected snapshot."""

    snapshot_id: str
    disposition: SnapshotSyncDisposition
    artifact_id: str
    artifact_ref: str
    byte_count: ByteCount
    duration_ms: DurationMilliseconds
    retrieved_at: AwareDatetime
    diagnostics: tuple[str, ...] = ()


class SyncResult(_ConnectorRecord):
    """Envelope-safe connector synchronization result."""

    connector_name: str
    snapshots: tuple[SnapshotSyncResult, ...]
    duration_ms: DurationMilliseconds


def _validate_kebab(value: str, *, label: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or _KEBAB_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be 1-64 characters of lowercase kebab-case")
    return value


def _parse_auth_style(value: str) -> tuple[str, str | None]:
    if value == "bearer":
        return "bearer", None
    kind, separator, parameter = value.partition(":")
    if not separator or not parameter:
        raise ValueError("auth_style must be bearer, header:<Name>, or query:<param>")
    if kind == "header" and _HEADER_NAME_PATTERN.fullmatch(parameter) is not None:
        if parameter.casefold() in {"connection", "content-length", "host", "transfer-encoding"}:
            raise ValueError("auth_style cannot control a connection framing header")
        return kind, parameter
    if kind == "query" and _QUERY_NAME_PATTERN.fullmatch(parameter) is not None:
        return kind, parameter
    raise ValueError("auth_style must be bearer, header:<Name>, or query:<param>")


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_MAX_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "ConnectorManifest",
    "ProvenanceSecretRef",
    "QueryValue",
    "SnapshotManifest",
    "SnapshotProvenance",
    "SnapshotSyncDisposition",
    "SnapshotSyncResult",
    "SyncResult",
]
