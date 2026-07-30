from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from referencing.exceptions import Unresolvable

from workctx.domain.entities import EntityFrontmatter
from workctx.domain.tasks import Task
from workctx.services.contexts import initialize_context

from .schema_support import (
    RFC3339_FORMAT_CHECKER,
    ROOT,
    build_registry,
    load_schemas,
    validator_for,
)

CANONICAL_TEMPLATE = ROOT / "src" / "workctx" / "resources" / "context_template"
FRONTMATTER_SCHEMAS = {
    "claim.md": "claim.schema.json",
    "decision.md": "entity.schema.json",
    "evidence.md": "entity.schema.json",
    "person.md": "entity.schema.json",
    "question.md": "entity.schema.json",
    "risk.md": "entity.schema.json",
    "task.md": "task.schema.json",
}


def _load_yaml_object(path: Path) -> dict[str, object]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a YAML object in {path}")
    return data


def _load_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    opening, separator, remainder = text.partition("---\n")
    if opening or not separator:
        raise ValueError(f"Missing opening frontmatter delimiter in {path}")
    frontmatter, separator, _body = remainder.partition("\n---\n")
    if not separator:
        raise ValueError(f"Missing closing frontmatter delimiter in {path}")
    data = yaml.safe_load(frontmatter)
    if not isinstance(data, dict):
        raise TypeError(f"Expected frontmatter object in {path}")
    return data


def test_canonical_context_configuration_validates() -> None:
    context = _load_yaml_object(CANONICAL_TEMPLATE / "context.yaml")

    validator_for("context.schema.json").validate(context)


def test_every_canonical_frontmatter_template_validates() -> None:
    template_root = CANONICAL_TEMPLATE / "99_meta" / "templates"
    discovered = {path.name for path in template_root.glob("*.md")}
    assert discovered == set(FRONTMATTER_SCHEMAS)

    for filename, schema_name in FRONTMATTER_SCHEMAS.items():
        validator_for(schema_name).validate(_load_frontmatter(template_root / filename))


def test_typed_frontmatter_templates_follow_canonical_model_order() -> None:
    template_root = CANONICAL_TEMPLATE / "99_meta" / "templates"
    for filename in sorted(set(FRONTMATTER_SCHEMAS) - {"claim.md"}):
        payload = _load_frontmatter(template_root / filename)
        model_type = Task if filename == "task.md" else EntityFrontmatter
        dumped = model_type.model_validate(payload).model_dump(mode="json")

        assert payload == dumped
        assert list(payload) == list(dumped)


def test_initialized_context_and_frontmatter_templates_remain_valid(tmp_path: Path) -> None:
    context_root = tmp_path / "rendered-context"
    initialize_context(
        context_root,
        name="Rendered Fictional Context",
        context_id="rendered-fictional-context",
    )

    validator_for("context.schema.json").validate(_load_yaml_object(context_root / "context.yaml"))
    template_root = context_root / "99_meta" / "templates"
    for filename, schema_name in FRONTMATTER_SCHEMAS.items():
        validator_for(schema_name).validate(_load_frontmatter(template_root / filename))


def test_template_contract_rejects_a_missing_required_timestamp() -> None:
    context = _load_yaml_object(CANONICAL_TEMPLATE / "context.yaml")
    del context["created_at"]

    with pytest.raises(JsonSchemaValidationError):
        validator_for("context.schema.json").validate(context)


def test_broken_cross_schema_reference_is_not_silently_accepted() -> None:
    schemas = load_schemas()
    broken_task_schema = deepcopy(schemas["task.schema.json"])
    all_of = broken_task_schema["allOf"]
    if not isinstance(all_of, list) or not isinstance(all_of[0], dict):
        raise TypeError("Unexpected task schema composition")
    all_of[0]["$ref"] = "missing-entity.schema.json"
    task = _load_frontmatter(CANONICAL_TEMPLATE / "99_meta" / "templates" / "task.md")
    validator = Draft202012Validator(
        broken_task_schema,
        registry=build_registry(schemas),
        format_checker=RFC3339_FORMAT_CHECKER,
    )

    with pytest.raises(Unresolvable):
        validator.validate(task)
