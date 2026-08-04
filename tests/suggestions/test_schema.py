from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry, Resource

from workctx.suggestions import SuggestionRecord

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
SCHEMA_ROOT = Path(__file__).parents[2] / "schemas"


def _fixture(group: str, name: str) -> dict[str, object]:
    payload = json.loads((FIXTURE_ROOT / group / f"{name}.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Suggestion fixtures must be JSON objects")
    return payload


def _validator() -> Draft202012Validator:
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        (str(schema["$id"]), Resource.from_contents(schema)) for schema in schemas.values()
    )
    return Draft202012Validator(schemas["suggestion-record.schema.json"], registry=registry)


def test_positive_suggestion_fixture_round_trips_through_schema_and_model() -> None:
    payload = _fixture("positive", "suggestion-record")
    validator = _validator()

    validator.validate(payload)
    record = SuggestionRecord.model_validate(payload)
    dumped = record.model_dump(mode="json")

    validator.validate(dumped)
    assert SuggestionRecord.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize(
    "fixture_name",
    [
        "suggestion-record-data-fix-approval",
        "suggestion-record-data-fix-proposal",
        "suggestion-record-id",
        "suggestion-record-numeric-created-at",
    ],
)
def test_negative_suggestion_fixtures_are_rejected_by_schema_and_model(
    fixture_name: str,
) -> None:
    payload = _fixture("negative", fixture_name)
    validator = _validator()

    with pytest.raises(JsonSchemaValidationError):
        validator.validate(payload)
    with pytest.raises(PydanticValidationError):
        SuggestionRecord.model_validate(payload)


def test_id_date_alignment_is_a_documented_model_only_invariant() -> None:
    payload = _fixture("positive", "suggestion-record")
    payload["created_at"] = "2026-08-04T12:00:00Z"
    payload["updated_at"] = "2026-08-04T12:00:00Z"

    _validator().validate(payload)
    with pytest.raises(PydanticValidationError, match="ID date"):
        SuggestionRecord.model_validate(payload)


def test_timestamp_order_is_a_documented_model_only_invariant() -> None:
    payload = _fixture("positive", "suggestion-record")
    payload["updated_at"] = "2026-08-02T12:00:00Z"

    _validator().validate(payload)
    with pytest.raises(PydanticValidationError, match="cannot precede"):
        SuggestionRecord.model_validate(payload)


def test_data_fix_context_alignment_is_a_documented_model_only_invariant() -> None:
    payload = _fixture("positive", "suggestion-record")
    payload["uri"] = (
        "workctx://other-fictional-context/investigation/SUG-20260803-fix-project-title-01"
    )

    _validator().validate(payload)
    with pytest.raises(PydanticValidationError, match="proposal context"):
        SuggestionRecord.model_validate(payload)


def test_data_fix_actor_alignment_is_a_documented_model_only_invariant() -> None:
    payload = _fixture("positive", "suggestion-record")
    payload["actor"] = {
        "type": "human",
        "id": "other-fictional-operator",
        "agent": None,
        "model": None,
    }

    _validator().validate(payload)
    with pytest.raises(PydanticValidationError, match="actor"):
        SuggestionRecord.model_validate(payload)


def test_supersession_self_link_is_a_documented_model_only_invariant() -> None:
    payload = _fixture("positive", "suggestion-record")
    payload["supersedes"] = payload["id"]

    _validator().validate(payload)
    with pytest.raises(PydanticValidationError, match="self-referential"):
        SuggestionRecord.model_validate(payload)
