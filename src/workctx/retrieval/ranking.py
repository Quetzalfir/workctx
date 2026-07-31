"""Deterministic, explainable ranking for retrieval candidates.

Every factor is an integer in the inclusive range 0..100. The public
``RANKING_FACTOR_WEIGHTS`` values sum to 100, so the weighted total is always
in 0..10_000. Ranking never reads the system clock: callers may supply an
aware reference time, while the default is the latest candidate timestamp (or
``DEFAULT_REFERENCE_TIME`` when no candidate has a timestamp).

Literal query matching NFC-normalizes and case-folds text, removes combining
diacritics, splits on non-alphanumeric characters, and compares complete
tokens. It does not interpret query syntax or award substring matches.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final

from workctx.domain import (
    ClaimStatus,
    Confidence,
    EntityType,
    ObservationKind,
    RelationType,
    SourceLocator,
    TaskPriority,
)

DEFAULT_REFERENCE_TIME: Final = datetime(1970, 1, 1, tzinfo=UTC)

# These weights are part of the public ranking contract. Their sum of 100
# makes a factor vector of eight 100s produce the maximum total of 10_000.
RANKING_FACTOR_WEIGHTS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "relation_semantics": 20,
        "recency": 15,
        "claim_state": 15,
        "confidence": 10,
        "directness": 15,
        "query_match": 10,
        "entity_importance": 5,
        "source_quality": 10,
    }
)

RELATION_SEMANTIC_SCORES: Final[Mapping[RelationType, int]] = MappingProxyType(
    {
        RelationType.EVIDENCED_BY: 100,
        RelationType.SUPPORTS: 100,
        RelationType.CONTRADICTS: 100,
        RelationType.SUPERSEDES: 100,
        RelationType.DERIVED_FROM: 95,
        RelationType.BLOCKS: 95,
        RelationType.DEPENDS_ON: 95,
        RelationType.WAITING_ON: 95,
        RelationType.OWNED_BY: 90,
        RelationType.REQUESTED_BY: 85,
        RelationType.PARENT_OF: 85,
        RelationType.IMPLEMENTS: 80,
        RelationType.AFFECTS: 80,
        RelationType.AUTHENTICATES_VIA: 80,
        RelationType.OPERATED_BY: 80,
        RelationType.PRODUCES: 75,
        RelationType.CALLS: 70,
        RelationType.PUBLISHES_TO: 70,
        RelationType.CONSUMES_FROM: 70,
        RelationType.STORES_IN: 70,
        RelationType.MENTIONS: 40,
        RelationType.RELATED_TO: 20,
    }
)

# The first inclusive age boundary that matches determines the score.
RECENCY_BUCKETS: Final[tuple[tuple[timedelta, int], ...]] = (
    (timedelta(days=7), 100),
    (timedelta(days=30), 80),
    (timedelta(days=90), 60),
    (timedelta(days=180), 40),
    (timedelta(days=365), 20),
)

CLAIM_STATE_SCORES: Final[Mapping[ClaimStatus, int]] = MappingProxyType(
    {
        ClaimStatus.CURRENT: 100,
        ClaimStatus.UNCERTAIN: 60,
        ClaimStatus.SUPERSEDED: 25,
        ClaimStatus.RETRACTED: 0,
    }
)
NON_CLAIM_STATE_SCORE: Final = 50

CONFIDENCE_SCORES: Final[Mapping[Confidence, int]] = MappingProxyType(
    {
        Confidence.HIGH: 100,
        Confidence.MEDIUM: 60,
        Confidence.LOW: 25,
    }
)

DIRECTNESS_STEP: Final = 20

TASK_PRIORITY_IMPORTANCE_SCORES: Final[Mapping[TaskPriority, int]] = MappingProxyType(
    {
        TaskPriority.P0: 100,
        TaskPriority.P1: 80,
        TaskPriority.P2: 60,
        TaskPriority.P3: 40,
        TaskPriority.P4: 20,
    }
)

ENTITY_TYPE_IMPORTANCE_SCORES: Final[Mapping[EntityType, int]] = MappingProxyType(
    {
        EntityType.INCIDENT: 100,
        EntityType.RISK: 90,
        EntityType.DECISION: 85,
        EntityType.QUESTION: 80,
        EntityType.PROJECT: 75,
        EntityType.TASK: 70,
        EntityType.INVESTIGATION: 70,
        EntityType.SYSTEM: 65,
        EntityType.SERVICE: 65,
        EntityType.FLOW: 65,
        EntityType.INTEGRATION: 65,
        EntityType.PERSON: 60,
        EntityType.TEAM: 60,
        EntityType.EVIDENCE: 55,
        EntityType.CLAIM: 55,
        EntityType.OBSERVATION: 55,
        EntityType.MODULE: 45,
        EntityType.ARTIFACT: 35,
        EntityType.DRAFT: 20,
    }
)

OBSERVATION_KIND_SOURCE_QUALITY_SCORES: Final[Mapping[ObservationKind, int]] = MappingProxyType(
    {
        ObservationKind.FACT: 100,
        ObservationKind.DECISION: 95,
        ObservationKind.COMMITMENT: 90,
        ObservationKind.TASK: 85,
        ObservationKind.BLOCKER: 85,
        ObservationKind.DEPENDENCY: 85,
        ObservationKind.RISK: 75,
        ObservationKind.QUESTION: 65,
        ObservationKind.INFERENCE: 55,
        ObservationKind.ASSUMPTION: 25,
    }
)

LOCATOR_PRECISION_SCORES: Final[Mapping[str, int]] = MappingProxyType(
    {
        "line_range": 100,
        "page_range": 100,
        "time_range": 100,
        "message": 100,
        "image_region": 100,
        "json_pointer": 100,
        "table_range": 100,
        "repo_range": 100,
        "whole_artifact": 20,
    }
)


@dataclass(frozen=True, slots=True)
class RankingCandidate:
    """Typed inputs needed to score one retrieval candidate."""

    key: str
    relation: RelationType | None = None
    timestamp: datetime | None = None
    claim_status: ClaimStatus | None = None
    confidence: Confidence | None = None
    depth: int = 1
    search_text: str = ""
    entity_type: EntityType | None = None
    task_priority: TaskPriority | None = None
    observation_kind: ObservationKind | None = None
    source_locator: SourceLocator | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("Ranking candidate key must not be empty")
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("Ranking candidate depth must be a non-negative integer")
        if self.timestamp is not None:
            _require_aware(self.timestamp, "Ranking candidate timestamp")


@dataclass(frozen=True, slots=True)
class RankingFactors:
    """Explainable normalized values for the eight doc-03 ranking factors."""

    relation_semantics: int
    recency: int
    claim_state: int
    confidence: int
    directness: int
    query_match: int
    entity_importance: int
    source_quality: int

    def __post_init__(self) -> None:
        for value in self.as_tuple():
            if not 0 <= value <= 100:
                raise ValueError("Ranking factor values must be between 0 and 100")

    def as_tuple(self) -> tuple[int, int, int, int, int, int, int, int]:
        """Return factors in the documented doc-03 tie-break order."""

        return (
            self.relation_semantics,
            self.recency,
            self.claim_state,
            self.confidence,
            self.directness,
            self.query_match,
            self.entity_importance,
            self.source_quality,
        )


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """One candidate with its factor breakdown and deterministic total."""

    candidate: RankingCandidate
    factors: RankingFactors
    total_score: int
    reference_time: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.total_score <= 10_000:
            raise ValueError("Ranking total must be between 0 and 10000")
        _require_aware(self.reference_time, "Ranking reference time")


def score_candidate(
    candidate: RankingCandidate,
    *,
    query: str | None = None,
    reference_time: datetime | None = None,
) -> RankedCandidate:
    """Score one candidate without consulting a clock or external state."""

    resolved_reference_time = _resolve_reference_time((candidate,), reference_time)
    factors = RankingFactors(
        relation_semantics=(
            0 if candidate.relation is None else RELATION_SEMANTIC_SCORES[candidate.relation]
        ),
        recency=_recency_score(candidate.timestamp, resolved_reference_time),
        claim_state=(
            NON_CLAIM_STATE_SCORE
            if candidate.claim_status is None
            else CLAIM_STATE_SCORES[candidate.claim_status]
        ),
        confidence=(0 if candidate.confidence is None else CONFIDENCE_SCORES[candidate.confidence]),
        directness=max(0, 100 - candidate.depth * DIRECTNESS_STEP),
        query_match=_literal_query_match(query, candidate.search_text),
        entity_importance=_entity_importance(candidate),
        source_quality=_source_quality(candidate),
    )
    total_score = sum(
        factor * RANKING_FACTOR_WEIGHTS[name]
        for name, factor in zip(RANKING_FACTOR_WEIGHTS, factors.as_tuple(), strict=True)
    )
    return RankedCandidate(
        candidate=candidate,
        factors=factors,
        total_score=total_score,
        reference_time=resolved_reference_time,
    )


def rank(
    candidates: Iterable[RankingCandidate],
    *,
    query: str | None = None,
    reference_time: datetime | None = None,
) -> tuple[RankedCandidate, ...]:
    """Rank unique candidates by total, factor vector, then key ascending."""

    materialized = tuple(candidates)
    keys = [candidate.key for candidate in materialized]
    if len(keys) != len(set(keys)):
        raise ValueError("Ranking candidate keys must be unique")

    resolved_reference_time = _resolve_reference_time(materialized, reference_time)
    scored = tuple(
        score_candidate(
            candidate,
            query=query,
            reference_time=resolved_reference_time,
        )
        for candidate in materialized
    )
    return tuple(
        sorted(
            scored,
            key=lambda item: (
                -item.total_score,
                *(-value for value in item.factors.as_tuple()),
                item.candidate.key,
            ),
        )
    )


def _resolve_reference_time(
    candidates: tuple[RankingCandidate, ...],
    reference_time: datetime | None,
) -> datetime:
    if reference_time is not None:
        _require_aware(reference_time, "Ranking reference time")
        return reference_time.astimezone(UTC)
    return max(
        (
            candidate.timestamp.astimezone(UTC)
            for candidate in candidates
            if candidate.timestamp is not None
        ),
        default=DEFAULT_REFERENCE_TIME,
    )


def _recency_score(timestamp: datetime | None, reference_time: datetime) -> int:
    if timestamp is None:
        return 0
    age = reference_time - timestamp.astimezone(UTC)
    if age < timedelta(0):
        age = timedelta(0)
    for maximum_age, score in RECENCY_BUCKETS:
        if age <= maximum_age:
            return score
    return 0


def _literal_query_match(query: str | None, search_text: str) -> int:
    query_tokens = _literal_tokens("" if query is None else query)
    if not query_tokens:
        return 0
    candidate_tokens = frozenset(_literal_tokens(search_text))
    matched = sum(token in candidate_tokens for token in frozenset(query_tokens))
    return 100 * matched // len(frozenset(query_tokens))


def _literal_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFD", unicodedata.normalize("NFC", value).casefold())
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        if unicodedata.category(character).startswith("M"):
            continue
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current.clear()
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _entity_importance(candidate: RankingCandidate) -> int:
    if candidate.task_priority is not None:
        return TASK_PRIORITY_IMPORTANCE_SCORES[candidate.task_priority]
    if candidate.entity_type is None:
        return 0
    return ENTITY_TYPE_IMPORTANCE_SCORES[candidate.entity_type]


def _source_quality(candidate: RankingCandidate) -> int:
    kind_score = (
        None
        if candidate.observation_kind is None
        else OBSERVATION_KIND_SOURCE_QUALITY_SCORES[candidate.observation_kind]
    )
    locator_score = (
        None
        if candidate.source_locator is None
        else LOCATOR_PRECISION_SCORES[candidate.source_locator.type]
    )
    available_scores = tuple(score for score in (kind_score, locator_score) if score is not None)
    if not available_scores:
        return 0
    return sum(available_scores) // len(available_scores)


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
