from __future__ import annotations

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from workctx.domain.artifacts import ArtifactManifest
from workctx.domain.entities import EntityFrontmatter
from workctx.domain.tasks import Task
from workctx.models.context import ContextConfig

from .schema_support import FIXTURE_ROOT, load_fixture, validator_for

OWNED_SCHEMAS = {
    "artifact-manifest": "artifact-manifest.schema.json",
    "audit-event": "audit-event.schema.json",
    "context": "context.schema.json",
    "entity": "entity.schema.json",
    "task": "task.schema.json",
    "transaction-proposal": "transaction-proposal.schema.json",
}

NEGATIVE_SCHEMA_CASES = {
    "artifact-manifest-hash": "artifact-manifest.schema.json",
    "artifact-manifest-missing-schema-version": "artifact-manifest.schema.json",
    "audit-event-hash": "audit-event.schema.json",
    "context-date-time": "context.schema.json",
    "context-federated-search": "context.schema.json",
    "context-missing-schema-version": "context.schema.json",
    "context-missing-timestamps": "context.schema.json",
    "context-numeric-date-time": "context.schema.json",
    "context-schema-version": "context.schema.json",
    "entity-missing-schema-version": "entity.schema.json",
    "entity-reference": "entity.schema.json",
    "entity-type": "entity.schema.json",
    "entity-uri-encoded-traversal": "entity.schema.json",
    "entity-uri": "entity.schema.json",
    "task-id": "task.schema.json",
    "task-missing-schema-version": "task.schema.json",
    "task-numeric-due-at": "task.schema.json",
    "transaction-proposal-operations": "transaction-proposal.schema.json",
}

MODEL_CASES: tuple[tuple[str, str, type[BaseModel]], ...] = (
    ("context", "context.schema.json", ContextConfig),
    ("entity", "entity.schema.json", EntityFrontmatter),
    ("task", "task.schema.json", Task),
    ("artifact-manifest", "artifact-manifest.schema.json", ArtifactManifest),
)

NEGATIVE_MODEL_CASES: tuple[tuple[str, str, type[BaseModel]], ...] = (
    ("context-date-time", "context.schema.json", ContextConfig),
    ("context-federated-search", "context.schema.json", ContextConfig),
    ("context-missing-schema-version", "context.schema.json", ContextConfig),
    ("context-numeric-date-time", "context.schema.json", ContextConfig),
    ("context-schema-version", "context.schema.json", ContextConfig),
    ("context-missing-timestamps", "context.schema.json", ContextConfig),
    ("entity-missing-schema-version", "entity.schema.json", EntityFrontmatter),
    ("entity-reference", "entity.schema.json", EntityFrontmatter),
    ("entity-type", "entity.schema.json", EntityFrontmatter),
    ("entity-uri-encoded-traversal", "entity.schema.json", EntityFrontmatter),
    ("entity-uri", "entity.schema.json", EntityFrontmatter),
    ("task-id", "task.schema.json", Task),
    ("task-missing-schema-version", "task.schema.json", Task),
    ("task-numeric-due-at", "task.schema.json", Task),
    ("artifact-manifest-hash", "artifact-manifest.schema.json", ArtifactManifest),
    (
        "artifact-manifest-missing-schema-version",
        "artifact-manifest.schema.json",
        ArtifactManifest,
    ),
)


def _fixture_names(group: str) -> set[str]:
    return {path.stem for path in (FIXTURE_ROOT / group).glob("*.json")}


def test_every_owned_schema_has_positive_and_negative_contract_fixtures() -> None:
    positive_names = _fixture_names("positive")
    negative_schema_names = set(NEGATIVE_SCHEMA_CASES.values())

    assert positive_names == set(OWNED_SCHEMAS)
    assert negative_schema_names == set(OWNED_SCHEMAS.values())


@pytest.mark.parametrize(("fixture_name", "schema_name"), sorted(OWNED_SCHEMAS.items()))
def test_positive_fixtures_validate_against_owned_schemas(
    fixture_name: str,
    schema_name: str,
) -> None:
    validator_for(schema_name).validate(load_fixture("positive", fixture_name))


@pytest.mark.parametrize(
    ("fixture_name", "schema_name"),
    sorted(NEGATIVE_SCHEMA_CASES.items()),
)
def test_negative_fixtures_are_rejected_by_owned_schemas(
    fixture_name: str,
    schema_name: str,
) -> None:
    with pytest.raises(JsonSchemaValidationError):
        validator_for(schema_name).validate(load_fixture("negative", fixture_name))


@pytest.mark.parametrize(("fixture_name", "schema_name", "model_type"), MODEL_CASES)
def test_typed_contracts_round_trip_and_remain_schema_valid(
    fixture_name: str,
    schema_name: str,
    model_type: type[BaseModel],
) -> None:
    instance = model_type.model_validate(load_fixture("positive", fixture_name))
    dumped = instance.model_dump(mode="json")

    validator_for(schema_name).validate(dumped)
    assert model_type.model_validate_json(instance.model_dump_json()) == instance


@pytest.mark.parametrize(
    ("fixture_name", "schema_name", "model_type"),
    NEGATIVE_MODEL_CASES,
)
def test_negative_typed_fixtures_are_rejected_by_schema_and_model(
    fixture_name: str,
    schema_name: str,
    model_type: type[BaseModel],
) -> None:
    fixture = load_fixture("negative", fixture_name)

    with pytest.raises(JsonSchemaValidationError):
        validator_for(schema_name).validate(fixture)
    with pytest.raises(PydanticValidationError):
        model_type.model_validate(fixture)


def test_entity_uri_identity_is_a_code_only_invariant() -> None:
    fixture = load_fixture("positive", "entity")
    fixture["id"] = "PER-different-person"

    validator_for("entity.schema.json").validate(fixture)
    with pytest.raises(PydanticValidationError, match="entity ID"):
        EntityFrontmatter.model_validate(fixture)


def test_reference_serialization_preserves_declared_field_order() -> None:
    fixture = load_fixture("positive", "entity")
    references = fixture["references"]
    if not isinstance(references, list) or not isinstance(references[0], dict):
        raise TypeError("Unexpected positive entity reference fixture")
    references[0].update(
        {
            "source_observations": ["OBS-fictional-001"],
            "note": "Fictional relationship.",
        }
    )

    dumped = EntityFrontmatter.model_validate(fixture).model_dump(mode="json")
    dumped_references = dumped["references"]
    if not isinstance(dumped_references, list) or not isinstance(dumped_references[0], dict):
        raise TypeError("Unexpected serialized entity references")

    assert list(dumped_references[0]) == [
        "relation",
        "target",
        "confidence",
        "source_observations",
        "valid_from",
        "valid_to",
        "note",
    ]
    validator_for("entity.schema.json").validate(dumped)
