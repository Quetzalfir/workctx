from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from workctx.domain import EntityFrontmatter, Topic, TopicRegistry, TopicStatus
from workctx.services.contexts import initialize_context
from workctx.validation import Severity, validate_workspace

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).parents[2]


def _topic(
    slug: str,
    *,
    aliases: list[str] | None = None,
    status: TopicStatus = TopicStatus.ACTIVE,
    merged_into: str | None = None,
) -> Topic:
    return Topic(
        slug=slug,
        name=slug.replace("-", " ").title(),
        scope=f"Fictional scope for {slug}.",
        aliases=[] if aliases is None else aliases,
        status=status,
        merged_into=merged_into,
        created_at=NOW,
    )


def _write_registry(root: Path, registry: object) -> None:
    (root / "99_meta" / "topics.yaml").write_text(
        yaml.safe_dump(registry, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )


def _write_entity(root: Path, topic: str) -> Path:
    payload = {
        "schema_version": 1,
        "id": "PRJ-topic-validation",
        "entity_type": "project",
        "title": "Topic validation fixture",
        "uri": "workctx://topic-validation/project/PRJ-topic-validation",
        "aliases": [],
        "status": "active",
        "confidence": "high",
        "tags": [],
        "topics": [topic],
        "references": [],
        "created_at": "2026-10-14T12:00:00Z",
        "updated_at": "2026-10-14T12:00:00Z",
    }
    frontmatter = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    path = root / "02_knowledge" / "PRJ-topic-validation.md"
    path.write_text(f"---\n{frontmatter}---\n\nFixture.\n", encoding="utf-8")
    return path


def test_topic_registry_schema_and_model_align() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "topic-registry.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    registry = TopicRegistry(
        schema_version=1,
        topics=[
            _topic("banorte", aliases=["banco-norte"]),
            _topic("legacy", status=TopicStatus.MERGED, merged_into="banorte"),
        ],
    )
    payload = registry.model_dump(mode="json", exclude_none=True)

    validator.validate(payload)
    assert TopicRegistry.model_validate(payload) == registry


@pytest.mark.parametrize(
    "topics",
    (
        [_topic("one", aliases=["shared"]), _topic("two", aliases=["shared"])],
        [_topic("one", aliases=["two"]), _topic("two")],
        [_topic("legacy", status=TopicStatus.MERGED, merged_into="missing")],
    ),
)
def test_registry_rejects_cross_entry_integrity_failures(topics: list[Topic]) -> None:
    with pytest.raises(ValidationError):
        TopicRegistry(schema_version=1, topics=topics)


def test_entity_topics_are_optional_unique_kebab_slugs() -> None:
    base = {
        "schema_version": 1,
        "id": "PRJ-topics",
        "entity_type": "project",
        "title": "Topics",
        "uri": "workctx://topic-validation/project/PRJ-topics",
        "created_at": NOW,
        "updated_at": NOW,
    }

    assert EntityFrontmatter.model_validate(base).topics == []
    assert EntityFrontmatter.model_validate({**base, "topics": ["one-topic"]}).topics == [
        "one-topic"
    ]
    with pytest.raises(ValidationError):
        EntityFrontmatter.model_validate({**base, "topics": ["Not Kebab"]})
    with pytest.raises(ValidationError):
        EntityFrontmatter.model_validate({**base, "topics": ["one", "one"]})


def test_existing_context_without_registry_remains_valid(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Validation", context_id="topic-validation")

    report = validate_workspace(root)

    assert not {issue.code for issue in report.issues} & {
        "TOPIC-REGISTRY",
        "TOPIC-UNKNOWN",
        "TOPIC-MERGED",
    }


def test_validation_accepts_alias_and_reports_unknown_and_merged_values(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Validation", context_id="topic-validation")
    registry = TopicRegistry(
        schema_version=1,
        topics=[
            _topic("banorte", aliases=["banco-norte"]),
            _topic("legacy", status=TopicStatus.MERGED, merged_into="banorte"),
        ],
    )
    _write_registry(root, registry.model_dump(mode="json", exclude_none=True))

    _write_entity(root, "banco-norte")
    assert "TOPIC-UNKNOWN" not in {issue.code for issue in validate_workspace(root).issues}

    _write_entity(root, "legacy")
    merged = [issue for issue in validate_workspace(root).issues if issue.code == "TOPIC-MERGED"]
    assert len(merged) == 1
    assert merged[0].severity is Severity.WARNING
    assert merged[0].repair_action == "Retag 'legacy' to 'banorte'."

    _write_entity(root, "unknown")
    unknown = [issue for issue in validate_workspace(root).issues if issue.code == "TOPIC-UNKNOWN"]
    assert len(unknown) == 1
    assert unknown[0].severity is Severity.ERROR
    assert "workctx topic list" in unknown[0].repair_action


def test_validation_reports_registry_integrity_error(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic Validation", context_id="topic-validation")
    _write_registry(
        root,
        {
            "schema_version": 1,
            "topics": [
                {
                    "slug": "one",
                    "name": "One",
                    "scope": "One scope.",
                    "aliases": ["shared"],
                    "status": "active",
                    "created_at": "2026-10-14T12:00:00Z",
                },
                {
                    "slug": "two",
                    "name": "Two",
                    "scope": "Two scope.",
                    "aliases": ["shared"],
                    "status": "active",
                    "created_at": "2026-10-14T12:00:00Z",
                },
            ],
        },
    )

    issues = [issue for issue in validate_workspace(root).issues if issue.code == "TOPIC-REGISTRY"]

    assert len(issues) == 1
    assert issues[0].severity is Severity.ERROR
