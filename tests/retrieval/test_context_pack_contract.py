from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource

from workctx.retrieval import RANKING_FACTOR_WEIGHTS
from workctx.retrieval.models import ContextPack, PackItemKind, PackSectionName

ROOT = Path(__file__).parents[2]
SCHEMA_ROOT = ROOT / "schemas"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "context-pack"
POSITIVE_FIXTURES = sorted((FIXTURE_ROOT / "positive").glob("*.json"))
STRUCTURAL_NEGATIVE_FIXTURES = sorted((FIXTURE_ROOT / "negative" / "structural").glob("*.json"))
PRODUCER_INVARIANT_FIXTURES = sorted(
    (FIXTURE_ROOT / "negative" / "producer-invariant").glob("*.json")
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return value


SCHEMAS = {path.name: _load(path) for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))}
REGISTRY = Registry().with_resources(
    (cast(str, schema["$id"]), Resource.from_contents(schema)) for schema in SCHEMAS.values()
)
VALIDATOR = Draft202012Validator(
    SCHEMAS["context-pack.schema.json"],
    registry=REGISTRY,
    format_checker=FormatChecker(),
)


def _sections(payload: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], payload["sections"])


def _budget(payload: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], _sections(payload)["budget_and_truncation"])


def test_context_pack_fixture_sets_are_complete() -> None:
    assert {path.stem for path in POSITIVE_FIXTURES} == {"complete", "minimal"}
    assert {path.stem for path in STRUCTURAL_NEGATIVE_FIXTURES} == {
        "extra-field",
        "fractional-budget",
        "inconsistent-truncation",
        "invalid-fingerprint",
        "invalid-uri",
        "missing-section",
        "negative-budget",
        "numeric-string-budget",
        "wrong-schema-version",
    }
    assert {path.stem for path in PRODUCER_INVARIANT_FIXTURES} == {
        "focal-context-mismatch",
        "minimum-exceeds-used",
        "omitted-count-mismatch",
        "over-budget-arithmetic-mismatch",
        "section-omission-mismatch",
        "within-budget-mismatch",
    }


def test_context_pack_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMAS["context-pack.schema.json"])


def test_schema_discloses_adr_0011_producer_invariants() -> None:
    description = cast(str, SCHEMAS["context-pack.schema.json"]["description"])

    for required_disclosure in (
        "validation is necessary but not sufficient",
        "focal_uri belongs to context_id",
        "used_units equals the documented cost of included items",
        "minimum_units does not exceed used_units",
        "within_budget is true if and only if",
        "over_budget_by equals",
        "budget_and_truncation.omitted_count equals the length of omitted_items",
        "every section omitted_count equals",
        "omission identities, units, and order",
        "ranking producers compute rank.total",
        "rank.total is authoritative",
    ):
        assert required_disclosure in description


def test_section_and_item_vocabularies_match_the_schema() -> None:
    schema_defs = cast(dict[str, Any], SCHEMAS["context-pack.schema.json"]["$defs"])
    section_schema = cast(dict[str, Any], schema_defs["contextPackSections"])
    section_names = (
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
    )

    assert tuple(section_schema["properties"]) == section_names
    assert tuple(section_schema["required"]) == section_names
    assert tuple(item.value for item in PackSectionName) == section_names[:-1]
    assert cast(dict[str, Any], schema_defs["packItem"])["properties"]["kind"]["enum"] == [
        item.value for item in PackItemKind
    ]


@pytest.mark.parametrize("path", POSITIVE_FIXTURES, ids=lambda path: path.stem)
def test_positive_context_pack_fixtures_validate_and_round_trip(path: Path) -> None:
    payload = _load(path)
    VALIDATOR.validate(payload)

    pack = ContextPack.model_validate(payload)
    dumped = pack.model_dump(mode="json")

    VALIDATOR.validate(dumped)
    assert ContextPack.model_validate(dumped) == pack
    assert ContextPack.model_validate_json(pack.model_dump_json()) == pack


@pytest.mark.parametrize("path", STRUCTURAL_NEGATIVE_FIXTURES, ids=lambda path: path.stem)
def test_structural_negative_fixtures_are_rejected_by_schema_and_model(
    path: Path,
) -> None:
    payload = _load(path)

    assert list(VALIDATOR.iter_errors(payload))
    with pytest.raises(ValidationError):
        ContextPack.model_validate(payload)


