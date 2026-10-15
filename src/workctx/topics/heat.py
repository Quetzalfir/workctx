"""Deterministic computed heat for active context topics.

Each attributable activity signal contributes ``2 ** (-age / half_life)``. Evidence notes
and tasks contribute their distinct created/updated timestamps, while each committed ledger
write to a currently tagged canonical document contributes its event timestamp. Bucket cutoffs
use the context's linearly interpolated 25th/50th/75th percentiles, bounded by absolute score
floors of 0.5 (cool), 1.5 (warm), and 3.0 (hot). The floors keep sparse contexts from making a
single recent item hot.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from workctx.domain import Task, TopicHeat, TopicHeatBucket
from workctx.domain.transactions import (
    AuditCreateOperation,
    AuditMoveOperation,
    AuditUpdateOperation,
)
from workctx.topics.errors import TopicRegistryError
from workctx.topics.registry import iter_topic_documents, load_registry
from workctx.transactions import read_audit_events

DEFAULT_HEAT_HALF_LIFE_DAYS = 7
COOL_SCORE_FLOOR = 0.5
WARM_SCORE_FLOOR = 1.5
HOT_SCORE_FLOOR = 3.0
_SECONDS_PER_DAY = 86_400.0
_BUCKET_ORDER = {
    TopicHeatBucket.HOT: 0,
    TopicHeatBucket.WARM: 1,
    TopicHeatBucket.COOL: 2,
    TopicHeatBucket.COLD: 3,
}


def decayed_activity_score(
    activity: Iterable[datetime],
    *,
    evaluated_at: datetime,
    half_life_days: int = DEFAULT_HEAT_HALF_LIFE_DAYS,
) -> float:
    """Return the pure exponential-decay sum for timezone-aware activity timestamps."""

    now = _aware_utc(evaluated_at)
    if not 1 <= half_life_days <= 90:
        raise ValueError("half_life_days must be between 1 and 90")
    half_life_seconds = half_life_days * _SECONDS_PER_DAY
    score = 0.0
    for timestamp in activity:
        occurred_at = _aware_utc(timestamp)
        age_seconds = max(0.0, (now - occurred_at).total_seconds())
        score += math.exp2(-age_seconds / half_life_seconds)
    return score


def assign_heat_buckets(scores: Mapping[str, float]) -> dict[str, TopicHeatBucket]:
    """Assign deterministic distribution-relative buckets with sparse-context floors."""

    if any(not math.isfinite(score) or score < 0 for score in scores.values()):
        raise ValueError("topic heat scores must be finite and non-negative")
    ordered = sorted(scores.values())
    cool_cutoff = max(COOL_SCORE_FLOOR, _percentile(ordered, 0.25))
    warm_cutoff = max(WARM_SCORE_FLOOR, _percentile(ordered, 0.50))
    hot_cutoff = max(HOT_SCORE_FLOOR, _percentile(ordered, 0.75))
    return {
        slug: (
            TopicHeatBucket.HOT
            if score >= hot_cutoff
            else TopicHeatBucket.WARM
            if score >= warm_cutoff
            else TopicHeatBucket.COOL
            if score >= cool_cutoff
            else TopicHeatBucket.COLD
        )
        for slug, score in scores.items()
    }


def compute_topic_heat(
    root: Path,
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[TopicHeat, ...]:
    """Compute active-topic heat from canonical documents and the verified audit ledger."""

    evaluated_at = _aware_utc((clock or _utc_now)())
    snapshot = load_registry(root)
    active = {topic.slug: topic for topic in snapshot.registry.active_topics()}
    if not active:
        return ()

    from workctx.adapters.filesystem import CanonicalStore

    half_life_days = CanonicalStore(root).read_context_config().topics.heat_half_life_days
    activity: dict[str, list[datetime]] = defaultdict(list)
    topics_by_path: dict[str, tuple[str, ...]] = {}
    for record in iter_topic_documents(root):
        resolved: list[str] = []
        for value in record.document.topics:
            resolution = snapshot.registry.resolve(value)
            if resolution is not None and resolution.topic.slug in active:
                slug = resolution.topic.slug
                if slug not in resolved:
                    resolved.append(slug)
        if not resolved:
            continue
        topics_by_path[record.relative_path] = tuple(resolved)
        if isinstance(record.document, Task) or record.document.entity_type == "evidence":
            for timestamp in {record.document.created_at, record.document.updated_at}:
                for slug in resolved:
                    activity[slug].append(timestamp)

    try:
        events = read_audit_events(root, through=evaluated_at)
    except ValueError as exc:
        raise TopicRegistryError("Topic heat could not read the verified audit ledger.") from exc
    for event in events:
        if event.result != "committed":
            continue
        for operation in event.operations:
            path: str | None = None
            if isinstance(operation, (AuditCreateOperation, AuditUpdateOperation)):
                path = operation.target
            elif isinstance(operation, AuditMoveOperation):
                path = operation.destination
            if path is None:
                continue
            for slug in topics_by_path.get(path, ()):
                activity[slug].append(event.timestamp)

    raw_scores = {
        slug: decayed_activity_score(
            activity.get(slug, ()),
            evaluated_at=evaluated_at,
            half_life_days=half_life_days,
        )
        for slug in active
    }
    buckets = assign_heat_buckets(raw_scores)
    results = (
        TopicHeat(
            slug=slug,
            name=active[slug].name,
            score=round(raw_scores[slug], 6),
            heat=buckets[slug],
            last_activity=(_aware_utc(max(activity[slug])) if activity.get(slug) else None),
        )
        for slug in active
    )
    return tuple(
        sorted(
            results,
            key=lambda item: (
                _BUCKET_ORDER[item.heat],
                -item.score,
                item.slug,
            ),
        )
    )


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("topic heat clocks and activity timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "COOL_SCORE_FLOOR",
    "DEFAULT_HEAT_HALF_LIFE_DAYS",
    "HOT_SCORE_FLOOR",
    "WARM_SCORE_FLOOR",
    "assign_heat_buckets",
    "compute_topic_heat",
    "decayed_activity_score",
]
