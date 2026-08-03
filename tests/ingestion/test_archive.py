from __future__ import annotations

from pathlib import Path

import pytest

from workctx.adapters.filesystem import ReplaceRetryPolicy, StagedReplacement, inspect_recovery
from workctx.ingestion import (
    ArchiveDisposition,
    ArtifactReceiptError,
    IngestionRecoveryPendingError,
    IngestionService,
    RegisterRequest,
)
from workctx.transactions import (
    ReceiptAuthenticationError,
    authenticate_apply_result,
    verify_ledger,
)

from .support import FIXED_NOW, initialize_ingestion_context, processing_receipt, write_raw


def _register(root: Path, *, sidecars: tuple[str, ...] = ()):
    service = IngestionService(root, clock=lambda: FIXED_NOW)
    return service.register(
        RegisterRequest(
            path="00_inbox/raw/report.txt",
            source_type="document",
            sidecars=sidecars,
        )
    )


@pytest.mark.acceptance
def test_e2e_register_transaction_archive_preserves_artifact_reference(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    content = b"Fictional evidence for the archive scenario.\n"
    source = write_raw(root, "00_inbox/raw/report.txt", content)
    registration = _register(root)
    processing = processing_receipt(root, registration.artifact.reference)

    archived = IngestionService(root, clock=lambda: FIXED_NOW).archive_after(
        registration.artifact.manifest.id,
        processing,
        session_id="archive-e2e",
    )

    destination = root / archived.destination_path
    assert archived.disposition is ArchiveDisposition.ARCHIVED
    assert archived.artifact.reference == registration.artifact.reference
    assert archived.artifact.manifest.status == "processed"
    assert archived.artifact.manifest.preserved_path.startswith("01_processed/")
    assert not source.exists()
    assert destination.read_bytes() == content
    assert inspect_recovery(root).state == "clean"

    assert archived.manifest_receipt is not None
    manifest_event = authenticate_apply_result(root, archived.manifest_receipt)
    assert len(manifest_event.operations) == 1
    operation = manifest_event.operations[0]
    assert operation.op == "update"
    assert operation.target == registration.artifact.manifest_path
    assert all("00_inbox/raw" not in item.model_dump_json() for item in manifest_event.operations)

    head = verify_ledger(root).head_hash
    repeated = IngestionService(root, clock=lambda: FIXED_NOW).archive_after(
        registration.artifact.manifest.id,
        processing,
        session_id="archive-repeat",
    )
    assert repeated.disposition is ArchiveDisposition.ALREADY_ARCHIVED
    assert verify_ledger(root).head_hash == head


def test_archive_refuses_forged_or_nonreferencing_receipts_without_moving(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    source = write_raw(root, "00_inbox/raw/report.txt", b"Fictional receipt guard.\n")
    registration = _register(root)
    unrelated = processing_receipt(root, None, slug="unrelated-proof")
    service = IngestionService(root, clock=lambda: FIXED_NOW)

    with pytest.raises(ArtifactReceiptError):
        service.archive_after(registration.artifact.manifest.id, unrelated)

    forged = unrelated.model_copy(update={"ledger_event_hash": "0" * 64})
    with pytest.raises(ReceiptAuthenticationError):
        service.archive_after(registration.artifact.manifest.id, forged)

    current = service.list_inbox().artifacts[0].manifest
    assert current.status == "pending"
    assert current.preserved_path == "00_inbox/raw/report.txt"
    assert source.is_file()
    assert not tuple((root / "01_processed").glob("ART-*"))


def test_archive_moves_registered_sidecars_with_the_original(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    source = write_raw(root, "00_inbox/raw/report.txt", b"Fictional screenshot evidence.\n")
    sidecar = write_raw(root, "00_inbox/raw/report.md", b"Fictional screenshot note.\n")
    registration = _register(root, sidecars=("00_inbox/raw/report.md",))
    processing = processing_receipt(root, registration.artifact.reference)

    archived = IngestionService(root, clock=lambda: FIXED_NOW).archive_after(
        registration.artifact.manifest.id,
        processing,
    )

    assert not source.exists()
    assert not sidecar.exists()
    assert (root / archived.artifact.manifest.preserved_path).is_file()
    assert len(archived.artifact.manifest.sidecars) == 1
    assert (root / archived.artifact.manifest.sidecars[0]).is_file()


def test_interrupted_archive_move_recovers_with_the_same_authenticated_receipt(
    tmp_path: Path,
) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    write_raw(root, "00_inbox/raw/report.txt", b"Fictional recoverable evidence.\n")
    registration = _register(root)
    processing = processing_receipt(root, registration.artifact.reference)

    def blocked_replace(_source: Path, _destination: Path) -> None:
        raise PermissionError("fictional sharing violation")

    failing = IngestionService(
        root,
        clock=lambda: FIXED_NOW,
        stager_factory=lambda context_root: StagedReplacement(
            context_root,
            retry_policy=ReplaceRetryPolicy(max_attempts=1, initial_delay_seconds=0),
            replace_function=blocked_replace,
        ),
    )
    with pytest.raises(IngestionRecoveryPendingError):
        failing.archive_after(
            registration.artifact.manifest.id,
            processing,
            session_id="archive-failure",
        )

    assert inspect_recovery(root).state == "prepared"
    recovered = IngestionService(root, clock=lambda: FIXED_NOW).archive_after(
        registration.artifact.manifest.id,
        processing,
        session_id="archive-recovery",
    )
    assert recovered.disposition is ArchiveDisposition.RECOVERED
    assert inspect_recovery(root).state == "clean"
    assert not (root / "00_inbox/raw/report.txt").exists()
    assert (root / recovered.destination_path).is_file()


def test_manifest_transaction_failure_never_starts_the_archive_move(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    source = write_raw(root, "00_inbox/raw/report.txt", b"Fictional transaction failure.\n")
    registration = _register(root)
    processing = processing_receipt(root, registration.artifact.reference)

    def fail_transaction(*_args: object, **_kwargs: object):
        raise RuntimeError("fictional transaction failure")

    service = IngestionService(
        root,
        clock=lambda: FIXED_NOW,
        transaction_apply=fail_transaction,
    )
    with pytest.raises(RuntimeError, match="fictional transaction failure"):
        service.archive_after(registration.artifact.manifest.id, processing)

    current = IngestionService(root).list_inbox().artifacts[0].manifest
    assert current.status == "pending"
    assert source.is_file()
    assert not tuple((root / "01_processed").glob("ART-*"))
