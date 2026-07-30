from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).parents[2]
SCHEMA_ROOT = ROOT / "schemas"
FIXTURE_ROOT = Path(__file__).parent / "fixtures"
RFC3339_FORMAT_CHECKER = FormatChecker()
_RFC3339_DATE_TIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})$"
)


@RFC3339_FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if _RFC3339_DATE_TIME.fullmatch(value) is None:
        return False
    normalized = f"{value[:-1]}+00:00" if value[-1] in {"Z", "z"} else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def load_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return data


def load_schemas() -> dict[str, dict[str, object]]:
    return {path.name: load_json(path) for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))}


def build_registry(schemas: dict[str, dict[str, object]] | None = None) -> Registry:
    loaded = schemas or load_schemas()
    return Registry().with_resources(
        (str(schema["$id"]), Resource.from_contents(schema)) for schema in loaded.values()
    )


def validator_for(schema_name: str) -> Draft202012Validator:
    schemas = load_schemas()
    return Draft202012Validator(
        schemas[schema_name],
        registry=build_registry(schemas),
        format_checker=RFC3339_FORMAT_CHECKER,
    )


def load_fixture(group: str, name: str) -> dict[str, object]:
    return load_json(FIXTURE_ROOT / group / f"{name}.json")
