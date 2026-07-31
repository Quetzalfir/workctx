from __future__ import annotations

import json
from pathlib import Path

import pytest

from workctx.adapters.filesystem.serialization import (
    canonical_mapping_data,
    canonical_model_data,
    dump_yaml_bytes,
    has_hand_edits_markdown,
    has_hand_edits_yaml,
    load_json_model,
    load_markdown_model,
    load_yaml_model,
    render_markdown_bytes,
)
from workctx.domain.artifacts import ArtifactManifest
from workctx.domain.entities import EntityFrontmatter
from workctx.domain.tasks import Task
from workctx.models.context import ContextConfig

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_FIXTURES = ROOT / "tests" / "workspace" / "fixtures" / "positive"
GOLDEN = Path(__file__).parent / "fixtures" / "canonical-entity.md"


def _load_model[ModelT](filename: str, model_type: type[ModelT]) -> ModelT:
    payload = json.loads((WORKSPACE_FIXTURES / filename).read_text(encoding="utf-8"))
    return model_type.model_validate(payload)


def test_markdown_golden_file_pins_canonical_bytes() -> None:
    entity = _load_model("entity.json", EntityFrontmatter)

    first = render_markdown_bytes(entity, "Jordan owns the fictional service.")
    second = render_markdown_bytes(entity, "Jordan owns the fictional service.")

    assert first == second == GOLDEN.read_bytes()
    assert not first.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in first


def test_parsed_markdown_reserializes_byte_identically() -> None:
    golden = GOLDEN.read_bytes()
    document = load_markdown_model(golden, EntityFrontmatter)

    assert render_markdown_bytes(document.frontmatter, document.body) == golden
    assert not has_hand_edits_markdown(golden, EntityFrontmatter)


def test_declared_order_is_preserved_while_free_form_mappings_are_sorted() -> None:
    payload = json.loads((WORKSPACE_FIXTURES / "entity.json").read_text(encoding="utf-8"))
    payload["z_extra"] = {"z": 1, "a": {"z": 2, "a": 3}}
    payload["a_extra"] = "first extra"
    entity = EntityFrontmatter.model_validate(payload)

    data = canonical_model_data(entity)

    assert list(data)[:5] == ["schema_version", "id", "entity_type", "title", "uri"]
    assert list(data)[-2:] == ["a_extra", "z_extra"]
    assert list(data["z_extra"]) == ["a", "z"]
    assert list(data["z_extra"]["a"]) == ["a", "z"]


def test_schema_nullable_top_level_fields_emit_null_but_nested_non_nullable_fields_omit() -> None:
    payload = json.loads((WORKSPACE_FIXTURES / "entity.json").read_text(encoding="utf-8"))
    payload["status"] = None
    payload["confidence"] = None
    payload["references"] = [
        {
            "relation": "related_to",
            "target": "workctx://fictional-context/project/PRJ-sample",
        }
    ]
    entity = EntityFrontmatter.model_validate(payload)

    data = canonical_model_data(entity)
    reference = data["references"][0]

    assert data["status"] is None
    assert data["confidence"] is None
    assert "confidence" not in reference
    assert "note" not in reference
    assert reference["valid_from"] is None
    assert reference["valid_to"] is None


def test_task_and_artifact_optional_fields_follow_schema_nullability() -> None:
    task = _load_model("task.json", Task)
    task_data = canonical_model_data(task)
    task_reference = task_data["references"][0]
    assert task_data["requester"] is None
    assert task_data["due_at"] is None
    assert "confidence" not in task_reference
    assert "source_observations" not in task_reference
    assert "note" not in task_reference
    assert task_reference["valid_from"] is None
    assert task_reference["valid_to"] is None

    manifest = _load_model("artifact-manifest.json", ArtifactManifest).model_copy(
        update={
            "source_origin": None,
            "event_at": None,
            "language": None,
            "classification": None,
            "duplicate_of": None,
            "notes": None,
        }
    )
    manifest_data = canonical_model_data(manifest)
    for field_name in (
        "source_origin",
        "event_at",
        "language",
        "classification",
        "duplicate_of",
        "notes",
    ):
        assert field_name in manifest_data
        assert manifest_data[field_name] is None


