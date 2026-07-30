import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import TypeAdapter, ValidationError
from referencing import Registry, Resource

from workctx.domain.claims import Claim
from workctx.domain.locators import SOURCE_LOCATOR_ADAPTER
from workctx.domain.observations import Observation
from workctx.domain.relations import TypedReference

ROOT = Path(__file__).parents[2]
SCHEMA_ROOT = ROOT / "schemas"
FIXTURE_ROOT = Path(__file__).parent / "fixtures"

SCHEMA_FILES = {
    "reference": "reference.schema.json",
    "source-locator": "source-locator.schema.json",
    "observation": "observation.schema.json",
    "claim": "claim.schema.json",
}
ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "reference": TypeAdapter(TypedReference),
    "source-locator": SOURCE_LOCATOR_ADAPTER,
    "observation": TypeAdapter(Observation),
    "claim": TypeAdapter(Claim),
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS: dict[str, dict[str, Any]] = {
    name: _load_json(SCHEMA_ROOT / filename) for name, filename in SCHEMA_FILES.items()
}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(SCHEMAS[name], registry=REGISTRY, format_checker=FormatChecker())


@pytest.mark.parametrize("contract_name", tuple(SCHEMA_FILES))
def test_positive_fixtures_round_trip_through_schema_and_model(contract_name: str) -> None:
    fixtures = _load_json(FIXTURE_ROOT / f"{contract_name}.json")
    validator = _validator(contract_name)
    adapter = ADAPTERS[contract_name]

    for data in fixtures["positive"]:
        validator.validate(data)
        model = adapter.validate_python(data)
        default_dump = adapter.dump_python(model, mode="json")
        validator.validate(default_dump)
        adapter.validate_python(default_dump)
        dumped = adapter.dump_python(model, mode="json", exclude_unset=True)
        validator.validate(dumped)
        adapter.validate_python(dumped)


NEGATIVE_CASES = [
    pytest.param(contract_name, case["data"], id=f"{contract_name}-{case['name']}")
    for contract_name in SCHEMA_FILES
    for case in _load_json(FIXTURE_ROOT / f"{contract_name}.json")["negative"]
]


@pytest.mark.parametrize(("contract_name", "data"), NEGATIVE_CASES)
def test_negative_fixtures_are_rejected_by_schema_and_model(
    contract_name: str, data: dict[str, Any]
) -> None:
    validator = _validator(contract_name)
    adapter = ADAPTERS[contract_name]

    assert not validator.is_valid(data)
    with pytest.raises(ValidationError):
        adapter.validate_python(data)
