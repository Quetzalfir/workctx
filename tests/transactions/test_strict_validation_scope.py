from __future__ import annotations

import json
from pathlib import Path

import pytest

from workctx.adapters.filesystem import render_markdown_bytes
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain.entities import EntityFrontmatter
from workctx.transactions import PostconditionRollbackError, TransactionEngine
from workctx.validation import validate_workspace

from .support import (
    content_hash,
    create_operation,
    entity_document,
    initialize_transaction_context,
    proposal,
)


def _write_path_escape_manifest(root: Path) -> None:
    artifact_id = "ART-20260801-baseline-path-01"
    payload = {
        "schema_version": 1,
        "id": artifact_id,
        "content_hash": f"sha256:{'a' * 64}",
        "original_name": "baseline.txt",
        "media_type": "text/plain",
        "source_type": "note",
        "event_at_inferred": False,
        "ingested_at": "2026-08-01T12:00:00Z",
        "participants": [],
        "status": "pending",
        "preserved_path": "../outside.txt",
        "sidecars": [],
    }
    (root / "00_inbox" / "manifests" / f"{artifact_id}.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def test_unrelated_legacy_strict_violation_does_not_poison_apply(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    legacy = root / "90_integrations" / "legacy-notes.md"
    legacy.write_text("C:\\legacy\\notes.txt\n", encoding="utf-8")
    transaction = proposal("legacy-unrelated", [create_operation("PRJ-clean")])

    receipt = TransactionEngine(root).apply(transaction, approved=True)

    assert receipt.committed is True
    assert (root / "02_knowledge" / "PRJ-clean.md").is_file()
    assert legacy.read_text(encoding="utf-8") == "C:\\legacy\\notes.txt\n"


def test_preexisting_untouched_errors_are_nonfatal_notices_for_preview_and_apply(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    (root / "90_integrations" / "legacy-secret.txt").write_text(
        "API_KEY=FICTIONAL_BASELINE_SECRET_123456\n",
        encoding="utf-8",
    )
    _write_path_escape_manifest(root)
    transaction = proposal("baseline-errors", [create_operation("PRJ-clean")])

    baseline = validate_workspace(root)
    assert {issue.code for issue in baseline.errors} == {
        "CTX-PATH-ESCAPE",
        "CTX-POSSIBLE-SECRET",
    }

    preview = TransactionEngine(root).dry_run(transaction)

    assert preview.valid is True
    preview_notice = next(
        diagnostic
        for diagnostic in preview.diagnostics
        if diagnostic.code == "TXN-PREEXISTING-WORKSPACE-ERRORS"
    )
    assert preview_notice.severity == "advisory"
    assert preview_notice.message.startswith("2 pre-existing")
    assert "workctx context validate" in (preview_notice.repair_action or "")

    receipt = TransactionEngine(root).apply(transaction, approved=True)

    assert receipt.committed is True
    assert receipt.diagnostics == (preview_notice,)
    assert {issue.code for issue in validate_workspace(root).errors} == {
        "CTX-PATH-ESCAPE",
        "CTX-POSSIBLE-SECRET",
    }


def test_transaction_written_strict_violation_still_rolls_back(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal(
        "touched-violation",
        [
            create_operation(
                "PRJ-violating",
                body="C:\\transaction\\written.txt\n",
            )
        ],
    )

    preview = TransactionEngine(root).dry_run(transaction)

    assert preview.valid is False
    preview_issue = next(
        diagnostic for diagnostic in preview.diagnostics if diagnostic.code == "CTX-ABSOLUTE-PATH"
    )
    assert preview_issue.severity == "error"
    assert "line" in preview_issue.message

    with pytest.raises(PostconditionRollbackError) as captured:
        TransactionEngine(root).apply(transaction, approved=True)

    diagnostics = [
        diagnostic
        for diagnostic in captured.value.result.diagnostics
        if diagnostic.code == "CTX-ABSOLUTE-PATH"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].severity == "error"
    assert diagnostics[0].path == "02_knowledge/PRJ-violating.md"
    assert not (root / "02_knowledge" / "PRJ-violating.md").exists()


def test_new_error_in_untouched_reference_owner_blocks_preview_and_rolls_back(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "context", build_projection=False)
    digest = "b" * 64
    artifact_id = "ART-20260801-reference-source-01"
    manifest = {
        "schema_version": 1,
        "id": artifact_id,
        "content_hash": f"sha256:{digest}",
        "original_name": "reference-source.txt",
        "media_type": "text/plain",
        "source_type": "note",
        "event_at_inferred": False,
        "ingested_at": "2026-08-01T12:00:00Z",
        "participants": [],
        "status": "pending",
        "preserved_path": "00_inbox/reference-source.txt",
        "sidecars": [],
    }
    (root / "00_inbox" / "manifests" / f"{artifact_id}.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    evidence_id = "EVD-20260801-reference-source-01"
    observation_id = f"{evidence_id}#OBS-001"
    observation_uri = (
        "workctx://transaction-lab/observation/EVD-20260801-reference-source-01%23OBS-001"
    )
    evidence_payload = entity_document(evidence_id, "evidence")
    evidence_payload["document"].update(
        {
            "artifact_ref": f"artifact://sha256/{digest}",
            "observations": [
                {
                    "id": observation_id,
                    "kind": "fact",
                    "statement": "The fictional source supports the project.",
                    "confidence": "high",
                    "source": {
                        "ref": f"artifact://sha256/{digest}",
                        "locator": {"type": "line_range", "start_line": 1, "end_line": 1},
                    },
                    "derived_from": [],
                    "related": [],
                }
            ],
        }
    )
    evidence_path = root / "02_knowledge" / f"{evidence_id}.md"
    evidence_path.write_bytes(
        render_markdown_bytes(
            EntityFrontmatter.model_validate(evidence_payload["document"]),
            evidence_payload["body"],
        )
    )
    project_payload = entity_document(
        "PRJ-reference-owner",
        references=[{"relation": "evidenced_by", "target": observation_uri}],
    )
    project_path = root / "02_knowledge" / "PRJ-reference-owner.md"
    project_path.write_bytes(
        render_markdown_bytes(
            EntityFrontmatter.model_validate(project_payload["document"]),
            project_payload["body"],
        )
    )
    SQLiteProjection(root).rebuild()
    assert validate_workspace(root).errors == []

    updated_evidence = entity_document(evidence_id, "evidence")
    updated_evidence["document"]["artifact_ref"] = f"artifact://sha256/{digest}"
    transaction = proposal(
        "break-untouched-reference",
        [
            {
                "op": "update",
                "target": f"02_knowledge/{evidence_id}.md",
                "payload": updated_evidence,
                "expected_hash": content_hash(evidence_path.read_bytes()),
            }
        ],
    )
    original = evidence_path.read_bytes()

    preview = TransactionEngine(root).dry_run(transaction)

    assert preview.valid is False
    issue = next(
        diagnostic for diagnostic in preview.diagnostics if diagnostic.code == "REF-UNRESOLVED"
    )
    assert issue.path == "02_knowledge/PRJ-reference-owner.md"

    with pytest.raises(PostconditionRollbackError) as captured:
        TransactionEngine(root).apply(transaction, approved=True)

    assert any(
        diagnostic.code == "REF-UNRESOLVED"
        and diagnostic.path == "02_knowledge/PRJ-reference-owner.md"
        for diagnostic in captured.value.result.diagnostics
    )
    assert evidence_path.read_bytes() == original
