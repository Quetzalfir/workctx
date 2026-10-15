"""Deterministic topic registry, mutation, merge, and review services."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from workctx.adapters.filesystem import CanonicalStore
from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import (
    EntityFrontmatter,
    Task,
    Topic,
    TopicDistinction,
    TopicRegistry,
    TopicStatus,
    slugify_topic,
    topic_lookup_key,
)
from workctx.domain.transactions import (
    AgentActor,
    ApprovalRequirement,
    EntityDocumentPayload,
    PathAbsentCondition,
    PathExistsCondition,
    PathHashCondition,
    TaskDocumentPayload,
    TopicRegistryDocumentPayload,
    TransactionCondition,
    TransactionOperation,
    UpdateOperation,
)
from workctx.models.context import LocalMutationPolicy
from workctx.topics.errors import (
    TopicDuplicateError,
    TopicMutationError,
    TopicRegistryError,
)
from workctx.topics.guard import (
    DuplicateRule,
    TopicDuplicateMatch,
    find_duplicate_topics,
    normalize_subject,
)
from workctx.topics.registry import (
    RegistrySnapshot,
    documents_by_topic,
    iter_topic_documents,
    load_registry,
    require_active_slug,
    resolve_active_topic,
)
from workctx.topics.transactions import (
    TopicRegistryCreateOperation,
    TopicRegistryUpdateOperation,
    TopicTransactionEngine,
    TopicTransactionProposal,
)
from workctx.transactions import ApplyResult, DryRunResult, verify_ledger

_REGISTRY_PATH = "99_meta/topics.yaml"


@dataclass(frozen=True, slots=True)
class TopicMutationResult:
    action: str
    proposal_id: str | None
    applied: bool
    changed: bool
    topic: Topic
    preview: DryRunResult | None = None
    receipt: ApplyResult | None = None


@dataclass(frozen=True, slots=True)
class TopicReviewCandidate:
    left_slug: str
    right_slug: str
    name_rule: DuplicateRule | None
    normalized_distance: float | None
    shared_documents: int
    union_documents: int
    co_reference_jaccard: float


class TopicService:
    """Application service bound to one isolated context root."""

    def __init__(
        self,
        context_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._clock = clock or _utc_now

    @property
    def context_root(self) -> Path:
        return self._root

    def list(self, *, include_all: bool = False) -> tuple[Topic, ...]:
        registry = load_registry(self._root).registry
        topics = registry.topics if include_all else list(registry.active_topics())
        return tuple(sorted(topics, key=lambda topic: topic.slug))

    def resolve(self, value: str) -> Topic:
        return resolve_active_topic(load_registry(self._root).registry, value)

    def add(
        self,
        name: str,
        *,
        scope: str,
        slug: str | None = None,
        aliases: Iterable[str] = (),
        distinct_from: str | None = None,
        reason: str | None = None,
        approved: bool = False,
    ) -> TopicMutationResult:
        snapshot = load_registry(self._root)
        candidate_slug = slug or slugify_topic(name)
        if not candidate_slug:
            raise TopicMutationError("Topic name cannot produce a portable slug.")
        if not normalize_subject(name).tokens:
            raise TopicMutationError(
                "A topic must name a durable subject, not only a test, version, number, or date."
            )
        matches = find_duplicate_topics(name, candidate_slug, snapshot.registry)
        distinction = self._validate_distinction(
            snapshot.registry,
            matches,
            distinct_from=distinct_from,
            reason=reason,
        )
        aliases_list = list(aliases)
        topic = Topic(
            slug=candidate_slug,
            name=name,
            scope=scope,
            aliases=aliases_list,
            status=TopicStatus.ACTIVE,
            merged_into=None,
            created_at=self._now(),
            distinct_from=[] if distinction is None else [distinction],
        )
        registry = self._validated_registry([*snapshot.registry.topics, topic])
        return self._mutate_registry("add", topic, snapshot, registry, approved=approved)

    def alias(
        self,
        slug: str,
        alias: str,
        *,
        approved: bool = False,
    ) -> TopicMutationResult:
        snapshot = load_registry(self._root)
        topic = require_active_slug(snapshot.registry, slug)
        alias_key = topic_lookup_key(alias)
        if not alias_key:
            raise TopicMutationError("Topic alias must contain letters or numbers.")
        if any(topic_lookup_key(existing) == alias_key for existing in topic.aliases):
            return TopicMutationResult(
                action="alias",
                proposal_id=None,
                applied=False,
                changed=False,
                topic=topic,
            )
        updated = topic.model_copy(update={"aliases": [*topic.aliases, alias]})
        registry = self._replace_topic(snapshot.registry, updated)
        return self._mutate_registry("alias", updated, snapshot, registry, approved=approved)

    def merge(
        self,
        source_slug: str,
        target_slug: str,
        *,
        approved: bool,
    ) -> TopicMutationResult:
        if source_slug == target_slug:
            raise TopicMutationError("A topic cannot be merged into itself.")
        snapshot = load_registry(self._root)
        if not snapshot.exists or snapshot.content_hash is None:
            raise TopicRegistryError("The canonical topic registry does not exist.")
        source = require_active_slug(snapshot.registry, source_slug)
        target = require_active_slug(snapshot.registry, target_slug)
        target_aliases = _dedupe_aliases([*target.aliases, *source.aliases, source.slug])
        merged_source = source.model_copy(
            update={
                "aliases": [],
                "status": TopicStatus.MERGED,
                "merged_into": target.slug,
            }
        )
        updated_target = target.model_copy(
            update={
                "aliases": target_aliases,
                "distinct_from": [
                    note for note in target.distinct_from if note.slug != source.slug
                ],
            }
        )
        updated_topics = [
            merged_source
            if topic.slug == source.slug
            else updated_target
            if topic.slug == target.slug
            else topic
            for topic in snapshot.registry.topics
        ]
        registry = self._validated_registry(updated_topics)
        now = self._now()
        operations: list[TransactionOperation] = [
            TopicRegistryUpdateOperation(
                op="update",
                target=_REGISTRY_PATH,
                payload=TopicRegistryDocumentPayload(
                    kind="topic_registry",
                    document=registry,
                ),
                expected_hash=snapshot.content_hash,
            )
        ]
        preconditions: list[TransactionCondition] = [
            PathHashCondition(
                kind="path_hash",
                path=_REGISTRY_PATH,
                content_hash=snapshot.content_hash,
            )
        ]
        postconditions: list[TransactionCondition] = [
            PathExistsCondition(kind="path_exists", path=_REGISTRY_PATH)
        ]
        for record in iter_topic_documents(self._root):
            retagged = _retag_values(
                record.document.topics,
                snapshot.registry,
                source.slug,
                target.slug,
            )
            if retagged == record.document.topics:
                continue
            values = record.document.model_dump(mode="python")
            values["topics"] = retagged
            values["updated_at"] = now
            if isinstance(record.document, Task):
                updated_task = Task.model_validate(values)
                payload: TaskDocumentPayload | EntityDocumentPayload = TaskDocumentPayload(
                    kind="task", document=updated_task, body=record.body
                )
            else:
                updated_entity = EntityFrontmatter.model_validate(values)
                payload = EntityDocumentPayload(
                    kind="entity", document=updated_entity, body=record.body
                )
            operations.append(
                UpdateOperation(
                    op="update",
                    target=record.relative_path,
                    payload=payload,
                    expected_hash=record.content_hash,
                )
            )
            preconditions.append(
                PathHashCondition(
                    kind="path_hash",
                    path=record.relative_path,
                    content_hash=record.content_hash,
                )
            )
            postconditions.append(
                PathExistsCondition(kind="path_exists", path=record.relative_path)
            )
        proposal = self._proposal(
            "merge",
            operations,
            preconditions,
            postconditions,
            created_at=now,
        )
        preview, receipt = self._execute(proposal, approved=approved)
        return TopicMutationResult(
            action="merge",
            proposal_id=proposal.id,
            applied=receipt is not None,
            changed=True,
            topic=updated_target,
            preview=preview,
            receipt=receipt,
        )

    def review(self) -> tuple[TopicReviewCandidate, ...]:
        registry = load_registry(self._root).registry
        active = sorted(registry.active_topics(), key=lambda topic: topic.slug)
        documents = documents_by_topic(self._root, registry)
        candidates: list[TopicReviewCandidate] = []
        for left_index, left in enumerate(active):
            for right in active[left_index + 1 :]:
                if _explicitly_distinct(left, right):
                    continue
                pair_registry = TopicRegistry(
                    schema_version=1,
                    topics=[right.model_copy(update={"distinct_from": []})],
                )
                guard_matches = find_duplicate_topics(left.name, left.slug, pair_registry)
                name_match = guard_matches[0] if guard_matches else None
                left_documents = documents.get(left.slug, frozenset())
                right_documents = documents.get(right.slug, frozenset())
                shared = len(left_documents & right_documents)
                union = len(left_documents | right_documents)
                jaccard = 0.0 if union == 0 else shared / union
                if name_match is None and shared == 0:
                    continue
                candidates.append(
                    TopicReviewCandidate(
                        left_slug=left.slug,
                        right_slug=right.slug,
                        name_rule=None if name_match is None else name_match.rule,
                        normalized_distance=(
                            None if name_match is None else name_match.normalized_distance
                        ),
                        shared_documents=shared,
                        union_documents=union,
                        co_reference_jaccard=jaccard,
                    )
                )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.name_rule is None,
                    -item.co_reference_jaccard,
                    item.left_slug,
                    item.right_slug,
                ),
            )
        )

    def _validate_distinction(
        self,
        registry: TopicRegistry,
        matches: tuple[TopicDuplicateMatch, ...],
        *,
        distinct_from: str | None,
        reason: str | None,
    ) -> TopicDistinction | None:
        if (distinct_from is None) != (reason is None):
            raise TopicMutationError("--distinct-from and --reason must be supplied together.")
        distinction: TopicDistinction | None = None
        if distinct_from is not None and reason is not None:
            target = require_active_slug(registry, distinct_from)
            distinction = TopicDistinction(slug=target.slug, reason=reason)
        unresolved_matches = [
            match
            for match in matches
            if distinction is None or match.topic_slug != distinction.slug
        ]
        if unresolved_matches:
            rendered = ", ".join(
                f"{match.topic_name} ({match.topic_slug})" for match in unresolved_matches
            )
            first = unresolved_matches[0]
            raise TopicDuplicateError(
                f"Topic creation refused: duplicate guard matched {rendered}. "
                "Reuse the existing topic or add the spelling as an alias; or re-run with "
                f"--distinct-from {first.topic_slug} --reason <text>."
            )
        return distinction

    def _mutate_registry(
        self,
        action: str,
        topic: Topic,
        snapshot: RegistrySnapshot,
        registry: TopicRegistry,
        *,
        approved: bool,
    ) -> TopicMutationResult:
        payload = TopicRegistryDocumentPayload(kind="topic_registry", document=registry)
        operations: list[TransactionOperation]
        preconditions: list[TransactionCondition]
        if snapshot.exists:
            if snapshot.content_hash is None:  # pragma: no cover - snapshot invariant
                raise TopicRegistryError("The topic registry hash is unavailable.")
            operations = [
                TopicRegistryUpdateOperation(
                    op="update",
                    target=_REGISTRY_PATH,
                    payload=payload,
                    expected_hash=snapshot.content_hash,
                )
            ]
            preconditions = [
                PathHashCondition(
                    kind="path_hash",
                    path=_REGISTRY_PATH,
                    content_hash=snapshot.content_hash,
                )
            ]
        else:
            operations = [
                TopicRegistryCreateOperation(
                    op="create",
                    target=_REGISTRY_PATH,
                    payload=payload,
                )
            ]
            preconditions = [PathAbsentCondition(kind="path_absent", path=_REGISTRY_PATH)]
        proposal = self._proposal(
            action,
            operations,
            preconditions,
            [PathExistsCondition(kind="path_exists", path=_REGISTRY_PATH)],
            created_at=self._now(),
        )
        preview, receipt = self._execute(proposal, approved=approved)
        return TopicMutationResult(
            action=action,
            proposal_id=proposal.id,
            applied=receipt is not None,
            changed=True,
            topic=topic,
            preview=preview,
            receipt=receipt,
        )

    def _proposal(
        self,
        action: str,
        operations: Sequence[TransactionOperation],
        preconditions: Sequence[TransactionCondition],
        postconditions: Sequence[TransactionCondition],
        *,
        created_at: datetime,
    ) -> TopicTransactionProposal:
        config = self._store.read_context_config()
        approval: ApprovalRequirement = (
            "required"
            if config.policies.local_mutations is LocalMutationPolicy.REVIEW_REQUIRED
            else "not_required"
        )
        proposal_id = f"TXP-{created_at:%Y%m%dT%H%M%SZ}-topic-{action}-{secrets.token_hex(4)}"
        return TopicTransactionProposal(
            schema_version=1,
            id=proposal_id,
            context_id=self._store.context_id,
            base_revision=verify_ledger(self._root).head_hash,
            actor=AgentActor(
                type="agent",
                id="workctx-topic",
                agent="workctx",
                model="deterministic",
            ),
            created_at=created_at,
            source_refs=[],
            operations=list(operations),
            preconditions=list(preconditions),
            postconditions=list(postconditions),
            expected_views=["sqlite"],
            approval=approval,
        )

    def _execute(
        self,
        proposal: TopicTransactionProposal,
        *,
        approved: bool,
    ) -> tuple[DryRunResult | None, ApplyResult | None]:
        engine = TopicTransactionEngine(self._root, clock=self._clock)
        if approved:
            return None, engine.apply(proposal, approved=True)
        SQLiteProjection(self._root).ensure_ready()
        preview = engine.dry_run(proposal)
        if not preview.valid:
            codes = ", ".join(item.code for item in preview.diagnostics)
            raise TopicMutationError(
                f"Topic transaction preview failed validation{': ' + codes if codes else ''}."
            )
        return preview, None

    @staticmethod
    def _validated_registry(topics: Sequence[Topic]) -> TopicRegistry:
        try:
            return TopicRegistry.model_validate(
                {
                    "schema_version": 1,
                    "topics": [topic.model_dump(mode="python") for topic in topics],
                }
            )
        except ValidationError as exc:
            raise TopicRegistryError(
                "The proposed topic registry violates registry integrity."
            ) from exc

    def _replace_topic(self, registry: TopicRegistry, replacement: Topic) -> TopicRegistry:
        return self._validated_registry(
            [replacement if topic.slug == replacement.slug else topic for topic in registry.topics]
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise TopicMutationError("Topic clocks must return timezone-aware datetimes.")
        return value.astimezone(UTC).replace(microsecond=0)


def list_topics(root: Path, *, include_all: bool = False) -> tuple[Topic, ...]:
    return TopicService(root).list(include_all=include_all)


def resolve_topic(root: Path, value: str) -> Topic:
    return TopicService(root).resolve(value)


def add_topic(
    root: Path,
    name: str,
    *,
    scope: str,
    slug: str | None = None,
    aliases: Iterable[str] = (),
    distinct_from: str | None = None,
    reason: str | None = None,
    approved: bool = False,
) -> TopicMutationResult:
    return TopicService(root).add(
        name,
        scope=scope,
        slug=slug,
        aliases=aliases,
        distinct_from=distinct_from,
        reason=reason,
        approved=approved,
    )


def add_topic_alias(
    root: Path,
    slug: str,
    alias: str,
    *,
    approved: bool = False,
) -> TopicMutationResult:
    return TopicService(root).alias(slug, alias, approved=approved)


def merge_topics(
    root: Path,
    source_slug: str,
    target_slug: str,
    *,
    approved: bool,
) -> TopicMutationResult:
    return TopicService(root).merge(source_slug, target_slug, approved=approved)


def review_topics(root: Path) -> tuple[TopicReviewCandidate, ...]:
    return TopicService(root).review()


def _retag_values(
    values: list[str],
    registry: TopicRegistry,
    source_slug: str,
    target_slug: str,
) -> list[str]:
    retagged: list[str] = []
    for value in values:
        resolution = registry.resolve(value)
        replacement = (
            target_slug
            if resolution is not None and resolution.topic.slug == source_slug
            else value
        )
        if replacement not in retagged:
            retagged.append(replacement)
    return retagged


def _dedupe_aliases(values: Iterable[str]) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = topic_lookup_key(value)
        if key in seen:
            continue
        seen.add(key)
        aliases.append(value)
    return aliases


def _explicitly_distinct(left: Topic, right: Topic) -> bool:
    return any(note.slug == right.slug for note in left.distinct_from) or any(
        note.slug == left.slug for note in right.distinct_from
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "TopicMutationResult",
    "TopicReviewCandidate",
    "TopicService",
    "add_topic",
    "add_topic_alias",
    "list_topics",
    "merge_topics",
    "resolve_topic",
    "review_topics",
]
