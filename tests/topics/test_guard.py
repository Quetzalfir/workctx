from __future__ import annotations

from datetime import UTC, datetime

import pytest

from workctx.domain import Topic, TopicRegistry, TopicStatus, slugify_topic
from workctx.topics import DuplicateRule, find_duplicate_topics, normalize_subject

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _topic(
    slug: str = "banorte",
    name: str = "Banorte",
    *,
    aliases: list[str] | None = None,
) -> Topic:
    return Topic(
        slug=slug,
        name=name,
        scope="Fictional financial-services account.",
        aliases=[] if aliases is None else aliases,
        status=TopicStatus.ACTIVE,
        created_at=NOW,
    )


def _matches(candidate: str, existing: Topic | None = None) -> tuple[str, ...]:
    registry = TopicRegistry(schema_version=1, topics=[existing or _topic()])
    return tuple(
        match.topic_slug
        for match in find_duplicate_topics(candidate, slugify_topic(candidate), registry)
    )


@pytest.mark.parametrize(
    "candidate",
    (
        "Banorte",
        "BANORTE",
        "Bánorte",
        "Banorte!",
        "The Banorte",
        "El Banorte",
        "Banorte Test",
        "Banorte Test 2",
        "Banorte Prueba",
        "Banorte Pruebas 17",
        "Banorte POC",
        "Banorte Demo",
        "Banorte v2",
        "Banorte version 23",
        "Banorte testing run 4",
        "Banorte Pilot 2026-10-14",
        "Banorte October 2026",
        "Banorte octubre 2026",
        "Banco de Banorte",
        "Banorte Migration API",
        "API Migration Banorte",
        "Proyecto: Banorte",
        "Banort",
        "Banortee",
    ),
)
def test_guard_matches_banorte_family(candidate: str) -> None:
    assert _matches(candidate) == ("banorte",)


@pytest.mark.parametrize(
    ("candidate", "existing"),
    (
        ("Santander", _topic()),
        ("Banregio", _topic()),
        ("Payments API", _topic("payroll-api", "Payroll API")),
        ("Customer Portal", _topic("supplier-portal", "Supplier Portal")),
        ("North Bank", _topic("south-bank", "South Bank")),
        ("Alpha", _topic("alpine", "Alpine")),
        ("Foo", _topic("food", "Food")),
        ("Mayo Clinic", _topic("october-health", "October Health")),
        ("October 2026", _topic("november-2026", "November 2026")),
        ("Data Lake", _topic("lake-house", "Lake House")),
        ("Mobile App", _topic("mobile-network", "Mobile Network")),
        ("Risk Review", _topic("security-review", "Security Review")),
    ),
)
def test_guard_preserves_clear_true_negatives(candidate: str, existing: Topic) -> None:
    assert _matches(candidate, existing) == ()


def test_guard_matches_registered_alias_and_reports_canonical_topic() -> None:
    existing = _topic(aliases=["Banco del Norte"])
    registry = TopicRegistry(schema_version=1, topics=[existing])

    matches = find_duplicate_topics("Banco Norte", "banco-norte", registry)

    assert len(matches) == 1
    assert matches[0].topic_slug == "banorte"
    assert matches[0].rule is DuplicateRule.EXACT


def test_token_subset_rule_is_reported_before_edit_distance() -> None:
    registry = TopicRegistry(schema_version=1, topics=[_topic()])

    match = find_duplicate_topics(
        "Banorte Migration API",
        "banorte-migration-api",
        registry,
    )[0]

    assert match.rule is DuplicateRule.TOKEN_SUBSET


@pytest.mark.parametrize(
    "value",
    (
        "Test 2",
        "Prueba 2026-10-14",
        "POC v2",
        "Demo Run 17",
    ),
)
def test_normalization_removes_instance_only_names(value: str) -> None:
    assert normalize_subject(value).tokens == ()


def test_month_word_without_numeric_date_context_is_preserved() -> None:
    assert normalize_subject("Mayo Clinic").tokens == ("clinic", "mayo")
