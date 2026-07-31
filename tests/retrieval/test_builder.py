from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from projections.support import (
    create_fictional_context,
    entity_frontmatter,
    write_markdown,
)
from workspace.schema_support import validator_for

import workctx.retrieval.builder as retrieval_builder
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import ClaimStatus, RelationType, TaskPriority
from workctx.retrieval import (
    ContextBoundaryError,
    EdgeDirection,
    MissingObservation,
    MissingObservationReason,
    PackBuildStatus,
    PackItem,
    PackItemKind,
    PackSectionName,
    TraversedEdge,
    build_pack,
    estimate_pack_item_units,
    serialize_context_pack,
)

from .support import create_context_pack_projection

TASK_URI = "workctx://fictional-context/task/TASK-2026-001"


def _built_pack(
    projection: SQLiteProjection,
    *,
    budget: int = 100_000,
    include_history: bool = False,
    include_architecture: bool = True,
):
    result = build_pack(
        projection,
        TASK_URI,
        budget=budget,
        query="authentication rollout",
        include_history=include_history,
        include_architecture=include_architecture,
    )
    assert result.status is PackBuildStatus.BUILT
    assert result.pack is not None
    return result.pack


@pytest.mark.integration
def test_build_pack_has_all_ten_sections_current_claim_and_exact_locator(
    tmp_path: Path,
) -> None:
    projection = create_context_pack_projection(tmp_path / "fictional-context")

    pack = _built_pack(projection)
    sections = pack.sections

    assert list(type(sections).model_fields) == [
        "focal_entity",
        "claims_and_status_history",
        "direct_relationships",
        "source_observations",
        "related_tasks_and_dependencies",
        "people_and_interactions",
        "decisions_risks_and_questions",
        "contradictory_or_superseding_evidence",
        "architecture_entities",
        "budget_and_truncation",
    ]
    assert sections.focal_entity.items[0].id == "TASK-2026-001"
    assert {item.id for item in sections.claims_and_status_history.items} == {
        "CLM-2026-00002",
        "claim-history-summary",
    }
    assert sections.direct_relationships.items
    assert sections.related_tasks_and_dependencies.items
    assert sections.people_and_interactions.items
    assert sections.decisions_risks_and_questions.items
    assert sections.contradictory_or_superseding_evidence.items
    assert sections.architecture_entities.items

    observation = next(
        item
        for item in sections.source_observations.items
        if item.kind is PackItemKind.OBSERVATION and item.id.endswith("OBS-001")
    )
    locator = observation.data["locator"]
    assert isinstance(locator, dict)
    assert locator == {"type": "line_range", "start_line": 4, "end_line": 7}
    assert sections.budget_and_truncation.truncated is False
    assert sections.budget_and_truncation.omitted_items == []
    validator_for("context-pack.schema.json").validate(pack.model_dump(mode="json"))


def test_history_and_architecture_are_explicit_options(tmp_path: Path) -> None:
    projection = create_context_pack_projection(tmp_path / "fictional-context")

    default_pack = _built_pack(projection, include_architecture=False)
    history_pack = _built_pack(projection, include_history=True)

    assert default_pack.sections.architecture_entities.items == []
    assert {item.id for item in default_pack.sections.claims_and_status_history.items} == {
        "CLM-2026-00002",
        "claim-history-summary",
    }
    assert {item.id for item in history_pack.sections.claims_and_status_history.items} == {
        "CLM-2026-00001",
        "CLM-2026-00002",
    }


def test_identical_inputs_serialize_identically(tmp_path: Path) -> None:
    projection = create_context_pack_projection(tmp_path / "fictional-context")

    first = _built_pack(projection)
    second = _built_pack(projection)

    assert first == second
    assert serialize_context_pack(first) == serialize_context_pack(second)


