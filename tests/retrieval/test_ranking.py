from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from workctx.domain import (
    ClaimStatus,
    Confidence,
    EntityType,
    ObservationKind,
    RelationType,
    TaskPriority,
)
from workctx.domain.locators import LineRangeLocator, WholeArtifactLocator
from workctx.retrieval.ranking import (
    DEFAULT_REFERENCE_TIME,
    RANKING_FACTOR_WEIGHTS,
    RankingCandidate,
    rank,
    score_candidate,
)

REFERENCE_TIME = datetime(2026, 7, 30, 12, tzinfo=UTC)


def _baseline(key: str = "candidate") -> RankingCandidate:
    return RankingCandidate(
        key=key,
        relation=RelationType.RELATED_TO,
        timestamp=REFERENCE_TIME - timedelta(days=100),
        claim_status=ClaimStatus.SUPERSEDED,
        confidence=Confidence.LOW,
        depth=3,
        search_text="unrelated material",
        entity_type=EntityType.DRAFT,
        observation_kind=ObservationKind.ASSUMPTION,
        source_locator=WholeArtifactLocator(
            type="whole_artifact",
            justification="The fictional source has no narrower locator.",
        ),
    )


def test_relation_semantics_factor_orders_candidates() -> None:
    baseline = _baseline()
    stronger = score_candidate(
        replace(baseline, relation=RelationType.SUPPORTS),
        reference_time=REFERENCE_TIME,
    )
    weaker = score_candidate(baseline, reference_time=REFERENCE_TIME)

    assert stronger.factors.relation_semantics > weaker.factors.relation_semantics
    assert stronger.total_score > weaker.total_score


def test_recency_factor_orders_candidates() -> None:
    baseline = _baseline()
    recent = score_candidate(
        replace(baseline, timestamp=REFERENCE_TIME - timedelta(days=1)),
        reference_time=REFERENCE_TIME,
    )
    old = score_candidate(
        replace(baseline, timestamp=REFERENCE_TIME - timedelta(days=400)),
        reference_time=REFERENCE_TIME,
    )

    assert recent.factors.recency > old.factors.recency
    assert recent.total_score > old.total_score


def test_current_claim_factor_orders_candidates() -> None:
    baseline = _baseline()
    current = score_candidate(
        replace(baseline, claim_status=ClaimStatus.CURRENT),
        reference_time=REFERENCE_TIME,
    )
    superseded = score_candidate(baseline, reference_time=REFERENCE_TIME)

    assert current.factors.claim_state > superseded.factors.claim_state
    assert current.total_score > superseded.total_score


def test_confidence_factor_orders_candidates() -> None:
    baseline = _baseline()
    high = score_candidate(
        replace(baseline, confidence=Confidence.HIGH),
        reference_time=REFERENCE_TIME,
    )
    low = score_candidate(baseline, reference_time=REFERENCE_TIME)

    assert high.factors.confidence > low.factors.confidence
    assert high.total_score > low.total_score


def test_directness_factor_orders_candidates() -> None:
    baseline = _baseline()
    direct = score_candidate(replace(baseline, depth=1), reference_time=REFERENCE_TIME)
    indirect = score_candidate(baseline, reference_time=REFERENCE_TIME)

    assert direct.factors.directness > indirect.factors.directness
    assert direct.total_score > indirect.total_score


def test_literal_query_match_factor_orders_candidates() -> None:
    baseline = _baseline()
    matching = score_candidate(
        replace(baseline, search_text="The Café gateway is READY."),
        query="cafe gateway",
        reference_time=REFERENCE_TIME,
    )
    nonmatching = score_candidate(
        replace(baseline, search_text="Authentication gatewayed behavior"),
        query="cafe gateway",
        reference_time=REFERENCE_TIME,
    )

    assert matching.factors.query_match == 100
    assert nonmatching.factors.query_match == 0
    assert matching.total_score > nonmatching.total_score


def test_entity_importance_factor_orders_candidates() -> None:
    baseline = replace(_baseline(), entity_type=EntityType.TASK)
    urgent = score_candidate(
        replace(baseline, task_priority=TaskPriority.P0),
        reference_time=REFERENCE_TIME,
    )
    low_priority = score_candidate(
        replace(baseline, task_priority=TaskPriority.P4),
        reference_time=REFERENCE_TIME,
    )

    assert urgent.factors.entity_importance > low_priority.factors.entity_importance
    assert urgent.total_score > low_priority.total_score


