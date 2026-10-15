"""Pure deterministic duplicate detection for durable topic subjects.

The edit-distance rule uses Levenshtein distance divided by the longer compact
normalized spelling. Values of at least five characters match at a ratio of
``0.20`` or less. Exact and token-subset matches take precedence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from workctx.domain.topics import Topic, TopicRegistry, TopicStatus

EDIT_DISTANCE_THRESHOLD = 0.20
_MIN_EDIT_LENGTH = 5
_TOKEN = re.compile(r"[a-z0-9]+")
_VERSION = re.compile(r"^(?:v|ver|version|versao|versione)[0-9]+$")
_QUALIFIED_RUN = re.compile(
    r"^(?:test|testing|tests|prueba|pruebas|poc|demo|demostracion|prototype|prototipo|"
    r"pilot|piloto)[0-9]*$"
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "de",
        "del",
        "el",
        "for",
        "la",
        "las",
        "los",
        "of",
        "para",
        "the",
        "y",
    }
)
_QUALIFIERS = frozenset(
    {
        "demo",
        "demostracion",
        "pilot",
        "piloto",
        "poc",
        "prototype",
        "prototipo",
        "prueba",
        "pruebas",
        "run",
        "runs",
        "test",
        "testing",
        "tests",
        "version",
    }
)
_MONTHS = frozenset(
    {
        "apr",
        "abril",
        "april",
        "ago",
        "agosto",
        "aug",
        "august",
        "dec",
        "december",
        "dic",
        "diciembre",
        "ene",
        "enero",
        "feb",
        "febrero",
        "february",
        "jan",
        "january",
        "jul",
        "julio",
        "july",
        "jun",
        "june",
        "junio",
        "mar",
        "march",
        "marzo",
        "may",
        "mayo",
        "nov",
        "november",
        "noviembre",
        "oct",
        "october",
        "octubre",
        "sep",
        "sept",
        "september",
        "septiembre",
    }
)


class DuplicateRule(StrEnum):
    """Ordered rules reported by the duplicate guard."""

    EXACT = "normalized_exact"
    TOKEN_SUBSET = "token_subset"
    EDIT_DISTANCE = "edit_distance"


@dataclass(frozen=True, slots=True)
class NormalizedSubject:
    """A spelling reduced to stable meaningful tokens."""

    tokens: tuple[str, ...]
    compact: str


@dataclass(frozen=True, slots=True)
class TopicDuplicateMatch:
    """One existing canonical topic matched by the pure guard."""

    topic_slug: str
    topic_name: str
    matched_value: str
    rule: DuplicateRule
    normalized_distance: float


def normalize_subject(value: str) -> NormalizedSubject:
    """Normalize accents, punctuation, stopwords, run qualifiers, versions, and dates."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    raw_tokens = _TOKEN.findall(ascii_text)
    has_numeric_date_fragment = any(token.isdigit() for token in raw_tokens)
    meaningful: set[str] = set()
    for token in raw_tokens:
        if token in _STOPWORDS or token in _QUALIFIERS:
            continue
        if has_numeric_date_fragment and token in _MONTHS:
            continue
        if token.isdigit() or _VERSION.fullmatch(token) or _QUALIFIED_RUN.fullmatch(token):
            continue
        meaningful.add(token)
    tokens = tuple(sorted(meaningful))
    return NormalizedSubject(tokens=tokens, compact="".join(tokens))


def find_duplicate_topics(
    candidate_name: str,
    candidate_slug: str,
    registry: TopicRegistry,
) -> tuple[TopicDuplicateMatch, ...]:
    """Return deterministic matches without filesystem, clock, locale, or model calls."""

    candidates = _unique_normalized_values((candidate_name, candidate_slug))
    if not candidates:
        return ()

    matches: dict[str, TopicDuplicateMatch] = {}
    for entry in registry.topics:
        canonical = _canonical_topic(entry, registry)
        existing_values = _unique_normalized_values(
            (entry.name, entry.slug, *entry.aliases, canonical.name, canonical.slug)
        )
        best: tuple[int, float, str, DuplicateRule] | None = None
        for _candidate_value, candidate in candidates:
            for existing_value, existing in existing_values:
                rule, distance = _compare(candidate, existing)
                if rule is None:
                    continue
                score = (_rule_order(rule), distance, existing_value, rule)
                if best is None or score < best:
                    best = score
        if best is None:
            continue
        _, distance, matched_value, rule = best
        match = TopicDuplicateMatch(
            topic_slug=canonical.slug,
            topic_name=canonical.name,
            matched_value=matched_value,
            rule=rule,
            normalized_distance=distance,
        )
        previous = matches.get(canonical.slug)
        if previous is None or (
            _rule_order(match.rule),
            match.normalized_distance,
            match.matched_value,
        ) < (
            _rule_order(previous.rule),
            previous.normalized_distance,
            previous.matched_value,
        ):
            matches[canonical.slug] = match
    return tuple(
        sorted(
            matches.values(),
            key=lambda item: (
                _rule_order(item.rule),
                item.normalized_distance,
                item.topic_slug,
            ),
        )
    )


def normalized_edit_distance(left: str, right: str) -> float:
    """Return Levenshtein distance divided by the longer input length."""

    if left == right:
        return 0.0
    denominator = max(len(left), len(right))
    if denominator == 0:
        return 0.0
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1] / denominator


def _compare(
    left: NormalizedSubject,
    right: NormalizedSubject,
) -> tuple[DuplicateRule | None, float]:
    if not left.tokens or not right.tokens:
        return None, 1.0
    distance = normalized_edit_distance(left.compact, right.compact)
    if left.compact == right.compact:
        return DuplicateRule.EXACT, 0.0
    left_tokens = frozenset(left.tokens)
    right_tokens = frozenset(right.tokens)
    if left_tokens < right_tokens or right_tokens < left_tokens:
        return DuplicateRule.TOKEN_SUBSET, distance
    if (
        min(len(left.compact), len(right.compact)) >= _MIN_EDIT_LENGTH
        and distance <= EDIT_DISTANCE_THRESHOLD
    ):
        return DuplicateRule.EDIT_DISTANCE, distance
    return None, distance


def _unique_normalized_values(
    values: tuple[str, ...],
) -> tuple[tuple[str, NormalizedSubject], ...]:
    normalized: dict[tuple[tuple[str, ...], str], str] = {}
    for value in values:
        subject = normalize_subject(value)
        if not subject.tokens:
            continue
        normalized.setdefault((subject.tokens, subject.compact), value)
    return tuple(
        (value, NormalizedSubject(tokens=key[0], compact=key[1]))
        for key, value in sorted(normalized.items(), key=lambda item: (item[0], item[1]))
    )


def _canonical_topic(topic: Topic, registry: TopicRegistry) -> Topic:
    if topic.status is not TopicStatus.MERGED:
        return topic
    target = registry.get(topic.merged_into or "")
    return topic if target is None else target


def _rule_order(rule: DuplicateRule) -> int:
    return {
        DuplicateRule.EXACT: 0,
        DuplicateRule.TOKEN_SUBSET: 1,
        DuplicateRule.EDIT_DISTANCE: 2,
    }[rule]


__all__ = [
    "EDIT_DISTANCE_THRESHOLD",
    "DuplicateRule",
    "NormalizedSubject",
    "TopicDuplicateMatch",
    "find_duplicate_topics",
    "normalize_subject",
    "normalized_edit_distance",
]
