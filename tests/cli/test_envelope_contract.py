import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from workctx.presentation.envelope import CliEnvelope

ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "envelope"
POSITIVE_FIXTURES = sorted((FIXTURE_ROOT / "positive").glob("*.json"))
NEGATIVE_FIXTURES = sorted((FIXTURE_ROOT / "negative").glob("*.json"))
SCHEMA = json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def _load(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value


def test_fixture_sets_are_not_empty() -> None:
    assert POSITIVE_FIXTURES
    assert NEGATIVE_FIXTURES


@pytest.mark.parametrize("path", POSITIVE_FIXTURES, ids=lambda path: path.stem)
def test_positive_fixtures_validate_and_round_trip(path: Path) -> None:
    payload = _load(path)
    VALIDATOR.validate(payload)

    envelope = CliEnvelope.model_validate(payload)
    round_tripped = json.loads(envelope.model_dump_json())
    VALIDATOR.validate(round_tripped)


@pytest.mark.parametrize("path", NEGATIVE_FIXTURES, ids=lambda path: path.stem)
def test_negative_fixtures_are_rejected_by_schema_and_model(path: Path) -> None:
    payload = _load(path)
    assert list(VALIDATOR.iter_errors(payload))
    with pytest.raises(ValidationError):
        CliEnvelope.model_validate(payload)
