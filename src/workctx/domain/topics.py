"""Canonical topic registry models and deterministic resolution semantics."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

TOPIC_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
TOPICS_REGISTRY_PATH = "99_meta/topics.yaml"

TopicSlug = Annotated[str, Field(pattern=TOPIC_SLUG_PATTERN, min_length=1, max_length=100)]


class TopicStatus(StrEnum):
    """Lifecycle state for one canonical topic."""

    ACTIVE = "active"
    MERGED = "merged"
    ARCHIVED = "archived"


class TopicHeatBucket(StrEnum):
    """Computed topic-activity band, ordered from most to least active."""

    HOT = "hot"
    WARM = "warm"
    COOL = "cool"
    COLD = "cold"


class TopicHeat(BaseModel):
    """One computed topic heat result; this record is never authored canonically."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: TopicSlug
    name: str = Field(min_length=1, max_length=200)
    score: float = Field(ge=0)
    heat: TopicHeatBucket
    last_activity: AwareDatetime | None


class TopicDistinction(BaseModel):
    """Operator-reviewed reason that two guard matches are distinct subjects."""

    model_config = ConfigDict(extra="forbid")

    slug: TopicSlug
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def require_single_line_reason(cls, value: str) -> str:
        return _single_line(value, "distinct topic reason")


