from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from workctx.adapters.sqlite import SQLiteProjection
from workctx.ingestion import ArtifactRecord, IngestionService
from workctx.services.contexts import initialize_context

FIXED_NOW = datetime(2026, 8, 3, 23, 20, 26, 123456, tzinfo=UTC)
FICTIONAL_TOKEN = "fictional-connector-token-710"
SECRET_REF = "fictional-connector-token"


def initialize_connector_context(root: Path) -> Path:
    initialize_context(root, name="Fictional Connector Lab", context_id="connector-test")
    SQLiteProjection(root).rebuild()
    return root


def manifest_payload(
    *,
    name: str = "fictional-service",
    base_url: str = "https://api.example.test/v1/",
    snapshots: list[dict[str, Any]] | None = None,
    secret_ref: str | None = None,
    auth_style: str | None = None,
    timeout_seconds: int | float = 30,
    max_bytes: int = 10 * 1024 * 1024,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "name": name,
        "base_url": base_url,
        "snapshots": snapshots
        or [
            {
                "id": "current-items",
                "path": "/snapshots/current",
            }
        ],
        "timeout_seconds": timeout_seconds,
        "max_bytes": max_bytes,
    }
    if secret_ref is not None:
        payload["secret_ref"] = secret_ref
    if auth_style is not None:
        payload["auth_style"] = auth_style
    return payload


def write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    directory = root / "07_connectors"
    directory.mkdir(exist_ok=True)
    path = directory / f"{payload['name']}.yaml"
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    return path


def artifact_records(root: Path) -> tuple[ArtifactRecord, ...]:
    return IngestionService(root).list_inbox().artifacts


def read_artifact_bytes(root: Path, record: ArtifactRecord) -> bytes:
    return root.joinpath(*record.manifest.preserved_path.split("/")).read_bytes()


def read_provenance(root: Path, record: ArtifactRecord) -> dict[str, Any]:
    sidecar_path = record.manifest.sidecars[0]
    payload = json.loads(root.joinpath(*sidecar_path.split("/")).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Expected a provenance mapping")
    return payload


def workspace_file_bytes(root: Path) -> tuple[bytes, ...]:
    return tuple(path.read_bytes() for path in root.rglob("*") if path.is_file())
