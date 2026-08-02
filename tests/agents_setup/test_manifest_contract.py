from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

from workctx.adapters.agents.manifest import (
    AdapterManifest,
    dump_manifest,
    load_manifest,
    source_set_aggregate_hash,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "manifest"


def _validator() -> Draft202012Validator:
    schema = json.loads(
        (ROOT / "schemas" / "skill-adapter-manifest.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _fixture_paths(category: str) -> list[Path]:
    return sorted((FIXTURES / category).glob("*.json"))


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths("positive"),
    ids=lambda path: path.stem,
)
def test_positive_manifest_fixtures_validate_with_schema_and_model(
    fixture_path: Path,
) -> None:
    raw = fixture_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    _validator().validate(payload)
    manifest = load_manifest(raw)

    assert manifest.model_dump(mode="json", exclude_none=True) == payload


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths("negative_structural"),
    ids=lambda path: path.stem,
)
def test_structural_negative_fixtures_are_rejected_by_schema_and_model(
    fixture_path: Path,
) -> None:
    raw = fixture_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    with pytest.raises(JsonSchemaValidationError):
        _validator().validate(payload)
    with pytest.raises(PydanticValidationError):
        load_manifest(raw)


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths("negative_producer"),
    ids=lambda path: path.stem,
)
def test_producer_invariant_fixtures_are_schema_valid_but_model_rejected(
    fixture_path: Path,
) -> None:
    raw = fixture_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    _validator().validate(payload)
    with pytest.raises(PydanticValidationError):
        load_manifest(raw)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "modern-generated.json",
        "modern-native-verified.json",
        "modern-native-verified-resources.json",
        "modern-with-backup.json",
    ],
)
def test_modern_manifest_serialization_is_deterministic(fixture_name: str) -> None:
    fixture_path = FIXTURES / "positive" / fixture_name
    raw = fixture_path.read_text(encoding="utf-8")
    manifest = load_manifest(raw)

    assert dump_manifest(manifest) == raw
    assert dump_manifest(manifest).endswith("\n")
    assert "\r" not in dump_manifest(manifest)


def test_native_source_set_hash_uses_exact_domain_separated_framing() -> None:
    files = [
        (
            ".agents/skills/bootstrap-session/SKILL.md",
            "sha256:" + "1" * 64,
        )
    ]

    assert source_set_aggregate_hash(files) == (
        "sha256:6bf796b247c93a7d3d114c8fa516ed192d3fdfc377d2c1cc3542e9aa7651d414"
    )


def test_native_source_set_hash_and_serialization_are_order_independent() -> None:
    fixture_path = FIXTURES / "positive" / "modern-native-verified-resources.json"
    raw = fixture_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    source_set = payload["skills"][0]["source_set"]
    source_set["files"].reverse()

    manifest = AdapterManifest.model_validate(payload)

    assert dump_manifest(manifest) == raw
    assert (
        source_set_aggregate_hash(
            (item["path"], item["content_hash"]) for item in source_set["files"]
        )
        == source_set["aggregate_hash"]
    )


def test_native_entry_does_not_change_generated_adapter_prefix_validation() -> None:
    payload = json.loads(
        (FIXTURES / "positive" / "modern-native-verified.json").read_text(encoding="utf-8")
    )
    payload["skills"].append(
        {
            "name": "close-session",
            "mode": "generated",
            "canonical": {
                "path": ".agents/skills/close-session/SKILL.md",
                "content_hash": "sha256:" + "5" * 64,
            },
            "generated": [
                {
                    "path": ".codex/skills/close-session/SKILL.md",
                    "content_hash": "sha256:" + "6" * 64,
                }
            ],
        }
    )

    AdapterManifest.model_validate(payload)


def test_serialization_sorts_skills_and_generated_files() -> None:
    payload = json.loads(
        (FIXTURES / "positive" / "modern-generated.json").read_text(encoding="utf-8")
    )
    first_skill = payload["skills"][0]
    first_skill["generated"].insert(
        0,
        {
            "path": ".claude/skills/bootstrap-session/README.md",
            "content_hash": "sha256:" + "6" * 64,
        },
    )
    payload["skills"].insert(
        0,
        {
            "name": "close-session",
            "mode": "generated",
            "canonical": {
                "path": ".agents/skills/close-session/SKILL.md",
                "content_hash": "sha256:" + "7" * 64,
            },
            "generated": [
                {
                    "path": ".claude/skills/close-session/SKILL.md",
                    "content_hash": "sha256:" + "8" * 64,
                }
            ],
        },
    )
    manifest = AdapterManifest.model_validate(payload)

    dumped = json.loads(dump_manifest(manifest))

    assert [skill["name"] for skill in dumped["skills"]] == [
        "bootstrap-session",
        "close-session",
    ]
    assert [item["path"] for item in dumped["skills"][0]["generated"]] == [
        ".claude/skills/bootstrap-session/README.md",
        ".claude/skills/bootstrap-session/SKILL.md",
    ]


def test_legacy_manifest_is_readable_but_cannot_be_emitted_by_new_producer() -> None:
    raw = (FIXTURES / "positive" / "legacy-generated.json").read_text(encoding="utf-8")
    manifest = load_manifest(raw)

    assert manifest.skills[0].effective_mode == "generated"
    assert manifest.components is None
    assert manifest.backups is None
    with pytest.raises(ValueError, match="explicit skill modes"):
        dump_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("adapter_version", True),
        ("schema_version", "1"),
        ("backups", None),
    ],
)
def test_strict_types_and_explicit_null_are_rejected_by_both_contracts(
    field: str, value: object
) -> None:
    payload = json.loads(
        (FIXTURES / "positive" / "modern-generated.json").read_text(encoding="utf-8")
    )
    payload[field] = value

    with pytest.raises(JsonSchemaValidationError):
        _validator().validate(payload)
    with pytest.raises(PydanticValidationError):
        AdapterManifest.model_validate(payload)


def test_first_documented_manifest_example_validates_and_round_trips() -> None:
    document = (ROOT / "docs" / "reference" / "skill-adapters.md").read_text(encoding="utf-8")
    example = document.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    payload = json.loads(example)

    _validator().validate(payload)
    manifest = AdapterManifest.model_validate(payload)

    assert json.loads(dump_manifest(manifest)) == payload
