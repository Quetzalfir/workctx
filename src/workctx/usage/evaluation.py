"""Deterministic folding and promotion/decay evaluation for usage telemetry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from workctx.domain import Claim, ClaimStatus, Task, TaskStatus, WorkctxUri
from workctx.domain.frontmatter import parse_frontmatter
from workctx.domain.transactions import AuditMoveOperation, AuditOperation
from workctx.services.contexts import load_context_config
from workctx.transactions import read_audit_events
from workctx.usage.models import (
    DecayCandidate,
    PromotionCandidate,
    UsageCandidate,
    UsageEvent,
    UsageStatus,
    UsageSummary,
    UsageTargetSummary,
    UsageWindowTotals,
)
from workctx.usage.recording import USAGE_RELATIVE_PATH, is_enabled


@dataclass(slots=True)
class _TargetAccumulator:
    uses_7d: int = 0
    uses_30d: int = 0
    uses_90d: int = 0
    last_used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _DecayRecord:
    target_uri: str
    entity_type: Literal["task", "claim"]
    source_path: str
    canonical_activity_at: datetime


def summarize(root: Path, *, now: datetime | None = None) -> UsageSummary:
    """Fold retained usage events into inclusive rolling 7/30/90-day windows."""

    current = _normalize_time(datetime.now(UTC) if now is None else now)
    cutoffs = {
        7: current - timedelta(days=7),
        30: current - timedelta(days=30),
        90: current - timedelta(days=90),
    }
    targets: dict[str, _TargetAccumulator] = {}
    event_count = 0
    uri_event_count = 0
    query_event_count = 0
    corrupt_line_count = 0

    for path in _usage_files(root):
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError:
            corrupt_line_count += 1
            continue
        with stream:
            try:
                lines = stream
                for line in lines:
                    if not line.strip():
                        corrupt_line_count += 1
                        continue
                    try:
                        raw: Any = json.loads(line)
                        event = UsageEvent.model_validate(raw)
                    except (json.JSONDecodeError, UnicodeError, ValidationError, ValueError):
                        corrupt_line_count += 1
                        continue
                    timestamp = event.timestamp.astimezone(UTC)
                    if timestamp > current:
                        continue
                    event_count += 1
                    if event.target_uri is None:
                        query_event_count += 1
                        continue
                    uri_event_count += 1
                    accumulator = targets.setdefault(event.target_uri, _TargetAccumulator())
                    if accumulator.last_used_at is None or timestamp > accumulator.last_used_at:
                        accumulator.last_used_at = timestamp
                    if timestamp >= cutoffs[90]:
                        accumulator.uses_90d += 1
                    if timestamp >= cutoffs[30]:
                        accumulator.uses_30d += 1
                    if timestamp >= cutoffs[7]:
                        accumulator.uses_7d += 1
            except (OSError, UnicodeError):
                corrupt_line_count += 1

    summaries = tuple(
        UsageTargetSummary(
            target_uri=target_uri,
            uses_7d=accumulator.uses_7d,
            uses_30d=accumulator.uses_30d,
            uses_90d=accumulator.uses_90d,
            last_used_at=accumulator.last_used_at,
        )
        for target_uri, accumulator in sorted(targets.items())
        if accumulator.last_used_at is not None
    )
    return UsageSummary(
        generated_at=current,
        event_count=event_count,
        uri_event_count=uri_event_count,
        query_event_count=query_event_count,
        corrupt_line_count=corrupt_line_count,
        totals=UsageWindowTotals(
            uses_7d=sum(item.uses_7d for item in summaries),
            uses_30d=sum(item.uses_30d for item in summaries),
            uses_90d=sum(item.uses_90d for item in summaries),
        ),
        targets=summaries,
    )


def usage_status(root: Path, *, now: datetime | None = None) -> UsageStatus:
    """Return opt-in state, retained byte counts, and the current window fold."""

    files = _usage_files(root)
    current_path = root.expanduser().resolve(strict=True) / USAGE_RELATIVE_PATH
    current_size = _safe_size(current_path)
    rotated = tuple(path for path in files if path != current_path)
    return UsageStatus(
        enabled=is_enabled(root),
        file_size_bytes=current_size,
        rotated_file_count=len(rotated),
        rotated_size_bytes=sum(_safe_size(path) for path in rotated),
        summary=summarize(root, now=now),
    )


def evaluate_usage(root: Path, *, now: datetime | None = None) -> tuple[UsageCandidate, ...]:
    """Return deterministic advisory candidates without mutating canonical state."""

    current = _normalize_time(datetime.now(UTC) if now is None else now)
    config = load_context_config(root)
    if not config.telemetry.usage:
        return ()
    summary = summarize(root, now=current)

    promotions: list[PromotionCandidate] = []
    for target in summary.targets:
        if target.target_uri.startswith("workctx://"):
            continue
        if target.uses_30d < config.telemetry.promotion_uses:
            continue
        promotions.append(
            PromotionCandidate(
                target_uri=target.target_uri,
                uses_7d=target.uses_7d,
                uses_30d=target.uses_30d,
                uses_90d=target.uses_90d,
                threshold_uses=config.telemetry.promotion_uses,
                last_used_at=target.last_used_at,
            )
        )

    usage_activity = {item.target_uri: item.last_used_at for item in summary.targets}
    records = _decay_records(root, context_id=config.id)
    ledger_activity = _ledger_activity(root, records, current)
    cutoff = current - timedelta(days=config.telemetry.decay_days)
    decays: list[DecayCandidate] = []
    for record in records:
        timestamps = [record.canonical_activity_at]
        used_at = usage_activity.get(record.target_uri)
        if used_at is not None:
            timestamps.append(used_at)
        touched_at = ledger_activity.get(record.target_uri)
        if touched_at is not None:
            timestamps.append(touched_at)
        last_activity = max(timestamp.astimezone(UTC) for timestamp in timestamps)
        if last_activity > cutoff:
            continue
        decays.append(
            DecayCandidate(
                target_uri=record.target_uri,
                entity_type=record.entity_type,
                last_activity_at=last_activity,
                inactive_days=(current - last_activity).days,
                threshold_days=config.telemetry.decay_days,
            )
        )

    return tuple(
        [
            *sorted(promotions, key=lambda candidate: candidate.target_uri),
            *sorted(decays, key=lambda candidate: candidate.target_uri),
        ]
    )


def _usage_files(root: Path) -> tuple[Path, ...]:
    resolved_root = root.expanduser().resolve(strict=True)
    path = resolved_root / USAGE_RELATIVE_PATH
    directory = path.parent
    if not directory.exists() or _is_link(directory) or not directory.is_dir():
        return ()
    rotated: list[tuple[int, Path]] = []
    try:
        entries = tuple(directory.iterdir())
    except OSError:
        return ()
    prefix = f"{path.name}."
    for candidate in entries:
        if not candidate.name.startswith(prefix):
            continue
        suffix = candidate.name.removeprefix(prefix)
        if suffix.isdigit() and not _is_link(candidate) and candidate.is_file():
            rotated.append((int(suffix), candidate))
    ordered = [candidate for _, candidate in sorted(rotated, reverse=True)]
    if path.exists() and not _is_link(path) and path.is_file():
        ordered.append(path)
    return tuple(ordered)


def _decay_records(root: Path, *, context_id: str) -> tuple[_DecayRecord, ...]:
    resolved_root = root.expanduser().resolve(strict=True)
    records: list[_DecayRecord] = []
    for zone in ("02_knowledge", "03_work"):
        directory = resolved_root / zone
        if not directory.is_dir() or _is_link(directory):
            continue
        for path in sorted(directory.rglob("*.md")):
            if _is_link(path) or not path.is_file():
                continue
            try:
                raw, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
                relative = path.relative_to(resolved_root).as_posix()
                if raw.get("entity_type") == "task":
                    task = Task.model_validate(raw)
                    parsed = WorkctxUri.parse(task.uri)
                    if parsed.context_id != context_id or task.status in {
                        TaskStatus.DONE,
                        TaskStatus.CANCELLED,
                    }:
                        continue
                    records.append(
                        _DecayRecord(
                            target_uri=str(parsed),
                            entity_type="task",
                            source_path=relative,
                            canonical_activity_at=task.updated_at.astimezone(UTC),
                        )
                    )
                elif isinstance(raw.get("id"), str) and raw["id"].startswith("CLM-"):
                    claim = Claim.model_validate(raw)
                    if WorkctxUri.parse(
                        claim.subject
                    ).context_id != context_id or claim.status not in {
                        ClaimStatus.CURRENT,
                        ClaimStatus.UNCERTAIN,
                    }:
                        continue
                    records.append(
                        _DecayRecord(
                            target_uri=str(WorkctxUri(context_id, "claim", claim.id)),
                            entity_type="claim",
                            source_path=relative,
                            canonical_activity_at=claim.observed_at.astimezone(UTC),
                        )
                    )
            except (OSError, UnicodeError, ValueError, ValidationError):
                continue
    return tuple(sorted(records, key=lambda record: record.target_uri))


def _ledger_activity(
    root: Path,
    records: tuple[_DecayRecord, ...],
    now: datetime,
) -> dict[str, datetime]:
    by_path = {record.source_path: record.target_uri for record in records}
    known_uris = {record.target_uri for record in records}
    activity: dict[str, datetime] = {}
    for event in read_audit_events(root, through=now):
        touched_uris = {reference for reference in event.source_refs if reference in known_uris}
        for operation in event.operations:
            for path in _operation_paths(operation):
                target_uri = by_path.get(path)
                if target_uri is not None:
                    touched_uris.add(target_uri)
        for target_uri in touched_uris:
            previous = activity.get(target_uri)
            if previous is None or event.timestamp > previous:
                activity[target_uri] = event.timestamp.astimezone(UTC)
    return activity


def _operation_paths(operation: AuditOperation) -> tuple[str, ...]:
    if isinstance(operation, AuditMoveOperation):
        return (operation.source, operation.destination)
    return (operation.target,)


def _safe_size(path: Path) -> int:
    try:
        if _is_link(path) or not path.is_file():
            return 0
        return path.stat().st_size
    except OSError:
        return 0


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("usage clocks must be timezone-aware")
    return value.astimezone(UTC)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


__all__ = ["evaluate_usage", "summarize", "usage_status"]