def test_source_quality_factor_orders_candidates() -> None:
    baseline = _baseline()
    precise_fact = score_candidate(
        replace(
            baseline,
            observation_kind=ObservationKind.FACT,
            source_locator=LineRangeLocator(type="line_range", start_line=4, end_line=7),
        ),
        reference_time=REFERENCE_TIME,
    )
    broad_assumption = score_candidate(baseline, reference_time=REFERENCE_TIME)

    assert precise_fact.factors.source_quality > broad_assumption.factors.source_quality
    assert precise_fact.total_score > broad_assumption.total_score


def test_weights_and_score_bounds_are_exact() -> None:
    assert sum(RANKING_FACTOR_WEIGHTS.values()) == 100
    maximum = score_candidate(
        RankingCandidate(
            key="maximum",
            relation=RelationType.SUPPORTS,
            timestamp=REFERENCE_TIME,
            claim_status=ClaimStatus.CURRENT,
            confidence=Confidence.HIGH,
            depth=0,
            search_text="exact",
            entity_type=EntityType.TASK,
            task_priority=TaskPriority.P0,
            observation_kind=ObservationKind.FACT,
            source_locator=LineRangeLocator(type="line_range", start_line=1, end_line=1),
        ),
        query="exact",
        reference_time=REFERENCE_TIME,
    )
    minimum = score_candidate(
        RankingCandidate(
            key="minimum",
            claim_status=ClaimStatus.RETRACTED,
            depth=5,
        ),
        query="absent",
        reference_time=REFERENCE_TIME,
    )

    assert maximum.factors.as_tuple() == (100,) * 8
    assert maximum.total_score == 10_000
    assert minimum.factors.as_tuple() == (0,) * 8
    assert minimum.total_score == 0


def test_producer_total_is_the_weighted_factor_sum() -> None:
    scored = score_candidate(_baseline(), reference_time=REFERENCE_TIME)

    assert scored.total_score == sum(
        factor * weight
        for factor, weight in zip(
            scored.factors.as_tuple(),
            RANKING_FACTOR_WEIGHTS.values(),
            strict=True,
        )
    )


def test_rank_is_repeatable_and_uses_key_ascending_for_complete_ties() -> None:
    candidates = (
        replace(_baseline("bravo"), timestamp=None),
        replace(_baseline("alpha"), timestamp=None),
    )

    first = rank(candidates)
    second = rank(candidates)

    assert first == second
    assert [item.candidate.key for item in first] == ["alpha", "bravo"]
    assert all(item.reference_time == DEFAULT_REFERENCE_TIME for item in first)


def test_factor_vector_precedes_key_when_totals_tie() -> None:
    semantic = RankingCandidate(
        key="z-semantic",
        relation=RelationType.RELATED_TO,
        claim_status=ClaimStatus.RETRACTED,
        depth=5,
    )
    important = RankingCandidate(
        key="a-important",
        claim_status=ClaimStatus.RETRACTED,
        depth=5,
        entity_type=EntityType.TASK,
        task_priority=TaskPriority.P1,
    )

    ranked = rank((important, semantic))

    assert ranked[0].total_score == ranked[1].total_score == 400
    assert [item.candidate.key for item in ranked] == ["z-semantic", "a-important"]


def test_rank_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="keys must be unique"):
        rank((_baseline("duplicate"), _baseline("duplicate")))


def test_candidate_rejects_naive_timestamp_and_negative_depth() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        RankingCandidate(key="naive", timestamp=datetime(2026, 7, 30, 12))
    with pytest.raises(ValueError, match="non-negative"):
        RankingCandidate(key="negative", depth=-1)


def test_scoring_rejects_naive_reference_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        score_candidate(_baseline(), reference_time=datetime(2026, 7, 30, 12))
    with pytest.raises(ValueError, match="timezone-aware"):
        rank((_baseline(),), reference_time=datetime(2026, 7, 30, 12))


def test_default_reference_time_is_latest_candidate_timestamp() -> None:
    older = replace(_baseline("older"), timestamp=REFERENCE_TIME - timedelta(days=40))
    latest = replace(_baseline("latest"), timestamp=REFERENCE_TIME)

    ranked = rank((older, latest))

    assert all(item.reference_time == REFERENCE_TIME for item in ranked)
    assert ranked[0].candidate.key == "latest"
    assert ranked[0].factors.recency > ranked[1].factors.recency


def test_explicit_reference_time_changes_recency_deterministically() -> None:
    candidate = replace(_baseline(), timestamp=REFERENCE_TIME)

    at_evidence_time = score_candidate(candidate, reference_time=REFERENCE_TIME)
    one_year_later = score_candidate(
        candidate,
        reference_time=REFERENCE_TIME + timedelta(days=366),
    )

    assert at_evidence_time.factors.recency == 100
    assert one_year_later.factors.recency == 0
