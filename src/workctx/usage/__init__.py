"""Public opt-in usage telemetry, evaluation, and suggestion APIs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from workctx.usage.models import (
    DecayCandidate,
    PromotionCandidate,
    UsageCandidate,
    UsageCandidateKind,
    UsageEvent,
    UsageStatus,
    UsageSummary,
    UsageTargetSummary,
    UsageWindowTotals,
)
from workctx.usage.recording import (
    DEFAULT_MAX_BYTES,
    DEFAULT_ROTATED_FILES,
    USAGE_RELATIVE_PATH,
    is_enabled,
    record,
)

if TYPE_CHECKING:
    from workctx.usage.suggestions import UsageSuggestionResult


def summarize(root: Path, *, now: datetime | None = None) -> UsageSummary:
    from workctx.usage.evaluation import summarize as _summarize

    return _summarize(root, now=now)


def usage_status(root: Path, *, now: datetime | None = None) -> UsageStatus:
    from workctx.usage.evaluation import usage_status as _usage_status

    return _usage_status(root, now=now)


def evaluate_usage(
    root: Path,
    *,
    now: datetime | None = None,
) -> tuple[UsageCandidate, ...]:
    from workctx.usage.evaluation import evaluate_usage as _evaluate_usage

    return _evaluate_usage(root, now=now)


def suggest_usage(
    root: Path,
    *,
    now: datetime | None = None,
    approved: bool,
) -> UsageSuggestionResult:
    from workctx.usage.suggestions import suggest_usage as _suggest_usage

    return _suggest_usage(root, now=now, approved=approved)


def __getattr__(name: str) -> Any:
    if name == "UsageSuggestionResult":
        from workctx.usage.suggestions import UsageSuggestionResult

        return UsageSuggestionResult
    raise AttributeError(name)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_ROTATED_FILES",
    "USAGE_RELATIVE_PATH",
    "DecayCandidate",
    "PromotionCandidate",
    "UsageCandidate",
    "UsageCandidateKind",
    "UsageEvent",
    "UsageStatus",
    "UsageSuggestionResult",
    "UsageSummary",
    "UsageTargetSummary",
    "UsageWindowTotals",
    "evaluate_usage",
    "is_enabled",
    "record",
    "suggest_usage",
    "summarize",
    "usage_status",
]