def test_budget_truncates_in_priority_order_and_preserves_valid_minimum(
    tmp_path: Path,
) -> None:
    projection = create_context_pack_projection(
        tmp_path / "fictional-context",
        long_focal_body=True,
    )
    complete = _built_pack(projection)
    complete_units = complete.sections.budget_and_truncation.used_units

    slightly_reduced = _built_pack(projection, budget=complete_units - 1)
    repeated_reduced = _built_pack(projection, budget=complete_units - 1)
    zero_budget = _built_pack(projection, budget=0)

    first_omission = slightly_reduced.sections.budget_and_truncation.omitted_items[0]
    assert first_omission.section is PackSectionName.ARCHITECTURE_ENTITIES
    assert serialize_context_pack(slightly_reduced) == serialize_context_pack(repeated_reduced)

    budget = zero_budget.sections.budget_and_truncation
    assert len(zero_budget.sections.focal_entity.items) == 1
    assert budget.truncated is True
    assert budget.within_budget is False
    assert budget.used_units == budget.minimum_units
    assert budget.over_budget_by == budget.minimum_units
    assert budget.omitted_items[-1].section is PackSectionName.FOCAL_ENTITY
    assert budget.omitted_items[-1].reason == "focal_details_compacted"
    section_runs = [
        omitted.section
        for index, omitted in enumerate(budget.omitted_items)
        if index == 0 or omitted.section != budget.omitted_items[index - 1].section
    ]
    assert section_runs == [
        PackSectionName.ARCHITECTURE_ENTITIES,
        PackSectionName.CLAIMS_AND_STATUS_HISTORY,
        PackSectionName.PEOPLE_AND_INTERACTIONS,
        PackSectionName.DECISIONS_RISKS_AND_QUESTIONS,
        PackSectionName.RELATED_TASKS_AND_DEPENDENCIES,
        PackSectionName.DIRECT_RELATIONSHIPS,
        PackSectionName.CONTRADICTORY_OR_SUPERSEDING_EVIDENCE,
        PackSectionName.SOURCE_OBSERVATIONS,
        PackSectionName.CLAIMS_AND_STATUS_HISTORY,
        PackSectionName.FOCAL_ENTITY,
    ]
    validator_for("context-pack.schema.json").validate(zero_budget.model_dump(mode="json"))


def test_budget_exact_boundaries_report_consistent_overage(tmp_path: Path) -> None:
    projection = create_context_pack_projection(tmp_path / "fictional-context")
    complete = _built_pack(projection)
    complete_budget = complete.sections.budget_and_truncation

    exact_complete = _built_pack(projection, budget=complete_budget.used_units)
    exact_minimum = _built_pack(projection, budget=complete_budget.minimum_units)
    below_minimum = _built_pack(projection, budget=complete_budget.minimum_units - 1)

    assert exact_complete.sections.budget_and_truncation.truncated is False
    assert exact_complete.sections.budget_and_truncation.within_budget is True
    assert exact_minimum.sections.budget_and_truncation.used_units == (
        complete_budget.minimum_units
    )
    assert exact_minimum.sections.budget_and_truncation.within_budget is True
    assert below_minimum.sections.budget_and_truncation.used_units == (
        complete_budget.minimum_units
    )
    assert below_minimum.sections.budget_and_truncation.within_budget is False
    assert below_minimum.sections.budget_and_truncation.over_budget_by == 1


def test_equal_total_ranking_order_is_preserved_during_budget_removal() -> None:
    section = PackSectionName.RELATED_TASKS_AND_DEPENDENCIES
    semantic_item = PackItem(
        id="semantic",
        kind=PackItemKind.RELATIONSHIP,
        uri=None,
        title="Semantic",
        summary="",
        data={},
        rank=None,
    )
    important_item = semantic_item.model_copy(update={"id": "important", "title": "Important"})
    semantic = retrieval_builder._make_entry(
        section,
        semantic_item,
        drop_tier=4,
        relation=RelationType.RELATED_TO,
        claim_status=ClaimStatus.RETRACTED,
        depth=5,
        search_text="",
    )
    important = retrieval_builder._make_entry(
        section,
        important_item,
        drop_tier=4,
        claim_status=ClaimStatus.RETRACTED,
        task_priority=TaskPriority.P1,
        depth=5,
        search_text="",
    )

    grouped = retrieval_builder._rank_and_group((important, semantic), None)
    ranked_items = grouped[section]
    assert [entry.item.id for entry in ranked_items] == ["semantic", "important"]
    assert ranked_items[0].score == ranked_items[1].score

    focal = PackItem(
        id="focal",
        kind=PackItemKind.TASK,
        uri=TASK_URI,
        title="Focal",
        summary="",
        data={},
        rank=None,
    )
    exact_one_item_budget = estimate_pack_item_units(focal) + estimate_pack_item_units(
        ranked_items[0].item
    )
    sections = retrieval_builder._apply_budget(
        grouped,
        full_focal=focal,
        minimal_focal=focal,
        requested_units=exact_one_item_budget,
    )

    assert [item.id for item in sections.related_tasks_and_dependencies.items] == ["semantic"]
    assert sections.budget_and_truncation.omitted_items[0].item_id == "important"


