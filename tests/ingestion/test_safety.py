from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from workctx.errors import ContextBoundaryError
from workctx.ingestion import IngestionService, RegisterRequest

from .support import FIXED_NOW, initialize_ingestion_context, write_raw


@pytest.mark.parametrize(
    "path",
    [
        "01_processed/note.txt",
        "00_inbox/raw/../quarantine/note.txt",
        "00_inbox\\raw\\note.txt",
        "C:/fictional/note.txt",
    ],
)
def test_registration_request_rejects_non_raw_or_nonportable_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(path=path, source_type="note")


def test_registration_rejects_a_link_instead_of_following_it(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    target = tmp_path / "outside.txt"
    target.write_bytes(b"Fictional link target.\n")
    link = root / "00_inbox" / "raw" / "link.txt"
    try:
        os.symlink(target, link)
    except (NotImplementedError, OSError):
        pytest.skip("File symlink creation is unavailable on this host")

    service = IngestionService(root, clock=lambda: FIXED_NOW)
    with pytest.raises(ContextBoundaryError):
        service.register(RegisterRequest(path="00_inbox/raw/link.txt", source_type="note"))


def test_registration_transaction_failure_leaves_raw_and_manifest_absent(tmp_path: Path) -> None:
    root = initialize_ingestion_context(tmp_path / "context")
    source = write_raw(root, "00_inbox/raw/note.txt", b"Fictional failed registration.\n")

    def fail_transaction(*_args: object, **_kwargs: object):
        raise RuntimeError("fictional transaction failure")

    service = IngestionService(
        root,
        clock=lambda: FIXED_NOW,
        transaction_apply=fail_transaction,
    )
    with pytest.raises(RuntimeError, match="fictional transaction failure"):
        service.register(RegisterRequest(path="00_inbox/raw/note.txt", source_type="note"))

    assert source.is_file()
    assert not tuple((root / "00_inbox" / "manifests").glob("ART-*"))
    assert not tuple((root / "00_inbox" / "quarantine").glob("ART-*"))