class Topic(BaseModel):
    """One durable subject in a context-local topic registry."""

    model_config = ConfigDict(extra="forbid")

    slug: TopicSlug
    name: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=500)
    aliases: list[str]
    status: TopicStatus
    merged_into: TopicSlug | None = None
    created_at: AwareDatetime
    distinct_from: list[TopicDistinction] = Field(default_factory=list)

    @field_validator("name", "scope")
    @classmethod
    def require_single_line_text(cls, value: str) -> str:
        return _single_line(value, "topic text")

    @field_validator("aliases")
    @classmethod
    def require_valid_aliases(cls, values: list[str]) -> list[str]:
        for value in values:
            _single_line(value, "topic alias")
            if not value:
                raise ValueError("topic aliases must not be empty")
        normalized = [topic_lookup_key(value) for value in values]
        if any(not value for value in normalized):
            raise ValueError("topic aliases must contain letters or numbers")
        if len(normalized) != len(set(normalized)):
            raise ValueError("topic aliases must be unique after normalization")
        return values

    @field_validator("created_at", mode="before")
    @classmethod
    def reject_numeric_timestamp(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("created_at must be an RFC 3339 string or datetime value")
        return value

    @field_validator("distinct_from")
    @classmethod
    def require_unique_distinctions(cls, values: list[TopicDistinction]) -> list[TopicDistinction]:
        slugs = [value.slug for value in values]
        if len(slugs) != len(set(slugs)):
            raise ValueError("distinct_from slugs must be unique")
        return values

    @model_validator(mode="after")
    def require_lifecycle_shape(self) -> Self:
        if self.status is TopicStatus.MERGED:
            if self.merged_into is None:
                raise ValueError("merged topics require merged_into")
            if self.merged_into == self.slug:
                raise ValueError("a topic cannot merge into itself")
        elif self.merged_into is not None:
            raise ValueError("only merged topics may declare merged_into")
        if any(note.slug == self.slug for note in self.distinct_from):
            raise ValueError("a topic cannot be distinct from itself")
        return self


class TopicResolution(BaseModel):
    """Resolution result retaining whether a merged spelling was used."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: str
    topic: Topic
    matched_by: str
    merged_from: TopicSlug | None = None


class TopicRegistry(BaseModel):
    """Versioned canonical registry with cross-entry producer invariants."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(strict=True)
    topics: list[Topic]

    @property
    def id(self) -> str:
        """Stable transaction filename identity; not part of registry serialization."""

        return "topics"

    @field_validator("schema_version")
    @classmethod
    def require_schema_version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version must be 1")
        return value

    @model_validator(mode="after")
    def require_registry_integrity(self) -> Self:
        by_slug = {topic.slug: topic for topic in self.topics}
        if len(by_slug) != len(self.topics):
            raise ValueError("topic slugs must be unique")

        alias_owners: dict[str, str] = {}
        slug_keys = {topic_lookup_key(topic.slug): topic for topic in self.topics}
        if len(slug_keys) != len(self.topics):
            raise ValueError("topic slugs must be unique after normalization")

        for topic in self.topics:
            own_slug_key = topic_lookup_key(topic.slug)
            for alias in topic.aliases:
                alias_key = topic_lookup_key(alias)
                if alias_key == own_slug_key:
                    raise ValueError("a topic alias cannot equal its own slug")
                slug_owner = slug_keys.get(alias_key)
                if slug_owner is not None:
                    allowed_merged_alias = (
                        slug_owner.status is TopicStatus.MERGED
                        and slug_owner.merged_into == topic.slug
                    )
                    if not allowed_merged_alias:
                        raise ValueError("a topic alias cannot equal another topic slug")
                previous = alias_owners.get(alias_key)
                if previous is not None and previous != topic.slug:
                    raise ValueError("topic aliases must be globally unique")
                alias_owners[alias_key] = topic.slug

            if topic.status is TopicStatus.MERGED:
                target = by_slug.get(topic.merged_into or "")
                if target is None:
                    raise ValueError("merged_into must identify an existing topic")
                if target.status is not TopicStatus.ACTIVE:
                    raise ValueError("merged_into must identify an active topic")
            for note in topic.distinct_from:
                if note.slug not in by_slug:
                    raise ValueError("distinct_from must identify an existing topic")
        return self

    def get(self, slug: str) -> Topic | None:
        """Return one entry by exact canonical slug."""

        return next((topic for topic in self.topics if topic.slug == slug), None)

    def resolve(self, value: str) -> TopicResolution | None:
        """Resolve a canonical slug or normalized alias, preserving merged spellings."""

        exact = self.get(value)
        if exact is not None:
            if exact.status is TopicStatus.MERGED:
                target = self.get(exact.merged_into or "")
                if target is None:  # pragma: no cover - registry invariant
                    return None
                return TopicResolution(
                    requested=value,
                    topic=target,
                    matched_by="slug",
                    merged_from=exact.slug,
                )
            if exact.status is TopicStatus.ACTIVE:
                return TopicResolution(requested=value, topic=exact, matched_by="slug")
            return None

        key = topic_lookup_key(value)
        for topic in self.topics:
            if topic.status is not TopicStatus.ACTIVE:
                continue
            if any(topic_lookup_key(alias) == key for alias in topic.aliases):
                return TopicResolution(requested=value, topic=topic, matched_by="alias")
        return None

    def active_topics(self) -> tuple[Topic, ...]:
        return tuple(topic for topic in self.topics if topic.status is TopicStatus.ACTIVE)


def topic_lookup_key(value: str) -> str:
    """Normalize spelling for deterministic slug/alias identity comparisons."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return "".join(re.findall(r"[a-z0-9]+", without_marks))


def slugify_topic(value: str) -> str:
    """Create one portable kebab-case topic slug from a display name."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", without_marks).strip("-")
    return slug[:100].rstrip("-")


def _single_line(value: str, label: str) -> str:
    if value != value.strip() or any(character in value for character in "\r\n"):
        raise ValueError(f"{label} must be trimmed single-line text")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must be printable single-line text")
    return value


__all__ = [
    "TOPICS_REGISTRY_PATH",
    "TOPIC_SLUG_PATTERN",
    "Topic",
    "TopicDistinction",
    "TopicHeat",
    "TopicHeatBucket",
    "TopicRegistry",
    "TopicResolution",
    "TopicSlug",
    "TopicStatus",
    "slugify_topic",
    "topic_lookup_key",
]