def test_budget_unit_counts_unicode_characters_and_rounds_up() -> None:
    base = PackItem(
        id="unicode",
        kind=PackItemKind.EVIDENCE,
        uri=None,
        title="Café 🙂",
        summary="",
        data={"label": "México"},
        rank=None,
    )
    candidates = [
        base.model_copy(update={"summary": f"Evidence {'x' * suffix_length}"})
        for suffix_length in range(4)
    ]
    item = next(
        candidate
        for candidate in candidates
        if len(
            json.dumps(
                candidate.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        % 4
        == 1
    )
    compact = json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert len(compact.encode("utf-8")) > len(compact)
    assert len(compact) % 4 == 1
    assert estimate_pack_item_units(item) == (len(compact) + 3) // 4


def test_missing_observations_use_only_valid_durable_uris_and_sanitized_ids() -> None:
    missing_uri = "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-999"
    secret_reference = "api_key=fictional-secret-value-12345"
    source_trace = SimpleNamespace(
        observations=(),
        missing_observations=(
            MissingObservation(
                reference="not-a-durable-reference",
                reason=MissingObservationReason.INVALID_REFERENCE,
                referenced_by=(TASK_URI,),
            ),
            MissingObservation(
                reference="EVD-20260730-auth-flow-01#OBS-999",
                reason=MissingObservationReason.NOT_FOUND,
                referenced_by=(TASK_URI,),
            ),
            MissingObservation(
                reference=missing_uri,
                reason=MissingObservationReason.NOT_FOUND,
                referenced_by=(TASK_URI,),
            ),
            MissingObservation(
                reference=secret_reference,
                reason=MissingObservationReason.INVALID_REFERENCE,
                referenced_by=(TASK_URI,),
            ),
        ),
    )

    entries = retrieval_builder._observation_entries(source_trace)

    assert len(entries) == 4
    assert entries[0].item.uri is None
    assert entries[0].item.data["reference"] == "not-a-durable-reference"
    assert entries[0].item.data["reason"] == "invalid_reference"
    assert entries[1].item.uri is None
    assert entries[2].item.uri == missing_uri
    assert secret_reference not in entries[3].item.model_dump_json()
    assert secret_reference not in entries[3].item.id


def test_supporting_observation_kind_and_locator_both_affect_pack_ranking(
    tmp_path: Path,
) -> None:
    projection = create_context_pack_projection(
        tmp_path / "fictional-context",
        observation_kind="assumption",
    )

    pack = _built_pack(projection)
    claim = next(
        item
        for item in pack.sections.claims_and_status_history.items
        if item.id == "CLM-2026-00002"
    )
    relationship = next(
        item
        for item in pack.sections.direct_relationships.items
        if item.data.get("relation") == "owned_by"
    )

    assert claim.rank is not None
    assert relationship.rank is not None
    assert claim.rank.source_quality == 62
    assert relationship.rank.source_quality == 62


def test_pack_recency_uses_supporting_evidence_timestamp_not_record_timestamp(
    tmp_path: Path,
) -> None:
    undated_projection = create_context_pack_projection(tmp_path / "undated")
    dated_projection = create_context_pack_projection(
        tmp_path / "dated",
        observation_observed_at="2026-07-30T12:00:00Z",
    )

    undated_pack = _built_pack(undated_projection)
    dated_pack = _built_pack(dated_projection)
    undated_relationship = next(
        item
        for item in undated_pack.sections.direct_relationships.items
        if item.data.get("relation") == "owned_by"
    )
    dated_relationship = next(
        item
        for item in dated_pack.sections.direct_relationships.items
        if item.data.get("relation") == "owned_by"
    )

    assert undated_relationship.rank is not None
    assert dated_relationship.rank is not None
    assert undated_relationship.rank.recency == 0
    assert dated_relationship.rank.recency == 100


def test_default_pack_traces_distinct_historical_claim_evidence(
    tmp_path: Path,
) -> None:
    projection = create_context_pack_projection(
        tmp_path / "fictional-context",
        distinct_historical_observation=True,
    )

    pack = _built_pack(projection)
    historical_observation = next(
        item for item in pack.sections.source_observations.items if item.id.endswith("OBS-002")
    )
    historical_metadata = next(
        item
        for item in pack.sections.contradictory_or_superseding_evidence.items
        if item.id == "historical:CLM-2026-00001"
    )

    assert historical_observation.data["locator"] == {
        "type": "line_range",
        "start_line": 8,
        "end_line": 9,
    }
    assert historical_metadata.rank is not None
    assert historical_metadata.rank.source_quality == 62
    assert historical_metadata.rank.recency == 100


def test_first_resolvable_authored_source_drives_claim_and_edge_ranking(
    tmp_path: Path,
) -> None:
    projection = create_context_pack_projection(
        tmp_path / "fictional-context",
        distinct_historical_observation=True,
    )
    fact = projection.get_observation("EVD-20260730-auth-flow-01#OBS-001")
    assumption = projection.get_observation("EVD-20260730-auth-flow-01#OBS-002")
    claim = projection.get_claim("CLM-2026-00002")
    assert fact is not None
    assert assumption is not None
    assert claim is not None
    observations = {
        str(fact.uri): fact,
        str(assumption.uri): assumption,
    }

    assumption_first_claim = replace(
        claim,
        source_observations=(assumption.uri, fact.uri),
    )
    fact_first_claim = replace(
        claim,
        source_observations=(fact.uri, assumption.uri),
    )
    assert (
        retrieval_builder._first_claim_observation(
            assumption_first_claim,
            observations,
        )
        == assumption
    )
    assert (
        retrieval_builder._first_claim_observation(
            fact_first_claim,
            observations,
        )
        == fact
    )

    edge = next(
        edge
        for edge in projection.outbound_edges(TASK_URI)
        if edge.relation is RelationType.OWNED_BY
    )
    assumption_first_edge = TraversedEdge(
        depth=1,
        direction=EdgeDirection.OUTBOUND,
        edge=replace(
            edge,
            source_observations=(str(assumption.uri), str(fact.uri)),
        ),
    )
    fact_first_edge = TraversedEdge(
        depth=1,
        direction=EdgeDirection.OUTBOUND,
        edge=replace(
            edge,
            source_observations=(str(fact.uri), str(assumption.uri)),
        ),
    )
    assert (
        retrieval_builder._first_edge_observation(
            assumption_first_edge,
            observations,
        )
        == assumption
    )
    assert (
        retrieval_builder._first_edge_observation(
            fact_first_edge,
            observations,
        )
        == fact
    )


def test_isolated_entity_produces_valid_empty_sections(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    write_markdown(
        root / "02_knowledge" / "system-isolated.md",
        entity_frontmatter(
            "fictional-context",
            "SYS-isolated",
            "system",
            "Isolated fictional system",
        ),
        "No authored relations.",
    )
    projection = SQLiteProjection(root)
    projection.rebuild()

    result = build_pack(
        projection,
        "workctx://fictional-context/system/SYS-isolated",
        budget=100_000,
    )

    assert result.pack is not None
    assert result.pack.sections.direct_relationships.items == []
    assert result.pack.sections.source_observations.items == []
    assert result.pack.sections.related_tasks_and_dependencies.items == []
    assert result.pack.sections.budget_and_truncation.truncated is False
    validator_for("context-pack.schema.json").validate(result.pack.model_dump(mode="json"))


def test_unknown_external_and_foreign_focals_have_typed_outcomes(tmp_path: Path) -> None:
    projection = create_context_pack_projection(tmp_path / "fictional-context")

    missing = build_pack(
        projection,
        "workctx://fictional-context/system/SYS-missing",
    )
    external = build_pack(projection, f"artifact://sha256/{'f' * 64}")

    assert missing.status is PackBuildStatus.NOT_FOUND
    assert missing.pack is None
    assert missing.message == "The focal Work Context entity was not found."
    assert external.status is PackBuildStatus.UNSUPPORTED_REFERENCE
    assert external.pack is None
    with pytest.raises(ContextBoundaryError):
        build_pack(
            projection,
            "workctx://other-context/task/TASK-2026-001",
        )


def test_secret_looking_claim_values_are_absent_from_serialized_pack(
    tmp_path: Path,
) -> None:
    projection = create_context_pack_projection(
        tmp_path / "fictional-context",
        include_secret=True,
    )

    serialized = serialize_context_pack(_built_pack(projection))

    assert "fictional-secret-value-12345" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.parametrize("budget", [-1, True, 1.5, "10"])
def test_budget_rejects_non_json_integer_or_negative_values(
    tmp_path: Path,
    budget: object,
) -> None:
    projection = create_context_pack_projection(tmp_path / "fictional-context")

    with pytest.raises(ValueError, match="non-negative integer"):
        build_pack(projection, TASK_URI, budget=budget)  # type: ignore[arg-type]
