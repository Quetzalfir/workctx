"""Approved conversion of advisory usage candidates into WP-680 records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workctx.domain.transactions import SystemActor
from workctx.suggestions import (
    SuggestionApprovalRequiredError,
    SuggestionMutationResult,
    SuggestionPayload,
    SuggestionService,
    SuggestionStatus,
    SuggestionType,
)
from workctx.usage.evaluation import evaluate_usage
from workctx.usage.models import UsageCandidate, UsageCandidateKind

_ACTOR = SystemActor(
    type="system",
    id="workctx-usage-evaluator",
    agent=None,
    model=None,
)


@dataclass(frozen=True, slots=True)
class UsageSuggestionResult:
    candidates: tuple[UsageCandidate, ...]
    created: tuple[SuggestionMutationResult, ...]
    skipped: tuple[UsageCandidate, ...]


def suggest_usage(
    root: Path,
    *,
    now: datetime | None = None,
    approved: bool,
) -> UsageSuggestionResult:
    """Create one approved advisory record per candidate, idempotently while open."""

    if approved is not True:
        raise SuggestionApprovalRequiredError()
    current = _normalize_time(datetime.now(UTC) if now is None else now)
    candidates = evaluate_usage(root, now=current)
    service = SuggestionService(root, clock=lambda: current)
    open_records = service.list(statuses={SuggestionStatus.OPEN})
    created: list[SuggestionMutationResult] = []
    skipped: list[UsageCandidate] = []

    for candidate in candidates:
        marker = _marker(candidate)
        if any(
            marker in document.record.signal and candidate.target_uri in document.record.source_refs
            for document in open_records
        ):
            skipped.append(candidate)
            continue
        mutation = service.create(_payload(candidate, marker), approved=approved)
        created.append(mutation)
        open_records = (*open_records, mutation.suggestion)

    return UsageSuggestionResult(
        candidates=candidates,
        created=tuple(created),
        skipped=tuple(skipped),
    )


def _payload(candidate: UsageCandidate, marker: str) -> SuggestionPayload:
    target_key = hashlib.sha256(candidate.target_uri.encode("utf-8")).hexdigest()[:12]
    if candidate.kind is UsageCandidateKind.PROMOTION:
        rationale = f"Review usage promotion candidate {target_key}"
        detail = (
            f"{candidate.uses_30d} URI uses in the rolling 30-day window; "
            f"threshold {candidate.threshold_uses}."
        )
    else:
        rationale = f"Review usage decay candidate {target_key}"
        detail = (
            f"{candidate.inactive_days} days since canonical, usage, or ledger activity; "
            f"threshold {candidate.threshold_days}."
        )
    return SuggestionPayload(
        type=SuggestionType.ENGINE_PROPOSAL,
        rationale=rationale,
        signal=f"{marker}; {detail}",
        source_refs=(candidate.target_uri,),
        actor=_ACTOR,
        body=(
            "## Advisory usage candidate\n\n"
            f"Kind: `{candidate.kind.value}`\n\n"
            f"Signal: {detail}\n\n"
            "The target is the record's sole `source_ref`. No promotion, closure, "
            "archival, or supersession has been applied. Review the candidate and "
            "prepare a separate approved data-fix transaction if action is warranted.\n"
        ),
    )


def _marker(candidate: UsageCandidate) -> str:
    digest = hashlib.sha256(candidate.target_uri.encode("utf-8")).hexdigest()
    return f"usage:{candidate.kind.value}:{digest}"


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("usage clocks must be timezone-aware")
    return value.astimezone(UTC)


__all__ = ["UsageSuggestionResult", "suggest_usage"]
