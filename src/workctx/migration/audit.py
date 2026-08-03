"""Audit-ledger interaction seam for migration apply."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from workctx.domain.transactions import SystemActor, TransactionProposal
from workctx.migration.errors import MigrationError
from workctx.migration.models import LedgerInteraction
from workctx.migration.normalize import NormalizedMigration
from workctx.transactions import ApplyResult, apply, verify_ledger


class MigrationLedgerWriter(Protocol):
    """Decision seam for how normalized migration documents enter the ledger."""

    interaction: LedgerInteraction

    def apply_import(
        self,
        context_root: Path,
        normalized: NormalizedMigration,
        *,
        migration_time: datetime,
        source_fingerprint: str,
    ) -> ApplyResult: ...


class SingleImportLedgerWriter:
    """Apply all normalized documents as one atomic canonical import event."""

    interaction = LedgerInteraction.SINGLE_IMPORT

    def apply_import(
        self,
        context_root: Path,
        normalized: NormalizedMigration,
        *,
        migration_time: datetime,
        source_fingerprint: str,
    ) -> ApplyResult:
        if not normalized.operations:
            raise MigrationError("Migration apply has no canonical documents to import.")
        timestamp = _utc_time(migration_time)
        fingerprint = source_fingerprint.removeprefix("sha256:")
        proposal = TransactionProposal(
            schema_version=1,
            id=f"TXP-{timestamp:%Y%m%dT%H%M%SZ}-legacy-import-{fingerprint[:12]}",
            context_id=verify_ledger(context_root).context_id,
            base_revision=verify_ledger(context_root).head_hash,
            actor=SystemActor(
                type="system",
                id="workctx-migration",
                agent=None,
                model=None,
            ),
            created_at=timestamp,
            source_refs=list(normalized.source_references),
            operations=list(normalized.operations),
            preconditions=[],
            postconditions=[],
            expected_views=["sqlite"],
            approval="required",
        )
        return apply(
            context_root,
            proposal,
            approved=True,
            session_id=f"migration-{fingerprint[:16]}",
        )


DEFAULT_LEDGER_WRITER: MigrationLedgerWriter = SingleImportLedgerWriter()


def _utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Migration clocks must return timezone-aware datetimes.")
    return value.astimezone(UTC).replace(microsecond=0)


__all__ = [
    "DEFAULT_LEDGER_WRITER",
    "MigrationLedgerWriter",
    "SingleImportLedgerWriter",
]
