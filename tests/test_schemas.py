import json
from pathlib import Path
from typing import get_args

from jsonschema import Draft202012Validator

from workctx.domain.entities import EntityType

SCHEMA_ROOT = Path(__file__).parents[1] / "schemas"
EXPECTED_ENTITY_TYPES = [
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


def test_all_json_schemas_are_valid_draft_2020_12() -> None:
    for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_operator_preference_examples_match_schema() -> None:
    import yaml

    root = Path(__file__).parents[1]
    schema = json.loads(
        (SCHEMA_ROOT / "operator-preferences.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    paths = [root / ".agents" / "operator.example.yaml"]
    local_path = root / ".agents" / "operator.local.yaml"
    if local_path.exists():
        paths.append(local_path)
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        validator.validate(data)


def test_entity_type_vocabulary_matches_decision_d018_literal_list() -> None:
    schema = json.loads((SCHEMA_ROOT / "entity.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["entity_type"]["enum"] == EXPECTED_ENTITY_TYPES
    assert list(get_args(EntityType)) == EXPECTED_ENTITY_TYPES


def test_context_schema_requires_creation_and_update_timestamps() -> None:
    schema = json.loads((SCHEMA_ROOT / "context.schema.json").read_text(encoding="utf-8"))

    assert "created_at" in schema["required"]
    assert "updated_at" in schema["required"]
