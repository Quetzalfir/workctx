"""Audited checkpoint save, listing, and deterministic staleness checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from urllib.parse import unquote

from pydantic import ValidationError

from workctx.adapters.filesystem import CanonicalStore, ContextZone
from workctx.adapters.sqlite import (
    ClaimRecord,
    EntityRecord,
    ObservationRecord,
    SQLiteProjection,
    TaskRecord,
)
from workctx.checkpoints.errors import CheckpointValidationError
from workctx.checkpoints.models import (
    CheckpointCheckResult,
    CheckpointItemResult,
    CheckpointLocation,
    CheckpointNewItem,
    CheckpointReason,
    CheckpointStatus,
    CheckpointStatusCounts,
)
from workctx.checkpoints.registry import list_checkpoints, load_checkpoint
from workctx.checkpoints.transactions import (
    CheckpointCreateOperation,
    CheckpointTransactionEngine,
    CheckpointTransactionProposal,
)
from workctx.domain import (
    Checkpoint,
    CheckpointItem,
    RelationType,
    WorkctxUri,
    slugify_topic,
)
from workctx.domain.transactions import (
    AgentActor,
    ApprovalRequirement,
    CheckpointDocumentPayload,
    PathAbsentCondition,
    PathExistsCondition,
)
from workctx.models.context import LocalMutationPolicy
from workctx.retrieval import ContextPack, PackSectionName
from workctx.topics.registry import iter_topic_documents, load_registry, resolve_active_topic
from workctx.transactions import ApplyResult, DryRunResult, verify_ledger
from workctx.validation.engine import contains_possible_secret

_MAX_PACK_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CheckpointSaveResult:
    checkpoint: Checkpoint
    proposal_id: str
    applied: bool
    preview: DryRunResult | None = None
    receipt: ApplyResult | None = None


type _DocumentRecord = EntityRecord | TaskRecord | ClaimRecord | ObservationRecord
type _DocumentChange = Literal["content", "revision"]


class CheckpointService:
    """Checkpoint operations bound to one isolated context root."""

    def __init__(
        self,
        context_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        projection_factory: Callable[[Path], SQLiteProjection] = SQLiteProjection,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._context_id = self._store.context_id
        self._clock = clock or _utc_now
        self._projection = projection_factory(self._root)

    @property
    def context_root(self) -> Path:
        return self._root

    def save(
        self,
        label: str,
        *,
        references: Iterable[str] = (),
        topic: str | None = None,
        from_pack: Path | None = None,
        approved: bool = False,
    ) -> CheckpointSaveResult:
        """Preview or save one content-free checkpoint through an audited transaction."""

        if contains_possible_secret(label):
            raise CheckpointValidationError(
                "Checkpoint labels cannot contain secret-looking values."
            )
        selected_topic = (
            None
            if topic is None
            else resolve_active_topic(load_registry(self._root).registry, topic).slug
        )
        supplied = list(references)
        if from_pack is not None:
            supplied.extend(_references_from_pack(from_pack, context_id=self._context_id))
        canonical_references = tuple(sorted(set(supplied)))
        if not canonical_references:
            raise CheckpointValidationError(
                "Checkpoint save requires at least one --ref or a --from-pack file."
            )

        self._projection.ensure_ready()
        items: list[CheckpointItem] = []
        hash_cache: dict[str, str] = {}
        for value in canonical_references:
            try:
                uri = WorkctxUri.parse(value)
                uri.require_context(self._context_id)
            except ValueError as exc:
                raise CheckpointValidationError(
                    "Checkpoint references must be canonical workctx:// URIs in this context."
                ) from exc
            record = self._projection.get_document_by_uri(uri)
            if record is None:
                raise CheckpointValidationError(
                    "A checkpoint reference does not resolve to a canonical item."
                )
            items.append(
                CheckpointItem(
                    uri=str(uri),
                    source_path=record.source_path,
                    content_hash=self._content_hash(record.source_path, hash_cache),
                    revision=_record_revision(record),
                )
            )
        if any(
            contains_possible_secret(value) or contains_possible_secret(unquote(value))
            for item in items
            for value in (item.uri, item.source_path)
        ):
            raise CheckpointValidationError(
                "Checkpoint references cannot contain secret-looking metadata."
            )

        created_at = self._now()
        base_revision = verify_ledger(self._root).head_hash
        checkpoint_id, digest = _checkpoint_identity(
            created_at,
            label,
            canonical_references,
            base_revision,
            selected_topic,
        )
        try:
            checkpoint = Checkpoint(
                id=checkpoint_id,
                created_at=created_at,
                label=label,
                topic=selected_topic,
                items=items,
            )
        except ValidationError as exc:
            raise CheckpointValidationError("Checkpoint metadata is invalid.") from exc

        payload = CheckpointDocumentPayload(kind="checkpoint", document=checkpoint)
        operation = CheckpointCreateOperation(
            op="create",
            target=checkpoint.relative_path,
            payload=payload,
        )
        config = self._store.read_context_config()
        approval: ApprovalRequirement = (
            "required"
            if config.policies.local_mutations is LocalMutationPolicy.REVIEW_REQUIRED
            else "not_required"
        )
        proposal = CheckpointTransactionProposal(
            schema_version=1,
            id=f"TXP-{created_at:%Y%m%dT%H%M%SZ}-checkpoint-save-{digest}",
            context_id=self._context_id,
            base_revision=base_revision,
            actor=AgentActor(
                type="agent",
                id="workctx-checkpoint",
                agent="workctx",
                model="deterministic",
            ),
            created_at=created_at,
            source_refs=[item.uri for item in items],
            operations=[operation],
            preconditions=[PathAbsentCondition(kind="path_absent", path=checkpoint.relative_path)],
            postconditions=[PathExistsCondition(kind="path_exists", path=checkpoint.relative_path)],
            expected_views=["sqlite"],
            approval=approval,
        )
        engine = CheckpointTransactionEngine(self._root, clock=self._clock)
        if approved:
            receipt = engine.apply(proposal, approved=True)
            return CheckpointSaveResult(
                checkpoint=checkpoint,
                proposal_id=proposal.id,
                applied=True,
                receipt=receipt,
            )
        preview = engine.dry_run(proposal)
        if not preview.valid:
            codes = ", ".join(item.code for item in preview.diagnostics)
            raise CheckpointValidationError(
                f"Checkpoint transaction preview failed validation{': ' + codes if codes else ''}."
            )
        return CheckpointSaveResult(
            checkpoint=checkpoint,
            proposal_id=proposal.id,
            applied=False,
            preview=preview,
        )

    def list(self) -> tuple[Checkpoint, ...]:
        return list_checkpoints(self._root)

    def check(self, checkpoint_id: str) -> CheckpointCheckResult:
        """Compare a checkpoint with current canonical state without exposing content."""

        checked_at = self._now()
        checkpoint = load_checkpoint(self._root, checkpoint_id)
        self._projection.ensure_ready()
        hash_cache: dict[str, str] = {}
        item_results = tuple(
            self._check_item(item, hash_cache)
            for item in sorted(checkpoint.items, key=lambda candidate: candidate.uri)
        )
        new_items = self._new_topic_items(checkpoint)
        return CheckpointCheckResult(
            checkpoint_id=checkpoint.id,
            label=checkpoint.label,
            checkpoint_created_at=checkpoint.created_at,
            checked_at=checked_at,
            topic=checkpoint.topic,
            items=item_results,
            new_items=new_items,
            counts=CheckpointStatusCounts(
                green=sum(item.status is CheckpointStatus.GREEN for item in item_results),
                yellow=sum(item.status is CheckpointStatus.YELLOW for item in item_results),
                red=sum(item.status is CheckpointStatus.RED for item in item_results),
                new=len(new_items),
            ),
        )

    def _check_item(
        self,
        item: CheckpointItem,
        hash_cache: dict[str, str],
    ) -> CheckpointItemResult:
        current = self._projection.get_document_by_uri(item.uri)
        successors = self._successors(item.uri, current, hash_cache)
        if successors:
            return _item_result(
                item,
                status=CheckpointStatus.RED,
                reason=CheckpointReason.SUPERSEDED,
                current=current,
                current_hash=(
                    None if current is None else self._content_hash(current.source_path, hash_cache)
                ),
                successors=successors,
            )
        if current is None:
            return _item_result(
                item,
                status=CheckpointStatus.RED,
                reason=CheckpointReason.DELETED,
                current=None,
                current_hash=None,
            )
        current_hash = self._content_hash(current.source_path, hash_cache)
        if current.source_path != item.source_path:
            location = _location(current, item.uri, current_hash)
            return _item_result(
                item,
                status=CheckpointStatus.RED,
                reason=CheckpointReason.MOVED,
                current=current,
                current_hash=current_hash,
                successors=(location,),
            )
        revision = _record_revision(current)
        changes = _document_changes(item, current_hash, revision)
        if changes:
            return _item_result(
                item,
                status=CheckpointStatus.YELLOW,
                reason=CheckpointReason.CHANGED,
                current=current,
                current_hash=current_hash,
                changes=changes,
            )
        return _item_result(
            item,
            status=CheckpointStatus.GREEN,
            reason=CheckpointReason.UNCHANGED,
            current=current,
            current_hash=current_hash,
        )

    def _successors(
        self,
        uri: str,
        current: _DocumentRecord | None,
        hash_cache: dict[str, str],
    ) -> tuple[CheckpointLocation, ...]:
        successor_uris: set[str] = set()
        if isinstance(current, ClaimRecord):
            if current.superseded_by is not None:
                successor_uris.add(
                    str(WorkctxUri(self._context_id, "claim", current.superseded_by))
                )
            for candidate in self._projection.claims_for_subject(current.subject):
                if candidate.supersedes == current.id:
                    successor_uris.add(str(candidate.uri))
        for edge in self._projection.inbound_edges(
            uri,
            relations=frozenset({RelationType.SUPERSEDES}),
        ):
            successor_uris.add(str(edge.source_uri))

        locations: list[CheckpointLocation] = []
        for successor_uri in sorted(successor_uris):
            successor = self._projection.get_document_by_uri(successor_uri)
            if successor is None:
                continue
            locations.append(
                _location(
                    successor,
                    successor_uri,
                    self._content_hash(successor.source_path, hash_cache),
                )
            )
        return tuple(locations)

    def _new_topic_items(self, checkpoint: Checkpoint) -> tuple[CheckpointNewItem, ...]:
        if checkpoint.topic is None:
            return ()
        registry = load_registry(self._root).registry
        resolution = registry.resolve(checkpoint.topic)
        if resolution is None:
            return ()
        canonical_topic = resolution.topic.slug
        saved = {item.uri for item in checkpoint.items}
        new_items: list[CheckpointNewItem] = []
        for record in iter_topic_documents(self._root):
            if record.document.created_at <= checkpoint.created_at:
                continue
            if str(record.document.uri) in saved:
                continue
            resolved_topics = {
                item.topic.slug
                for value in record.document.topics
                if (item := registry.resolve(value)) is not None
            }
            if canonical_topic not in resolved_topics:
                continue
            new_items.append(
                CheckpointNewItem(
                    uri=str(record.document.uri),
                    source_path=record.relative_path,
                    created_at=record.document.created_at,
                )
            )
        return tuple(sorted(new_items, key=lambda item: (item.created_at, item.uri)))

    def _content_hash(self, relative_path: str, cache: dict[str, str]) -> str:
        cached = cache.get(relative_path)
        if cached is not None:
            return cached
        zone = _source_zone(relative_path)
        path = self._store.resolve_path(relative_path, zones=(zone,))
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise CheckpointValidationError(
                "A checkpoint item source could not be read safely."
            ) from exc
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        cache[relative_path] = digest
        return digest

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise CheckpointValidationError(
                "Checkpoint clocks must return timezone-aware datetimes."
            )
        return value.astimezone(UTC).replace(microsecond=0)


def save_checkpoint(
    root: Path,
    label: str,
    *,
    references: Iterable[str] = (),
    topic: str | None = None,
    from_pack: Path | None = None,
    approved: bool = False,
) -> CheckpointSaveResult:
    return CheckpointService(root).save(
        label,
        references=references,
        topic=topic,
        from_pack=from_pack,
        approved=approved,
    )


def check_checkpoint(root: Path, checkpoint_id: str) -> CheckpointCheckResult:
    return CheckpointService(root).check(checkpoint_id)


def _checkpoint_identity(
    created_at: datetime,
    label: str,
    references: tuple[str, ...],
    base_revision: str,
    topic: str | None,
) -> tuple[str, str]:
    label_slug = slugify_topic(label)[:48].rstrip("-") or "session"
    digest_input = json.dumps(
        {
            "base_revision": base_revision,
            "created_at": created_at.isoformat(),
            "label": label,
            "references": references,
            "topic": topic,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(digest_input).hexdigest()[:8]
    return f"CHK-{created_at:%Y%m%dT%H%M%SZ}-{label_slug}-{digest}", digest


def _references_from_pack(path: Path, *, context_id: str) -> tuple[str, ...]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_PACK_BYTES:
            raise ValueError("pack output must be a bounded regular file")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("pack output must contain a JSON object")
        candidate: object = loaded
        if isinstance(loaded.get("result"), dict):
            candidate = cast(dict[str, object], loaded["result"]).get("pack")
        elif "pack" in loaded:
            candidate = loaded["pack"]
        pack = ContextPack.model_validate(candidate)
    except (OSError, UnicodeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        raise CheckpointValidationError(
            "The --from-pack file is not a valid context-pack JSON output."
        ) from exc
    if pack.context_id != context_id:
        raise CheckpointValidationError("The --from-pack file belongs to another context.")
    references: set[str] = set()
    for section_name in PackSectionName:
        section = getattr(pack.sections, section_name.value)
        for item in section.items:
            if item.uri is not None and item.uri.startswith("workctx://"):
                references.add(item.uri)
    return tuple(sorted(references))


def _record_revision(record: _DocumentRecord) -> datetime | None:
    if isinstance(record, (EntityRecord, TaskRecord)):
        return record.updated_at
    if isinstance(record, ClaimRecord):
        return record.observed_at
    return record.observed_at


def _document_changes(
    item: CheckpointItem,
    current_hash: str,
    current_revision: datetime | None,
) -> tuple[_DocumentChange, ...]:
    changes: list[_DocumentChange] = []
    if current_hash != item.content_hash:
        changes.append("content")
    if current_revision != item.revision:
        changes.append("revision")
    return tuple(changes)


def _item_result(
    item: CheckpointItem,
    *,
    status: CheckpointStatus,
    reason: CheckpointReason,
    current: _DocumentRecord | None,
    current_hash: str | None,
    changes: tuple[_DocumentChange, ...] = (),
    successors: tuple[CheckpointLocation, ...] = (),
) -> CheckpointItemResult:
    return CheckpointItemResult(
        uri=item.uri,
        status=status,
        reason=reason,
        saved_path=item.source_path,
        current_path=None if current is None else current.source_path,
        saved_revision=item.revision,
        current_revision=None if current is None else _record_revision(current),
        saved_content_hash=item.content_hash,
        current_content_hash=current_hash,
        changes=changes,
        successors=successors,
    )


def _location(record: _DocumentRecord, uri: str, content_hash: str) -> CheckpointLocation:
    return CheckpointLocation(
        uri=uri,
        source_path=record.source_path,
        revision=_record_revision(record),
        content_hash=content_hash,
    )


def _source_zone(relative_path: str) -> ContextZone:
    prefix = relative_path.partition("/")[0]
    zones = {
        ContextZone.KNOWLEDGE.value: ContextZone.KNOWLEDGE,
        ContextZone.WORK.value: ContextZone.WORK,
        ContextZone.OUTBOX.value: ContextZone.OUTBOX,
    }
    try:
        return zones[prefix]
    except KeyError as exc:
        raise CheckpointValidationError(
            "A checkpoint item source is outside canonical document zones."
        ) from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CheckpointSaveResult",
    "CheckpointService",
    "check_checkpoint",
    "save_checkpoint",
]
