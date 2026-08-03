from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from workctx.adapters.filesystem import CanonicalStore
from workctx.ingestion import (
    DuplicateArtifactError,
    DuplicatePolicy,
    IngestionService,
    RegisterRequest,
    RegistrationDisposition,
)
from workctx.transactions import verify_ledger

from .support import FIXED_NOW, initialize_ingestion_context, write_raw


def request(
    path: str, *, duplicate_policy: DuplicatePolicy = DuplicatePolicy.REFUSE
) -> RegisterRequest:
    return RegisterRequest(
        path=path,
        source_type="note",
        source_origin="fictional://local-drop",
        event_at="2026-08-02T08:00:00-04:00",
        language="en",
        classification="internal",
        duplicate_policy=duplicate_policy,
    )


def test_registration_writes_schema_valid_manifest_and_is_idempotent(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    content = b"Fictional planning note.\n"
    write_raw(root, "00_inbox/raw/planning-note.txt", content)
    service = IngestionService(root, clock=lambda: FIXED_NOW)

    first = service.register(request("00_inbox/raw/planning-note.txt"))
    head_after_first = verify_ledger(root).head_hash
    second = service.register(request("00_inbox/raw/planning-note.txt"))

    expected_hash = f"sha256:{hashlib.sha256(content).hexdigest()}"
    assert first.disposition is RegistrationDisposition.REGISTERED
    assert first.artifact.manifest.id == "ART-20260802-planning-note-01"
    assert first.artifact.manifest.content_hash == expected_hash
    assert first.artifact.manifest.media_type == "text/plain"
    assert first.artifact.manifest.status == "pending"
    assert first.artifact.reference == f"artifact://sha256/{expected_hash.removeprefix('sha256:')}"
    assert second.disposition is RegistrationDisposition.ALREADY_REGISTERED
    assert second.artifact == first.artifact
    assert verify_ledger(root).head_hash == head_after_first

    stored = CanonicalStore(root).read_artifact_manifest(first.artifact.manifest_path)
    assert stored == first.artifact.manifest
    schema = json.loads(
        (Path("schemas") / "artifact-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(stored.model_dump(mode="json"))
    listing = service.list_inbox()
    assert listing.count == 1
    assert listing.artifacts == (first.artifact,)


def test_duplicate_policy_refuses_or_links_same_bytes(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    content = b"Fictional duplicate note.\n"
    write_raw(root, "00_inbox/raw/first.txt", content)
    write_raw(root, "00_inbox/raw/second.txt", content)
    service = IngestionService(root, clock=lambda: FIXED_NOW)
    first = service.register(request("00_inbox/raw/first.txt"))

    with pytest.raises(DuplicateArtifactError) as refused:
        service.register(request("00_inbox/raw/second.txt"))

    assert refused.value.duplicate_of == first.artifact.manifest.id
    assert service.list_inbox().count == 1
    linked = service.register(
        request("00_inbox/raw/second.txt", duplicate_policy=DuplicatePolicy.LINK)
    )
    assert linked.disposition is RegistrationDisposition.DUPLICATE_LINKED
    assert linked.artifact.manifest.status == "duplicate"
    assert linked.artifact.manifest.duplicate_of == first.artifact.manifest.id
    assert linked.artifact.manifest.preserved_path == "00_inbox/raw/second.txt"


def test_same_name_with_different_content_allocates_distinct_manifest(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    write_raw(root, "00_inbox/raw/first/note.txt", b"First fictional note.\n")
    write_raw(root, "00_inbox/raw/second/note.txt", b"Second fictional note.\n")
    service = IngestionService(root, clock=lambda: FIXED_NOW)

    first = service.register(request("00_inbox/raw/first/note.txt"))
    second = service.register(request("00_inbox/raw/second/note.txt"))

    assert first.artifact.manifest.id == "ART-20260802-note-01"
    assert second.artifact.manifest.id == "ART-20260802-note-02"
    assert first.artifact.manifest.content_hash != second.artifact.manifest.content_hash
