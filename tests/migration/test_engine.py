from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from workctx.adapters.filesystem import load_markdown_model
from workctx.cli import app
from workctx.domain import Claim, EntityFrontmatter, Observation, Task
from workctx.migration import MigrationBlockedError, MigrationError, migrate_legacy
from workctx.migration.inventory import fingerprint_source_tree
from workctx.migration.models import FileClassification, MappingAction, StageStatus
from workctx.validation import validate_workspace

MIGRATION_TIME = datetime(2026, 8, 2, 23, 30, tzinfo=UTC)
FAKE_SECRET_VALUE = b"fictional-example-value-not-a-credential"
runner = CliRunner()


def _write_legacy_document(
    root: Path,
    relative_path: str,
    frontmatter: dict[str, Any],
    body: str,
) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    path.write_text(
        f"---\n{rendered}---\n\n{body.rstrip()}\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def test_dry_run_executes_detection_and_preview_without_writes(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "migrated-context"
    source_before = fingerprint_source_tree(legacy_source)

    report = migrate_legacy(
        legacy_source,
        target,
        clock=lambda: MIGRATION_TIME,
    )

    assert not target.exists()
    assert report.mode == "dry_run"
    assert not report.applied
    assert report.blocked
    assert report.source_integrity.before == source_before
    assert report.source_integrity.after == source_before
    assert report.source_integrity.unchanged
    assert [stage.status for stage in report.stages[:3]] == [StageStatus.COMPLETED] * 3
    assert all(stage.status is StageStatus.NOT_RUN for stage in report.stages[3:])
    assert {
        "MIG-ABSOLUTE-PATH",
        "MIG-BROKEN-LINK",
        "MIG-DUPLICATE-ID",
        "MIG-POSSIBLE-SECRET",
        "MIG-UNKNOWN-ENTITY-TYPE",
    }.issubset({finding.code for finding in report.findings})
    assert {item.classification for item in report.inventory} == {
        FileClassification.CANONICAL,
        FileClassification.GENERATED,
        FileClassification.OBSOLETE,
        FileClassification.UNKNOWN,
    }
    decision_mappings = [
        item
        for item in report.mappings
        if item.source_id == "DEC-2026-007" and item.action is MappingAction.MIGRATE
    ]
    assert len(decision_mappings) == 2
    assert len({item.target_id for item in decision_mappings}) == 2
    assert FAKE_SECRET_VALUE not in report.model_dump_json(by_alias=True).encode()


def test_apply_refuses_blocking_findings_without_creating_target(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "migrated-context"
    source_before = fingerprint_source_tree(legacy_source)

    with pytest.raises(MigrationBlockedError) as caught:
        migrate_legacy(
            legacy_source,
            target,
            apply_changes=True,
            clock=lambda: MIGRATION_TIME,
        )

    assert not target.exists()
    assert caught.value.report.blocked
    assert not caught.value.report.applied
    assert caught.value.report.source_integrity.unchanged
    assert fingerprint_source_tree(legacy_source) == source_before
    assert FAKE_SECRET_VALUE not in caught.value.report.model_dump_json(by_alias=True).encode()


@pytest.mark.integration
def test_apply_builds_valid_context_and_complete_reports(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "migrated-context"
    source_before = fingerprint_source_tree(legacy_source)

    report = migrate_legacy(
        legacy_source,
        target,
        apply_changes=True,
        allow_findings=True,
        clock=lambda: MIGRATION_TIME,
    )

    assert report.applied
    assert not report.blocked
    assert all(stage.status is StageStatus.COMPLETED for stage in report.stages)
    assert fingerprint_source_tree(legacy_source) == source_before
    assert validate_workspace(target).ok
    assert report.validation is not None and report.validation.ok
    assert report.projection is not None and report.projection.documents_skipped == 0
    assert report.views is not None and len(report.views.paths) == 5
    assert report.ledger is not None
    assert report.ledger.interaction == "single_import"
    assert report.ledger.import_events == 1
    assert report.ledger.artifact_registration_events > 0
    assert report.ledger.total_events == (
        report.ledger.artifact_registration_events + report.ledger.import_events
    )

    report_json_path = target / "99_meta" / "migration" / "report.json"
    report_markdown_path = target / "99_meta" / "migration" / "report.md"
    stored = json.loads(report_json_path.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 1
    assert stored["report_paths"] == {
        "json": "99_meta/migration/report.json",
        "markdown": "99_meta/migration/report.md",
    }
    assert len(stored["stages"]) == 13
    assert stored["source_integrity"]["unchanged"] is True
    assert "## Audit-ledger decision request" in report_markdown_path.read_text(encoding="utf-8")

    migrated_bytes = b"\n".join(path.read_bytes() for path in target.rglob("*") if path.is_file())
    assert FAKE_SECRET_VALUE not in migrated_bytes
    assert not any(
        item.source_path == "projects/private-plan.md"
        and item.action is MappingAction.PRESERVE_ARTIFACT
        for item in report.mappings
    )
    assert any(
        item.path == "unknown/star-map.md" and item.reason == "unknown_or_unsupported_file"
        for item in report.skipped_files
    )
    raw_mapping = next(
        item
        for item in report.mappings
        if item.source_path == "evidence/raw/session-note.txt"
        and item.action is MappingAction.PRESERVE_ARTIFACT
    )
    assert raw_mapping.target_path is not None
    assert (target / raw_mapping.target_path).read_bytes() == (
        legacy_source / "evidence" / "raw" / "session-note.txt"
    ).read_bytes()


def test_apply_preserves_hierarchy_marks_missing_raw_and_handles_broken_links(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "migrated-context"
    report = migrate_legacy(
        legacy_source,
        target,
        apply_changes=True,
        allow_findings=True,
        clock=lambda: MIGRATION_TIME,
    )

    parent_path = target / "03_work" / "tasks" / "TASK-2026-101.md"
    subtask_path = target / "03_work" / "tasks" / "TASK-2026-101-ST01.md"
    parent = load_markdown_model(parent_path.read_bytes(), Task).frontmatter
    subtask = load_markdown_model(subtask_path.read_bytes(), Task).frontmatter
    assert parent.task_type == "parent"
    assert parent.root_task == parent.id
    assert subtask.task_type == "subtask"
    assert subtask.parent_task == parent.id
    assert subtask.root_task == parent.id
    assert not parent.dependencies
    assert "unavailable://legacy/" in parent_path.read_text(encoding="utf-8")

    evidence_path = target / "02_knowledge" / "evidence" / "EVD-20260703-orion-summary-01.md"
    evidence = load_markdown_model(evidence_path.read_bytes(), EntityFrontmatter).frontmatter
    assert evidence.model_extra is not None
    assert evidence.model_extra["raw_unavailable"] is True
    assert evidence.model_extra["provenance_quality"] == "derived_only"
    assert any(item.code == "MIG-TASK-RELATION-UNAVAILABLE" for item in report.precision_losses)
    dependency_claims = []
    for claim_path in (target / "02_knowledge" / "claims").glob("*.md"):
        claim = load_markdown_model(claim_path.read_bytes(), Claim).frontmatter
        if claim.subject.endswith("/TASK-2026-101") and claim.predicate == "dependencies":
            dependency_claims.append(claim)
    assert len(dependency_claims) == 1
    assert dependency_claims[0].object == ["unavailable://legacy/reference-d66a87acb2b330d0"]
    project_text = (target / "02_knowledge" / "projects" / "PRJ-orion-kite.md").read_text(
        encoding="utf-8"
    )
    assert "unavailable://legacy/" in project_text
    assert "C:\\Fictional" not in project_text


def test_non_empty_target_is_refused_without_source_mutation(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "occupied"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("Fictional existing content.\n", encoding="utf-8")
    before = fingerprint_source_tree(legacy_source)

    with pytest.raises(MigrationError, match="not empty"):
        migrate_legacy(
            legacy_source,
            target,
            apply_changes=True,
            allow_findings=True,
            clock=lambda: MIGRATION_TIME,
        )

    assert marker.read_text(encoding="utf-8") == "Fictional existing content.\n"
    assert fingerprint_source_tree(legacy_source) == before


def test_cli_defaults_to_json_dry_run_and_does_not_create_target(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "cli-preview"

    result = runner.invoke(
        app,
        ["migrate", "legacy", str(legacy_source), str(target), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["command"] == "migrate.legacy"
    assert payload["result"]["mode"] == "dry_run"
    assert payload["result"]["applied"] is False
    assert payload["result"]["report"]["blocked"] is True
    assert payload["warnings"][0]["code"] == "MIGRATION_APPLY_BLOCKED"
    assert result.stderr == ""
    assert not target.exists()
    assert FAKE_SECRET_VALUE not in result.stdout_bytes


def test_cli_explicit_dry_run_overrides_apply(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "cli-explicit-preview"

    result = runner.invoke(
        app,
        [
            "migrate",
            "legacy",
            str(legacy_source),
            str(target),
            "--apply",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["result"]["mode"] == "dry_run"
    assert payload["result"]["applied"] is False
    assert {warning["code"] for warning in payload["warnings"]} == {
        "MIGRATION_APPLY_BLOCKED",
        "MIGRATION_DRY_RUN_OVERRIDES_APPLY",
    }
    assert not target.exists()


def test_cli_blocked_apply_returns_report_envelope_without_secret(
    legacy_source: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "cli-blocked"

    result = runner.invoke(
        app,
        [
            "migrate",
            "legacy",
            str(legacy_source),
            str(target),
            "--apply",
            "--json",
        ],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "migrate.legacy"
    assert payload["result"]["mode"] == "apply"
    assert payload["result"]["applied"] is False
    assert payload["errors"][0]["code"] == "MIGRATION_FINDINGS_BLOCK_APPLY"
    assert result.stderr.startswith("Error:")
    assert not target.exists()
    assert FAKE_SECRET_VALUE not in result.stdout_bytes


@pytest.mark.integration
def test_apply_normalizes_claim_observation_and_lossy_legacy_variants(
    tmp_path: Path,
) -> None:
    source = tmp_path / "variant-legacy"
    target = tmp_path / "variant-context"
    raw_path = source / "evidence" / "raw" / "field-note.txt"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text("Fictional field note on line one.\n", encoding="utf-8")
    _write_legacy_document(
        source,
        "projects/subject.md",
        {
            "id": "PRJ-claim-subject",
            "entity_type": "project",
            "title": "Fictional claim subject",
            "status": "active",
            "confidence": "high",
            "references": [
                {
                    "target": "PRJ-claim-subject",
                    "relation": "mystery_relation",
                    "confidence": "high",
                    "note": "Fictional self-reference.",
                },
                {
                    "target": "https://example.test/reference",
                    "relation": "depends_on",
                },
            ],
            "event_date": "2026-07-01",
            "event_at": datetime(2026, 7, 1, 9, 15),
            "odd_metadata": {1: "numeric keys are deliberately unsupported"},
            "created_at": "2026-07-01",
            "updated_at": "2026-07-02T09:00:00Z",
        },
        (
            "# Project\n\n"
            "See [[../evidence/note.md|the evidence note]], "
            "![the raw note](../evidence/raw/field-note.txt), "
            "[an anchor](#project), and [an external source](https://example.test/source).\n\n"
            "Legacy URI: workctx://legacy-space/project/PRJ-claim-subject.\n"
            "Legacy local path: /Users/fictional/operator/project.md."
        ),
    )
    _write_legacy_document(
        source,
        "claims/current.md",
        {
            "id": "CLM-2026-00042",
            "entity_type": "claim",
            "subject": "PRJ-claim-subject",
            "predicate": "readiness",
            "object": {"signals": ["blue", 2, True], 7: "skip this numeric key"},
            "status": "unknown-state",
            "confidence": "unknown-confidence",
            "valid_from": "2026-07-01",
            "valid_to": "not-a-date",
            "supersedes": "CLM-2020-99999",
            "created_at": "2026-07-01T08:00:00Z",
            "updated_at": "2026-07-02T08:00:00Z",
        },
        "# Claim\n\nThe fictional subject has mixed readiness signals.",
    )
    observation_id = "EVD-20260801-weather-note-01#OBS-001"
    _write_legacy_document(
        source,
        "observations/weather.md",
        {
            "id": observation_id,
            "entity_type": "observation",
            "statement": "The fictional ribbon moved during the rehearsal.",
            "kind": "inference",
            "confidence": "low",
        },
        "# Observation\n\nThis statement has a recoverable frontmatter line locator.",
    )
    _write_legacy_document(
        source,
        "evidence/note.md",
        {
            "id": "EVD-20260801-field-note-01",
            "entity_type": "evidence",
            "title": "Fictional field note",
            "status": "active",
            "raw_path": "raw/field-note.txt",
            "created_at": "2026-08-01T10:00:00Z",
            "updated_at": "2026-08-01T10:00:00Z",
            "observations": [
                {
                    "kind": "fact",
                    "statement": "The fictional note contains one line.",
                    "confidence": "high",
                    "source": {
                        "ref": "raw/field-note.txt",
                        "locator": {
                            "type": "line_range",
                            "start_line": 1,
                            "end_line": 1,
                        },
                    },
                    "observed_at": "2026-08-01T10:00:00Z",
                },
                "unstructured observation",
                {"statement": "Missing structured source."},
                {
                    "statement": "Invalid line range.",
                    "source": {
                        "ref": "raw/field-note.txt",
                        "locator": {
                            "type": "line_range",
                            "start_line": 0,
                            "end_line": 0,
                        },
                    },
                    "observed_at": "2026-08-01T10:00:00Z",
                },
            ],
        },
        "# Evidence\n\nFictional synthesized field note.",
    )
    _write_legacy_document(
        source,
        "tasks/legacy-task.md",
        {
            "id": "legacy-task-alpha",
            "entity_type": "task",
            "title": "Review fictional edge cases",
            "status": "mystery-state",
            "priority": "P9",
            "owner": 42,
            "requester": {"name": "Fictional requester"},
            "waiting_on": [None, ""],
            "due_at": "not-a-date",
            "dependencies": ["PRJ-claim-subject"],
            "blockers": ["TASK-2099-998"],
        },
        "# Task\n\nReview the deliberately lossy fictional variants.",
    )

    report = migrate_legacy(
        source,
        target,
        apply_changes=True,
        clock=lambda: MIGRATION_TIME,
    )

    assert report.applied
    assert validate_workspace(target).ok
    claim = load_markdown_model(
        (target / "02_knowledge" / "claims" / "CLM-2026-00042.md").read_bytes(),
        Claim,
    ).frontmatter
    assert claim.subject == "workctx://variant-context/project/PRJ-claim-subject"
    assert claim.status == "uncertain"
    assert claim.valid_from == datetime(2026, 7, 1, tzinfo=UTC)
    assert claim.valid_to is None
    assert claim.object == {"signals": ["blue", 2, True]}
    observation_path = (
        target / "02_knowledge" / "observations" / "EVD-20260801-weather-note-01%23OBS-001.md"
    )
    observation = load_markdown_model(observation_path.read_bytes(), Observation).frontmatter
    assert observation.statement == "The fictional ribbon moved during the rehearsal."
    evidence = load_markdown_model(
        (target / "02_knowledge" / "evidence" / "EVD-20260801-field-note-01.md").read_bytes(),
        EntityFrontmatter,
    ).frontmatter
    assert evidence.model_extra is not None
    assert len(evidence.model_extra["observations"]) == 2
    task = load_markdown_model(
        (target / "03_work" / "tasks" / "TASK-2026-001.md").read_bytes(),
        Task,
    ).frontmatter
    assert task.status == "backlog"
    assert task.priority == "P2"
    assert task.next_action.startswith("Review the migrated task")
    loss_codes = {item.code for item in report.precision_losses}
    assert {
        "MIG-METADATA-KEY-SKIPPED",
        "MIG-OBSERVATION-SKIPPED",
        "MIG-REFERENCE-RELATION-DOWNGRADED",
        "MIG-TASK-NEXT-ACTION-DEFAULTED",
        "MIG-TASK-PRIORITY-DEFAULTED",
        "MIG-TASK-STATUS-DEFAULTED",
        "MIG-TIMESTAMP-DEFAULTED",
        "MIG-TIMESTAMP-OMITTED",
        "MIG-TIMEZONE-ASSUMED",
    }.issubset(loss_codes)
