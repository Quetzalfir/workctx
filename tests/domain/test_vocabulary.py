import json
from pathlib import Path

import pytest

from workctx.domain.claims import ClaimStatus
from workctx.domain.observations import ObservationKind
from workctx.domain.relations import Confidence, RelationType
from workctx.domain.vocabulary import EntityType

ROOT = Path(__file__).parents[2]


def test_entity_type_vocabulary_is_exactly_decision_d018() -> None:
    expected = [
        "evidence",
        "person",
        "team",
        "project",
        "system",
        "service",
        "module",
        "flow",
        "integration",
        "decision",
        "risk",
        "question",
        "task",
        "claim",
        "draft",
        "investigation",
        "incident",
        "observation",
        "artifact",
    ]

    assert [item.value for item in EntityType] == expected


def test_relation_vocabulary_matches_public_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "reference.schema.json").read_text(encoding="utf-8"))

    assert [item.value for item in RelationType] == schema["properties"]["relation"]["enum"]


def test_observation_and_claim_vocabularies_match_public_schemas() -> None:
    observation_schema = json.loads(
        (ROOT / "schemas" / "observation.schema.json").read_text(encoding="utf-8")
    )
    claim_schema = json.loads((ROOT / "schemas" / "claim.schema.json").read_text(encoding="utf-8"))

    assert [item.value for item in ObservationKind] == observation_schema["properties"]["kind"][
        "enum"
    ]
    assert [item.value for item in ClaimStatus] == claim_schema["properties"]["status"]["enum"]
    expected_confidence = observation_schema["properties"]["confidence"]["enum"]
    assert [item.value for item in Confidence] == expected_confidence
    assert claim_schema["properties"]["confidence"]["enum"] == expected_confidence


def test_unknown_entity_and_relation_types_are_rejected() -> None:
    with pytest.raises(ValueError):
        EntityType("unknown")
    with pytest.raises(ValueError):
        RelationType("approximately_related")
