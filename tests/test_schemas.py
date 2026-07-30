import json
from pathlib import Path

from jsonschema import Draft202012Validator


SCHEMA_ROOT = Path(__file__).parents[1] / "schemas"


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