@pytest.mark.parametrize("path", PRODUCER_INVARIANT_FIXTURES, ids=lambda path: path.stem)
def test_producer_invariant_fixtures_are_schema_valid_but_model_rejected(
    path: Path,
) -> None:
    payload = _load(path)

    VALIDATOR.validate(payload)
    with pytest.raises(ValidationError):
        ContextPack.model_validate(payload)


def test_integral_json_float_budget_normalizes_consistently() -> None:
    payload = _load(FIXTURE_ROOT / "positive" / "minimal.json")
    _budget(payload)["requested_units"] = 16.0

    VALIDATOR.validate(payload)
    pack = ContextPack.model_validate(payload)

    assert pack.sections.budget_and_truncation.requested_units == 16
    VALIDATOR.validate(pack.model_dump(mode="json"))


@pytest.mark.parametrize("encoded_separator", ("%2F", "%5C"))
@pytest.mark.parametrize("location", ("focal_uri", "item_uri"))
def test_encoded_path_separators_are_rejected_by_schema_and_model(
    encoded_separator: str,
    location: str,
) -> None:
    payload = _load(FIXTURE_ROOT / "positive" / "minimal.json")
    invalid_uri = f"workctx://fictional-context/task/TASK-2026-014{encoded_separator}unexpected"
    if location == "focal_uri":
        payload["focal_uri"] = invalid_uri
    else:
        focal = cast(dict[str, Any], _sections(payload)["focal_entity"])
        first_item = cast(dict[str, Any], cast(list[Any], focal["items"])[0])
        first_item["uri"] = invalid_uri

    assert list(VALIDATOR.iter_errors(payload))
    with pytest.raises(ValidationError):
        ContextPack.model_validate(payload)


def test_focal_section_requires_exactly_one_item() -> None:
    payload = _load(FIXTURE_ROOT / "positive" / "minimal.json")
    focal = cast(dict[str, Any], _sections(payload)["focal_entity"])
    focal["items"] = []

    assert list(VALIDATOR.iter_errors(payload))
    with pytest.raises(ValidationError, match="exactly one item"):
        ContextPack.model_validate(payload)


@pytest.mark.parametrize(
    ("factor", "value"),
    (
        ("relation_semantics", 101),
        ("total", 10_001),
    ),
)
def test_rank_value_domains_are_aligned(factor: str, value: int) -> None:
    payload = _load(FIXTURE_ROOT / "positive" / "complete.json")
    focal = cast(dict[str, Any], _sections(payload)["focal_entity"])
    first_item = cast(dict[str, Any], cast(list[Any], focal["items"])[0])
    rank = cast(dict[str, Any], first_item["rank"])
    rank[factor] = value

    assert list(VALIDATOR.iter_errors(payload))
    with pytest.raises(ValidationError):
        ContextPack.model_validate(payload)


def test_positive_fixture_rank_total_matches_producer_formula() -> None:
    payload = _load(FIXTURE_ROOT / "positive" / "complete.json")
    focal = cast(dict[str, Any], _sections(payload)["focal_entity"])
    first_item = cast(dict[str, Any], cast(list[Any], focal["items"])[0])
    rank = cast(dict[str, Any], first_item["rank"])
    factor_values = {
        "relation_semantics": cast(int, rank["relation_semantics"]),
        "recency": cast(int, rank["recency"]),
        "claim_state": cast(int, rank["current_state"]),
        "confidence": cast(int, rank["confidence"]),
        "directness": cast(int, rank["directness"]),
        "query_match": cast(int, rank["query_match"]),
        "entity_importance": cast(int, rank["entity_importance"]),
        "source_quality": cast(int, rank["source_quality"]),
    }

    assert rank["total"] == sum(
        factor_values[name] * weight for name, weight in RANKING_FACTOR_WEIGHTS.items()
    )


def test_serialized_rank_total_is_authoritative_for_consumers() -> None:
    payload = _load(FIXTURE_ROOT / "positive" / "complete.json")
    focal = cast(dict[str, Any], _sections(payload)["focal_entity"])
    first_item = cast(dict[str, Any], cast(list[Any], focal["items"])[0])
    rank = cast(dict[str, Any], first_item["rank"])
    rank["total"] = 9_624

    VALIDATOR.validate(payload)
    pack = ContextPack.model_validate(payload)

    serialized_rank = pack.sections.focal_entity.items[0].rank
    assert serialized_rank is not None
    assert serialized_rank.total == 9_624