@pytest.mark.parametrize("unordered", [{"alpha", "bravo"}, frozenset({"alpha", "bravo"})])
def test_model_preflight_rejects_unordered_extra_before_serialization(unordered: object) -> None:
    payload = json.loads((WORKSPACE_FIXTURES / "entity.json").read_text(encoding="utf-8"))
    payload["unordered_extra"] = unordered
    entity = EntityFrontmatter.model_validate(payload)

    for _attempt in range(2):
        with pytest.raises(TypeError, match="unordered set"):
            dump_yaml_bytes(entity)


def test_model_preflight_rejects_iterator_without_consuming_it() -> None:
    payload = json.loads((WORKSPACE_FIXTURES / "entity.json").read_text(encoding="utf-8"))
    iterator = iter(["alpha", "bravo"])
    payload["iterator_extra"] = iterator
    entity = EntityFrontmatter.model_validate(payload)

    for _attempt in range(2):
        with pytest.raises(TypeError, match="one-shot iterator"):
            dump_yaml_bytes(entity)
    assert list(iterator) == ["alpha", "bravo"]


@pytest.mark.parametrize(
    "mapping",
    [
        {1: "integer key", "1": "string key"},
        {"1": "string key", 1: "integer key"},
    ],
)
def test_model_preflight_rejects_mapping_keys_before_pydantic_can_coerce_them(
    mapping: dict[object, str],
) -> None:
    payload = json.loads((WORKSPACE_FIXTURES / "entity.json").read_text(encoding="utf-8"))
    payload["mapping_extra"] = mapping
    entity = EntityFrontmatter.model_validate(payload)

    with pytest.raises(TypeError, match="mapping keys must be strings"):
        dump_yaml_bytes(entity)


@pytest.mark.parametrize("value", [("tuple", "value"), b"bytes", float("nan")])
def test_free_form_preflight_rejects_non_json_native_values(value: object) -> None:
    with pytest.raises(TypeError, match=r"JSON-native|finite"):
        canonical_mapping_data({"value": value})


def test_declared_non_finite_number_is_rejected() -> None:
    entity = _load_model("entity.json", EntityFrontmatter).model_copy(
        update={"confidence": float("inf")}
    )

    with pytest.raises(TypeError, match="non-finite"):
        dump_yaml_bytes(entity)


def test_canonical_json_loader_rejects_duplicate_object_keys() -> None:
    with pytest.raises(ValueError, match="duplicate object key"):
        load_json_model(
            b'{"schema_version":1,"schema_version":1}\n',
            ArtifactManifest,
        )


def test_yaml_round_trip_is_identity_for_context_config() -> None:
    config = _load_model("context.json", ContextConfig)
    first = dump_yaml_bytes(config)
    reparsed = load_yaml_model(first, ContextConfig)

    assert dump_yaml_bytes(reparsed) == first
    assert not has_hand_edits_yaml(first, ContextConfig)


@pytest.mark.parametrize(
    "edited",
    [
        lambda raw: b"# hand note\n" + raw,
        lambda raw: raw.replace(b"\n", b"\r\n"),
        lambda raw: raw.replace(b"title: Jordan Lee\n", b"title: 'Jordan Lee'\n"),
        lambda raw: raw + b"\n",
    ],
)
def test_hand_edit_detection_flags_byte_drift(edited) -> None:
    golden = GOLDEN.read_bytes()
    changed = edited(golden)

    if changed.startswith(b"#"):
        with pytest.raises(ValueError, match="must start"):
            has_hand_edits_markdown(changed, EntityFrontmatter)
    else:
        assert has_hand_edits_markdown(changed, EntityFrontmatter)


def test_markdown_renderer_enforces_lf_and_one_structural_blank_line() -> None:
    entity = _load_model("entity.json", EntityFrontmatter)

    rendered = render_markdown_bytes(entity, "\r\n\r\nBody\r\n\r\n")

    assert b"\r" not in rendered
    assert rendered.endswith(b"---\n\nBody\n")
