"""Canonical topic registry and duplicate-guard APIs."""

from workctx.topics.errors import (
    TopicDuplicateError,
    TopicError,
    TopicMutationError,
    TopicNotFoundError,
    TopicRegistryError,
)
from workctx.topics.guard import (
    EDIT_DISTANCE_THRESHOLD,
    DuplicateRule,
    NormalizedSubject,
    TopicDuplicateMatch,
    find_duplicate_topics,
    normalize_subject,
    normalized_edit_distance,
)
from workctx.topics.heat import (
    COOL_SCORE_FLOOR,
    DEFAULT_HEAT_HALF_LIFE_DAYS,
    HOT_SCORE_FLOOR,
    WARM_SCORE_FLOOR,
    assign_heat_buckets,
    compute_topic_heat,
    decayed_activity_score,
)
from workctx.topics.registry import load_registry, resolve_active_topic
from workctx.topics.service import (
    TopicMutationResult,
    TopicReviewCandidate,
    TopicService,
    add_topic,
    add_topic_alias,
    list_topics,
    merge_topics,
    resolve_topic,
    review_topics,
)

__all__ = [
    "COOL_SCORE_FLOOR",
    "DEFAULT_HEAT_HALF_LIFE_DAYS",
    "EDIT_DISTANCE_THRESHOLD",
    "HOT_SCORE_FLOOR",
    "WARM_SCORE_FLOOR",
    "DuplicateRule",
    "NormalizedSubject",
    "TopicDuplicateError",
    "TopicDuplicateMatch",
    "TopicError",
    "TopicMutationError",
    "TopicMutationResult",
    "TopicNotFoundError",
    "TopicRegistryError",
    "TopicReviewCandidate",
    "TopicService",
    "add_topic",
    "add_topic_alias",
    "assign_heat_buckets",
    "compute_topic_heat",
    "decayed_activity_score",
    "find_duplicate_topics",
    "list_topics",
    "load_registry",
    "merge_topics",
    "normalize_subject",
    "normalized_edit_distance",
    "resolve_active_topic",
    "resolve_topic",
    "review_topics",
]
