"""Deterministic JSON and Markdown migration report rendering."""

from __future__ import annotations

from pathlib import Path

from workctx.adapters.filesystem.serialization import dump_json_bytes
from workctx.migration.models import MigrationReport

REPORT_JSON_PATH = "99_meta/migration/report.json"
REPORT_MARKDOWN_PATH = "99_meta/migration/report.md"


def write_migration_reports(context_root: Path, report: MigrationReport) -> None:
    directory = context_root / "99_meta" / "migration"
    directory.mkdir(parents=True, exist_ok=True)
    (context_root / REPORT_JSON_PATH).write_bytes(
        dump_json_bytes(report.model_dump(mode="json", by_alias=True))
    )
    (context_root / REPORT_MARKDOWN_PATH).write_text(
        render_migration_markdown(report),
        encoding="utf-8",
        newline="\n",
    )


def render_migration_markdown(report: MigrationReport) -> str:
    lines = [
        "# Legacy migration report",
        "",
        f"- Mode: `{report.mode.value}`",
        f"- Applied: `{str(report.applied).lower()}`",
        f"- Blocked: `{str(report.blocked).lower()}`",
        f"- Source label: `{_cell(report.source_label)}`",
        f"- Target context: `{_cell(report.target_context_id)}`",
        f"- Source unchanged: `{str(report.source_integrity.unchanged).lower()}`",
        f"- Source fingerprint: `{report.source_integrity.before}`",
        "",
        "## Stage status",
        "",
        "| # | Stage | Status | Detail |",
        "| ---: | --- | --- | --- |",
    ]
    lines.extend(
        f"| {stage.number} | {_cell(stage.name)} | {stage.status.value} | {_cell(stage.detail)} |"
        for stage in report.stages
    )
    lines.extend(
        [
            "",
            "## Inventory summary",
            "",
            f"Inventoried files: {len(report.inventory)}.",
            "",
            "| Legacy path | Classification | Entity type | SHA-256 |",
            "| --- | --- | --- | --- |",
        ]
    )
    lines.extend(
        (
            f"| {_cell(item.path)} | {item.classification.value} | "
            f"{_cell(item.entity_type or '')} | `{item.content_hash}` |"
        )
        for item in report.inventory
    )
    lines.extend(["", "## Findings", ""])
    if report.findings:
        lines.extend(
            [
                "| Severity | Code | Legacy path | Locator | Apply blocker | Message |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            (
                f"| {item.severity.value} | `{item.code}` | {_cell(item.path)} | "
                f"{_cell(item.locator or '')} | {str(item.blocks_apply).lower()} | "
                f"{_cell(item.message)} |"
            )
            for item in report.findings
        )
    else:
        lines.append("No findings.")

    lines.extend(["", "## Migration ledger", ""])
    if report.mappings:
        lines.extend(
            [
                "| Legacy path | Action | Legacy ID | New URI | Target path | Note |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            (
                f"| {_cell(item.source_path)} | {item.action.value} | "
                f"{_cell(item.source_id or '')} | {_cell(item.target_uri or '')} | "
                f"{_cell(item.target_path or '')} | {_cell(item.note or '')} |"
            )
            for item in report.mappings
        )
    else:
        lines.append("No mappings were produced.")

    lines.extend(["", "## Precision loss", ""])
    if report.precision_losses:
        lines.extend(
            [
                "| Code | Legacy path | Description |",
                "| --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| `{item.code}` | {_cell(item.path)} | {_cell(item.message)} |"
            for item in report.precision_losses
        )
    else:
        lines.append("No precision loss was recorded.")

    lines.extend(["", "## Skipped files", ""])
    if report.skipped_files:
        lines.extend(["| Legacy path | Reason |", "| --- | --- |"])
        lines.extend(
            f"| {_cell(item.path)} | {_cell(item.reason)} |" for item in report.skipped_files
        )
    else:
        lines.append("No files were skipped.")

    lines.extend(["", "## Validation and derived state", ""])
    if report.validation is None:
        lines.append("Destination validation did not run.")
    else:
        lines.append(f"Validation clean: `{str(report.validation.ok).lower()}`.")
        for issue in report.validation.issues:
            lines.append(
                f"- `{issue.severity}` `{issue.code}` at `{_cell(issue.path or '')}`: "
                f"{_cell(issue.message)}"
            )
    if report.projection is not None:
        lines.append(
            "Projection: "
            f"{report.projection.documents_indexed} indexed, "
            f"{report.projection.documents_skipped} skipped, "
            f"{report.projection.tasks} tasks, "
            f"{report.projection.claims} claims, "
            f"{report.projection.observations} observations."
        )
    if report.views is not None:
        lines.append(f"Generated views: {', '.join(report.views.paths)}.")

    lines.extend(["", "## Audit-ledger decision request", ""])
    if report.ledger is None:
        lines.extend(
            [
                "Audit interaction did not run during this preview.",
                "Provisional apply interaction: `single_import`.",
            ]
        )
    else:
        lines.extend(
            [
                f"Provisional interaction: `{report.ledger.interaction.value}`.",
                "This remains an open operator decision.",
                f"Artifact registration events: {report.ledger.artifact_registration_events}.",
                f"Canonical import events: {report.ledger.import_events}.",
                f"Total audit events: {report.ledger.total_events}.",
            ]
        )
    lines.extend(
        [
            "",
            "- `single_import` is atomic and compact, but provides coarse per-entity history.",
            "- `per_entity` improves audit and replay granularity, but increases event volume "
            "and needs an all-or-nothing orchestration policy.",
            "- `none` avoids import events, but canonical writes lose normal audit provenance.",
            "- Artifact preservation still uses ingestion events because the existing "
            "ingestion engine is consumed unchanged.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _cell(value: str) -> str:
    return " ".join(value.replace("|", "\\|").splitlines())


__all__ = [
    "REPORT_JSON_PATH",
    "REPORT_MARKDOWN_PATH",
    "render_migration_markdown",
    "write_migration_reports",
]
