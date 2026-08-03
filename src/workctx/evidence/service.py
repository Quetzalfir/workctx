"""Deterministic application rails for agent-authored evidence processing."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote

from pydantic import BaseModel, ValidationError

from workctx.adapters.filesystem import CanonicalStore, ContextZone
from workctx.adapters.sqlite import EntityRecord, SQLiteProjection, TaskRecord
from workctx.domain import (
    ArtifactStatus,
    Claim,
    EntityFrontmatter,
    EntityType,
    Observation,
    ObservationId,
    ObservationKind,
    Task,
    TypedReference,
    WorkctxUri,
    normalize_workctx_uri,
    validate_durable_reference,
    validate_workctx_entity_uri,
)
from workctx.domain.tasks import TASK_ID_PATTERN
from workctx.domain.transactions import TransactionProposal
from workctx.evidence.errors import (
    EvidenceArtifactNotFoundError,
    EvidenceArtifactQuarantinedError,
    EvidenceContextError,
    EvidenceInputError,
    EvidenceStateError,
)
from workctx.evidence.models import (
    CandidateContextPack,
    EvidenceContentDescriptor,
    EvidenceStagingPayload,
    EvidenceStagingResult,
    ObservationSchemaExpectations,
    ProcessingPacket,
    ProposedDocument,
    ResolvedEntityReference,
    ResolvedRelation,
    StagedClaimDocument,
    StagedEntityDocument,
    StagedTaskDocument,
)
from workctx.ingestion import ArchiveResult, ArtifactRecord, archive_after, list_inbox
from workctx.retrieval import build_pack
from workctx.transactions import ApplyResult, authenticate_apply_result, verify_ledger
from workctx.validation import contains_possible_secret

_LOCATOR_TYPES = (
    "line_range",
    "page_range",
    "time_range",
    "message",
    "image_region",
    "json_pointer",
    "table_range",
    "repo_range",
    "whole_artifact",
)
_TASK_ID = re.compile(TASK_ID_PATTERN)
_SPECIAL_NEW_ENTITY_TYPES = {"artifact", "claim", "evidence", "observation", "task"}
_ENTITY_DIRECTORIES = {
    "person": "02_knowledge/people",
    "team": "02_knowledge/teams",
    "project": "02_knowledge/projects",
    "system": "02_knowledge/systems",
    "service": "02_knowledge/services",
    "module": "02_knowledge/modules",
    "flow": "02_knowledge/flows",
    "integration": "02_knowledge/integrations",
    "decision": "02_knowledge/decisions",
    "risk": "02_knowledge/risks",
    "question": "02_knowledge/questions",
    "draft": "05_outbox/documentation",
    "investigation": "03_work/investigations",
    "incident": "03_work/incidents",
}
_DOCUMENT_ZONES = (ContextZone.KNOWLEDGE, ContextZone.WORK, ContextZone.OUTBOX)


@dataclass(slots=True)
class _EntityState:
    target: str
    document: EntityFrontmatter
    body: str
    expected_hash: str | None


@dataclass(slots=True)
class _TaskState:
    target: str
    document: Task
    body: str
    expected_hash: str | None


class _EntityResolver:
    def __init__(self, projection: SQLiteProjection, context_id: str) -> None:
        self._projection = projection
        self._context_id = context_id
        self._declared_uris: set[str] = set()
        self._declared_names: dict[str, set[str]] = {}
        self._resolutions: dict[tuple[str, str], bool] = {}

    def add_declared(
        self,
        *,
        uri: str,
        identifier: str,
        title: str,
        aliases: Sequence[str] = (),
    ) -> None:
        self._declared_uris.add(uri)
        for value in (uri, identifier, title, *aliases):
            normalized = value.strip().casefold()
            if normalized:
                self._declared_names.setdefault(normalized, set()).add(uri)

    def resolve_entity(self, value: str, *, path: str) -> str:
        authored = value.strip()
        if not authored:
            raise _invalid_input("Entity references must not be empty.", path=path)

        if authored.startswith("workctx://"):
            try:
                canonical = str(validate_workctx_entity_uri(normalize_workctx_uri(authored)))
                parsed = WorkctxUri.parse(canonical)
            except ValueError as exc:
                raise _invalid_input("A proposed entity reference is invalid.", path=path) from exc
            if parsed.context_id != self._context_id:
                raise EvidenceContextError(path=path)
            if (
                canonical not in self._declared_uris
                and self._projection.get_document_by_uri(canonical) is None
            ):
                raise _unknown_entity(path)
            self._remember(authored, canonical)
            return canonical

        if "://" in authored:
            raise _unknown_entity(path)

        candidates = set(self._declared_names.get(authored.casefold(), ()))
        by_id = self._projection.get_entity_by_id(authored)
        if by_id is not None:
            candidates.add(str(by_id.uri))
        candidates.update(
            str(record.uri) for record in self._projection.find_entities_by_alias(authored)
        )
        try:
            hits = self._projection.search(authored, limit=50)
        except ValueError:
            hits = ()
        for hit in hits:
            if hit.id == authored or hit.title.casefold() == authored.casefold():
                candidates.add(str(hit.uri))

        if not candidates:
            raise _unknown_entity(path)
        if len(candidates) != 1:
            raise EvidenceInputError(
                "EVIDENCE-AMBIGUOUS-ENTITY",
                "A proposed entity reference resolves to multiple entities.",
                path=path,
            )
        canonical = next(iter(candidates))
        self._remember(authored, canonical)
        return canonical

    def resolve_target(self, value: str, *, path: str) -> str:
        authored = value.strip()
        if "://" not in authored or authored.startswith("workctx://"):
            return self.resolve_entity(authored, path=path)
        try:
            return validate_durable_reference(authored)
        except ValueError as exc:
            raise _invalid_input("A proposed relation target is invalid.", path=path) from exc

    def resolutions(self) -> tuple[ResolvedEntityReference, ...]:
        return tuple(
            ResolvedEntityReference(authored=authored, uri=uri, declared=declared)
            for (authored, uri), declared in sorted(
                self._resolutions.items(), key=lambda item: (item[0][0].casefold(), item[0][1])
            )
        )

    def _remember(self, authored: str, uri: str) -> None:
        self._resolutions[(authored, uri)] = uri in self._declared_uris


def begin_processing(root: Path, artifact_id: str) -> ProcessingPacket:
    """Return a content-free processing packet for one eligible artifact."""

    record = _require_processable_artifact(root, artifact_id)
    projection = SQLiteProjection(root)
    context_packs: list[CandidateContextPack] = []
    unresolved: list[str] = []
    for candidate in _candidate_values(record):
        matches = _candidate_records(projection, candidate)
        built_for_candidate = False
        for match in matches:
            outcome = build_pack(projection, str(match.uri))
            if not outcome.built or outcome.pack is None:
                continue
            context_packs.append(
                CandidateContextPack(
                    candidate=candidate,
                    uri=str(match.uri),
                    pack=outcome.pack,
                )
            )
            built_for_candidate = True
        if not built_for_candidate:
            unresolved.append(candidate)

    manifest = record.manifest
    return ProcessingPacket(
        context_id=projection.context_id,
        manifest_path=record.manifest_path,
        manifest=manifest,
        artifact_ref=record.reference,
        content=EvidenceContentDescriptor(
            path=manifest.preserved_path,
            content_hash=manifest.content_hash,
            media_type=manifest.media_type,
        ),
        context_packs=tuple(context_packs),
        unresolved_candidates=tuple(unresolved),
        observation_expectations=ObservationSchemaExpectations(
            source_ref=record.reference,
            observation_kinds=tuple(kind.value for kind in ObservationKind),
            locator_types=_LOCATOR_TYPES,
            json_schema=Observation.model_json_schema(mode="validation"),
        ),
    )


def stage_observations(
    root: Path,
    artifact_id: str,
    payload: object,
) -> EvidenceStagingResult:
    """Validate and resolve agent-authored evidence material without LLM calls."""

    record = _require_processable_artifact(root, artifact_id)
    _reject_possible_secrets(payload)
    try:
        authored = (
            payload
            if isinstance(payload, EvidenceStagingPayload)
            else EvidenceStagingPayload.model_validate(payload)
        )
    except ValidationError as exc:
        raise _validation_error(exc) from exc

    if any(source_ref != record.reference for source_ref in authored.source_refs):
        raise _source_mismatch("$.source_refs")

    store = CanonicalStore(root)
    projection = SQLiteProjection(root)
    context_id = store.context_id
    note = authored.evidence_note
    evidence_uri = str(WorkctxUri(context_id, "evidence", note.id))
    if projection.get_document_by_uri(evidence_uri) is not None:
        raise EvidenceStateError(
            "EVIDENCE-DOCUMENT-EXISTS",
            "The proposed evidence note already exists.",
        )

    resolver = _EntityResolver(projection, context_id)
    resolver.add_declared(
        uri=evidence_uri,
        identifier=note.id,
        title=note.title,
        aliases=note.aliases,
    )

    entity_states = _prepare_new_entity_shells(
        authored.new_entities,
        context_id=context_id,
        projection=projection,
        resolver=resolver,
    )
    task_states = _prepare_task_shells(
        authored.tasks,
        context_id=context_id,
        projection=projection,
        store=store,
        resolver=resolver,
    )
    observations, observation_uris = _parse_observations(
        authored.observations,
        evidence_id=note.id,
        artifact_ref=record.reference,
        context_id=context_id,
        projection=projection,
        resolver=resolver,
    )

    claims = _prepare_claims(
        authored.claims,
        resolver=resolver,
        observation_uris=observation_uris,
        projection=projection,
        context_id=context_id,
    )
    for claim in claims:
        resolver.add_declared(
            uri=str(WorkctxUri(context_id, "claim", claim.document.id)),
            identifier=claim.document.id,
            title=claim.document.id,
        )

    evidence_document = EntityFrontmatter.model_validate(
        {
            "schema_version": 1,
            "id": note.id,
            "entity_type": "evidence",
            "title": note.title,
            "uri": evidence_uri,
            "aliases": list(note.aliases),
            "status": note.status,
            "confidence": note.confidence.value,
            "tags": list(note.tags),
            "references": [],
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "artifact_ref": record.reference,
            "observations": [observation.model_dump(mode="json") for observation in observations],
        }
    )
    evidence_state = _EntityState(
        target=f"02_knowledge/evidence/{quote(note.id, safe='-._~')}.md",
        document=evidence_document,
        body=note.body,
        expected_hash=None,
    )

    states_by_uri: dict[str, _EntityState | _TaskState] = {
        evidence_uri: evidence_state,
        **{str(state.document.uri): state for state in entity_states},
        **{str(state.document.uri): state for state in task_states},
    }
    resolved_relations = _resolve_and_attach_relations(
        authored,
        resolver=resolver,
        observation_uris=observation_uris,
        projection=projection,
        store=store,
        states_by_uri=states_by_uri,
        context_id=context_id,
    )

    created_at = note.created_at.astimezone(UTC).replace(microsecond=0)
    proposal_id = _proposal_id(created_at, artifact_id)
    return EvidenceStagingResult(
        context_id=context_id,
        artifact_id=artifact_id,
        artifact_ref=record.reference,
        base_revision=verify_ledger(root).head_hash,
        proposal_id=proposal_id,
        created_at=created_at,
        actor=authored.actor,
        evidence_note=_staged_entity(evidence_state),
        entity_documents=tuple(
            _staged_entity(state)
            for uri, state in sorted(states_by_uri.items(), key=lambda item: item[1].target)
            if uri != evidence_uri and isinstance(state, _EntityState)
        ),
        task_documents=tuple(
            _staged_task(state)
            for state in sorted(
                (item for item in states_by_uri.values() if isinstance(item, _TaskState)),
                key=lambda item: item.target,
            )
        ),
        claim_documents=tuple(sorted(claims, key=lambda item: item.target)),
        observations=observations,
        relations=resolved_relations,
        resolutions=resolver.resolutions(),
    )


def build_evidence_proposal(staging: EvidenceStagingResult) -> TransactionProposal:
    """Build one atomic multi-document proposal from a typed staging result."""

    operations: list[dict[str, object]] = []
    preconditions: list[dict[str, object]] = [
        {"kind": "reference_exists", "reference": staging.artifact_ref}
    ]
    postconditions: list[dict[str, object]] = []
    created_uris: list[str] = []

    staged_documents: tuple[
        StagedEntityDocument | StagedTaskDocument | StagedClaimDocument, ...
    ] = (
        staging.evidence_note,
        *staging.entity_documents,
        *staging.task_documents,
        *staging.claim_documents,
    )
    for staged in staged_documents:
        if isinstance(staged, StagedEntityDocument):
            payload = {
                "kind": "entity",
                "document": staged.document.model_dump(mode="python"),
                "body": staged.body,
            }
            operation = staged.operation
            expected_hash = staged.expected_hash
            uri = staged.document.uri
        elif isinstance(staged, StagedTaskDocument):
            payload = {
                "kind": "task",
                "document": staged.document.model_dump(mode="python"),
                "body": staged.body,
            }
            operation = staged.operation
            expected_hash = staged.expected_hash
            uri = staged.document.uri
        else:
            assert isinstance(staged, StagedClaimDocument)
            payload = {
                "kind": "claim",
                "document": staged.document.model_dump(mode="python"),
                "body": staged.body,
            }
            operation = "create"
            expected_hash = None
            uri = str(WorkctxUri(staging.context_id, "claim", staged.document.id))

        operation_payload: dict[str, object] = {
            "op": operation,
            "target": staged.target,
            "payload": payload,
        }
        if expected_hash is not None:
            operation_payload["expected_hash"] = expected_hash
            preconditions.append(
                {"kind": "path_hash", "path": staged.target, "content_hash": expected_hash}
            )
        else:
            preconditions.append({"kind": "path_absent", "path": staged.target})
        operations.append(operation_payload)
        postconditions.append({"kind": "path_exists", "path": staged.target})
        created_uris.append(uri)

    postconditions.extend({"kind": "reference_exists", "reference": uri} for uri in created_uris)
    try:
        return TransactionProposal.model_validate(
            {
                "schema_version": 1,
                "id": staging.proposal_id,
                "context_id": staging.context_id,
                "base_revision": staging.base_revision,
                "actor": staging.actor.model_dump(mode="python"),
                "created_at": staging.created_at,
                "source_refs": [staging.artifact_ref],
                "operations": operations,
                "preconditions": preconditions,
                "postconditions": postconditions,
                "expected_views": ["sqlite"],
                "approval": "required",
            }
        )
    except ValidationError as exc:  # staging is typed; preserve a content-free boundary
        raise _validation_error(exc, code="EVIDENCE-PROPOSAL-INVALID") from exc


def complete_processing(
    root: Path,
    artifact_id: str,
    apply_result: ApplyResult,
) -> ArchiveResult:
    """Authenticate the commit receipt and archive through the ingestion API."""

    record = _artifact_record(root, artifact_id)
    if record.manifest.status is ArtifactStatus.QUARANTINED:
        raise EvidenceArtifactQuarantinedError()
    authenticate_apply_result(root, apply_result)
    return archive_after(root, artifact_id, apply_result)


def _require_processable_artifact(root: Path, artifact_id: str) -> ArtifactRecord:
    record = _artifact_record(root, artifact_id)
    status = record.manifest.status
    if status is ArtifactStatus.QUARANTINED:
        raise EvidenceArtifactQuarantinedError()
    if status not in {ArtifactStatus.PENDING, ArtifactStatus.PROCESSING}:
        raise EvidenceStateError(
            "EVIDENCE-ARTIFACT-STATE",
            "The artifact is not eligible for evidence processing.",
        )
    return record


def _artifact_record(root: Path, artifact_id: str) -> ArtifactRecord:
    matches = tuple(
        artifact for artifact in list_inbox(root).artifacts if artifact.manifest.id == artifact_id
    )
    if len(matches) != 1:
        raise EvidenceArtifactNotFoundError()
    return matches[0]


def _candidate_values(record: ArtifactRecord) -> tuple[str, ...]:
    values: list[str] = []
    for value in record.manifest.participants:
        normalized = value.strip()
        if normalized and normalized not in values:
            values.append(normalized)
    return tuple(values)


def _candidate_records(projection: SQLiteProjection, candidate: str) -> tuple[EntityRecord, ...]:
    matches: dict[str, EntityRecord] = {}
    if candidate.startswith("workctx://"):
        try:
            parsed = WorkctxUri.parse(candidate)
        except ValueError:
            return ()
        if parsed.context_id != projection.context_id:
            return ()
        record = projection.get_entity_by_uri(parsed)
        if record is not None:
            matches[str(record.uri)] = record
    else:
        by_id = projection.get_entity_by_id(candidate)
        if by_id is not None:
            matches[str(by_id.uri)] = by_id
        for record in projection.find_entities_by_alias(candidate):
            matches[str(record.uri)] = record
        try:
            hits = projection.search(candidate, limit=50)
        except ValueError:
            hits = ()
        for hit in hits:
            if hit.id != candidate and hit.title.casefold() != candidate.casefold():
                continue
            record = projection.get_entity_by_uri(hit.uri)
            if record is not None:
                matches[str(record.uri)] = record
    return tuple(matches[key] for key in sorted(matches))


def _prepare_new_entity_shells(
    proposals: Sequence[ProposedDocument],
    *,
    context_id: str,
    projection: SQLiteProjection,
    resolver: _EntityResolver,
) -> list[_EntityState]:
    states: list[_EntityState] = []
    seen_uris: set[str] = set()
    seen_targets: set[str] = set()
    for index, proposal in enumerate(proposals):
        raw = copy.deepcopy(proposal.document)
        raw["references"] = []
        try:
            document = EntityFrontmatter.model_validate(raw)
        except ValidationError as exc:
            raise _validation_error(exc, prefix=f"$.new_entities[{index}].document") from exc
        _require_local_document_uri(
            document.uri,
            context_id,
            f"$.new_entities[{index}].document.uri",
        )
        entity_type = str(document.entity_type)
        if entity_type in _SPECIAL_NEW_ENTITY_TYPES or entity_type not in _ENTITY_DIRECTORIES:
            raise _invalid_input(
                "new_entities contains a specialized or unsupported entity type.",
                path=f"$.new_entities[{index}].document.entity_type",
            )
        if projection.get_document_by_uri(document.uri) is not None:
            raise EvidenceStateError(
                "EVIDENCE-DOCUMENT-EXISTS",
                "A declared new entity already exists.",
            )
        target = _entity_target(entity_type, document.id)
        if document.uri in seen_uris or target.casefold() in seen_targets:
            raise _invalid_input(
                "new_entities contains a duplicate identity.",
                path=f"$.new_entities[{index}]",
            )
        seen_uris.add(document.uri)
        seen_targets.add(target.casefold())
        state = _EntityState(target, document, proposal.body, None)
        states.append(state)
        resolver.add_declared(
            uri=document.uri,
            identifier=document.id,
            title=document.title,
            aliases=document.aliases,
        )
    return states


def _prepare_task_shells(
    proposals: Sequence[ProposedDocument],
    *,
    context_id: str,
    projection: SQLiteProjection,
    store: CanonicalStore,
    resolver: _EntityResolver,
) -> list[_TaskState]:
    states: list[_TaskState] = []
    seen_uris: set[str] = set()
    for index, proposal in enumerate(proposals):
        raw = copy.deepcopy(proposal.document)
        raw["references"] = []
        try:
            document = Task.model_validate(raw)
        except ValidationError as exc:
            raise _validation_error(exc, prefix=f"$.tasks[{index}].document") from exc
        _require_local_document_uri(document.uri, context_id, f"$.tasks[{index}].document.uri")
        if document.uri in seen_uris:
            raise _invalid_input("tasks contains a duplicate identity.", path=f"$.tasks[{index}]")
        seen_uris.add(document.uri)
        existing = projection.get_task(document.uri)
        if existing is None:
            target = f"03_work/tasks/{quote(document.id, safe='-._~')}.md"
            expected_hash = None
        else:
            target = existing.source_path
            expected_hash = _hash_document(store, target)
        state = _TaskState(target, document, proposal.body, expected_hash)
        states.append(state)
        resolver.add_declared(
            uri=document.uri,
            identifier=document.id,
            title=document.title,
            aliases=document.aliases,
        )
    return states


def _parse_observations(
    proposals: Sequence[Mapping[str, object]],
    *,
    evidence_id: str,
    artifact_ref: str,
    context_id: str,
    projection: SQLiteProjection,
    resolver: _EntityResolver,
) -> tuple[tuple[Observation, ...], dict[str, str]]:
    observation_uris: dict[str, str] = {}
    for index, proposal in enumerate(proposals):
        identifier = proposal.get("id")
        if not isinstance(identifier, str):
            raise _invalid_input(
                "Every observation requires a valid ID.", path=f"$.observations[{index}].id"
            )
        try:
            parsed = ObservationId.parse(identifier)
        except ValueError as exc:
            raise _invalid_input(
                "Every observation requires a valid ID.", path=f"$.observations[{index}].id"
            ) from exc
        if not parsed.value.startswith(f"{evidence_id}#"):
            raise _invalid_input(
                "Observation IDs must belong to the proposed evidence note.",
                path=f"$.observations[{index}].id",
            )
        if identifier in observation_uris:
            raise _invalid_input(
                "Observation IDs must be unique.", path=f"$.observations[{index}].id"
            )
        observation_uris[identifier] = str(WorkctxUri(context_id, "observation", identifier))

    observations: list[Observation] = []
    for index, proposal in enumerate(proposals):
        raw = copy.deepcopy(dict(proposal))
        related = raw.get("related", [])
        if isinstance(related, list):
            for related_index, reference in enumerate(related):
                if not isinstance(reference, dict) or not isinstance(reference.get("target"), str):
                    continue
                reference["target"] = resolver.resolve_target(
                    cast(str, reference["target"]),
                    path=f"$.observations[{index}].related[{related_index}].target",
                )
                sources = reference.get("source_observations", [])
                if isinstance(sources, list):
                    reference["source_observations"] = [
                        _observation_uri(
                            source,
                            path=(
                                f"$.observations[{index}].related[{related_index}]"
                                f".source_observations[{source_index}]"
                            ),
                            staged=observation_uris,
                            projection=projection,
                            context_id=context_id,
                        )
                        for source_index, source in enumerate(sources)
                    ]
        derived = raw.get("derived_from", [])
        if isinstance(derived, list):
            raw["derived_from"] = [
                _observation_uri(
                    source,
                    path=f"$.observations[{index}].derived_from[{source_index}]",
                    staged=observation_uris,
                    projection=projection,
                    context_id=context_id,
                )
                for source_index, source in enumerate(derived)
            ]
        try:
            observation = Observation.model_validate(raw)
        except ValidationError as exc:
            raise _validation_error(exc, prefix=f"$.observations[{index}]") from exc
        if observation.source.ref != artifact_ref:
            raise _source_mismatch(f"$.observations[{index}].source.ref")
        observations.append(observation)
    return tuple(observations), observation_uris


def _restore_authored_documents(
    authored: EvidenceStagingPayload,
    entity_states: Sequence[_EntityState],
    task_states: Sequence[_TaskState],
    *,
    resolver: _EntityResolver,
    observation_uris: Mapping[str, str],
    projection: SQLiteProjection,
    context_id: str,
) -> None:
    pairs = zip(authored.new_entities, entity_states, strict=True)
    for index, (proposal, entity_state) in enumerate(pairs):
        raw = cast(dict[str, object], copy.deepcopy(proposal.document))
        raw["references"] = _resolved_reference_payloads(
            raw.get("references", []),
            prefix=f"$.new_entities[{index}].document.references",
            resolver=resolver,
            observation_uris=observation_uris,
            projection=projection,
            context_id=context_id,
        )
        try:
            entity_state.document = EntityFrontmatter.model_validate(raw)
        except ValidationError as exc:
            raise _validation_error(exc, prefix=f"$.new_entities[{index}].document") from exc

    for index, (proposal, task_state) in enumerate(zip(authored.tasks, task_states, strict=True)):
        raw = cast(dict[str, object], copy.deepcopy(proposal.document))
        raw["references"] = _resolved_reference_payloads(
            raw.get("references", []),
            prefix=f"$.tasks[{index}].document.references",
            resolver=resolver,
            observation_uris=observation_uris,
            projection=projection,
            context_id=context_id,
        )
        for field_name in ("owner", "requester"):
            value = raw.get(field_name)
            if isinstance(value, str) and value:
                raw[field_name] = resolver.resolve_entity(
                    value, path=f"$.tasks[{index}].document.{field_name}"
                )
        waiting_on = raw.get("waiting_on", [])
        if isinstance(waiting_on, list):
            raw["waiting_on"] = [
                resolver.resolve_target(
                    value, path=f"$.tasks[{index}].document.waiting_on[{item_index}]"
                )
                for item_index, value in enumerate(waiting_on)
                if isinstance(value, str)
            ]
        sources = raw.get("source_observations", [])
        if isinstance(sources, list):
            raw["source_observations"] = [
                _observation_uri(
                    source,
                    path=f"$.tasks[{index}].document.source_observations[{source_index}]",
                    staged=observation_uris,
                    projection=projection,
                    context_id=context_id,
                )
                for source_index, source in enumerate(sources)
            ]
        for field_name in ("dependencies", "blockers"):
            values = raw.get(field_name, [])
            if isinstance(values, list):
                raw[field_name] = [
                    _task_reference(
                        value,
                        path=f"$.tasks[{index}].document.{field_name}[{item_index}]",
                        resolver=resolver,
                    )
                    for item_index, value in enumerate(values)
                ]
        try:
            task = Task.model_validate(raw)
        except ValidationError as exc:
            raise _validation_error(exc, prefix=f"$.tasks[{index}].document") from exc
        if not any(source in observation_uris.values() for source in task.source_observations):
            raise _invalid_input(
                "Every proposed task change requires a staged source observation.",
                path=f"$.tasks[{index}].document.source_observations",
            )
        task_state.document = task


def _prepare_claims(
    proposals: Sequence[ProposedDocument],
    *,
    resolver: _EntityResolver,
    observation_uris: Mapping[str, str],
    projection: SQLiteProjection,
    context_id: str,
) -> tuple[StagedClaimDocument, ...]:
    claims: list[StagedClaimDocument] = []
    seen: set[str] = set()
    for index, proposal in enumerate(proposals):
        raw = copy.deepcopy(proposal.document)
        subject = raw.get("subject")
        if isinstance(subject, str):
            raw["subject"] = resolver.resolve_entity(
                subject, path=f"$.claims[{index}].document.subject"
            )
        sources = raw.get("source_observations", [])
        if isinstance(sources, list):
            raw["source_observations"] = [
                _observation_uri(
                    source,
                    path=f"$.claims[{index}].document.source_observations[{source_index}]",
                    staged=observation_uris,
                    projection=projection,
                    context_id=context_id,
                )
                for source_index, source in enumerate(sources)
            ]
        try:
            claim = Claim.model_validate(raw)
        except ValidationError as exc:
            raise _validation_error(exc, prefix=f"$.claims[{index}].document") from exc
        if claim.id in seen or projection.get_claim(claim.id) is not None:
            raise EvidenceStateError(
                "EVIDENCE-DOCUMENT-EXISTS",
                "A proposed claim already exists.",
            )
        if not any(
            str(source) in observation_uris.values() for source in claim.source_observations
        ):
            raise _invalid_input(
                "Every proposed claim requires a staged source observation.",
                path=f"$.claims[{index}].document.source_observations",
            )
        seen.add(claim.id)
        claims.append(
            StagedClaimDocument(
                target=f"02_knowledge/claims/{quote(claim.id, safe='-._~')}.md",
                document=claim,
                body=proposal.body,
            )
        )
    return tuple(claims)


def _resolve_and_attach_relations(
    authored: EvidenceStagingPayload,
    *,
    resolver: _EntityResolver,
    observation_uris: Mapping[str, str],
    projection: SQLiteProjection,
    store: CanonicalStore,
    states_by_uri: dict[str, _EntityState | _TaskState],
    context_id: str,
) -> tuple[ResolvedRelation, ...]:
    authored_entity_states = [
        state
        for state in states_by_uri.values()
        if isinstance(state, _EntityState)
        and state.expected_hash is None
        and state.document.entity_type != "evidence"
    ]
    _restore_authored_documents(
        authored,
        authored_entity_states,
        [state for state in states_by_uri.values() if isinstance(state, _TaskState)],
        resolver=resolver,
        observation_uris=observation_uris,
        projection=projection,
        context_id=context_id,
    )
    resolved: list[ResolvedRelation] = []
    for index, relation in enumerate(authored.relations):
        source_uri = resolver.resolve_entity(relation.source, path=f"$.relations[{index}].source")
        target = resolver.resolve_target(relation.target, path=f"$.relations[{index}].target")
        sources = tuple(
            _observation_uri(
                source,
                path=f"$.relations[{index}].source_observations[{source_index}]",
                staged=observation_uris,
                projection=projection,
                context_id=context_id,
            )
            for source_index, source in enumerate(relation.source_observations)
        )
        if not any(source in observation_uris.values() for source in sources):
            raise _invalid_input(
                "Every proposed relation requires a staged source observation.",
                path=f"$.relations[{index}].source_observations",
            )
        reference_payload: dict[str, object] = {
            "relation": relation.relation.value,
            "target": target,
            "source_observations": list(sources),
            "valid_from": relation.valid_from,
            "valid_to": relation.valid_to,
        }
        if relation.confidence is not None:
            reference_payload["confidence"] = relation.confidence.value
        if relation.note is not None:
            reference_payload["note"] = relation.note
        reference = TypedReference.model_validate(reference_payload)
        state = states_by_uri.get(source_uri)
        if state is None:
            state = _load_existing_relation_source(store, projection, source_uri)
            states_by_uri[source_uri] = state
        state.document = _append_reference(state.document, reference)
        resolved.append(ResolvedRelation(source_uri=source_uri, reference=reference))
    return tuple(resolved)


def _load_existing_relation_source(
    store: CanonicalStore,
    projection: SQLiteProjection,
    uri: str,
) -> _EntityState | _TaskState:
    record = projection.get_document_by_uri(uri)
    if isinstance(record, TaskRecord):
        task_document = store.read_task(record.source_path)
        return _TaskState(
            record.source_path,
            task_document.frontmatter,
            task_document.body,
            _hash_document(store, record.source_path),
        )
    if isinstance(record, EntityRecord):
        entity_document = store.read_entity(record.source_path)
        return _EntityState(
            record.source_path,
            entity_document.frontmatter,
            entity_document.body,
            _hash_document(store, record.source_path),
        )
    raise _invalid_input(
        "A relation source must be an evidence note, entity, or task.",
        path="$.relations",
    )


def _append_reference(
    document: EntityFrontmatter | Task,
    reference: TypedReference,
) -> EntityFrontmatter | Task:
    raw = document.model_dump(mode="python")
    references = [item.model_dump(mode="json") for item in document.references]
    candidate = reference.model_dump(mode="json", exclude_none=True)
    if candidate not in references:
        references.append(candidate)
    raw["references"] = references
    if isinstance(document, Task):
        return Task.model_validate(raw)
    return EntityFrontmatter.model_validate(raw)


def _resolved_reference_payloads(
    value: object,
    *,
    prefix: str,
    resolver: _EntityResolver,
    observation_uris: Mapping[str, str],
    projection: SQLiteProjection,
    context_id: str,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise _invalid_input("references must be an array.", path=prefix)
    resolved: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _invalid_input("references entries must be objects.", path=f"{prefix}[{index}]")
        reference = copy.deepcopy(item)
        target = reference.get("target")
        if isinstance(target, str):
            reference["target"] = resolver.resolve_target(target, path=f"{prefix}[{index}].target")
        sources = reference.get("source_observations", [])
        if isinstance(sources, list):
            reference["source_observations"] = [
                _observation_uri(
                    source,
                    path=f"{prefix}[{index}].source_observations[{source_index}]",
                    staged=observation_uris,
                    projection=projection,
                    context_id=context_id,
                )
                for source_index, source in enumerate(sources)
            ]
        resolved.append(cast(dict[str, object], reference))
    return resolved


def _observation_uri(
    value: object,
    *,
    path: str,
    staged: Mapping[str, str],
    projection: SQLiteProjection,
    context_id: str,
) -> str:
    if not isinstance(value, str):
        raise _invalid_input("Observation references must be strings.", path=path)
    if value in staged:
        return staged[value]
    try:
        canonical = normalize_workctx_uri(value)
        parsed = WorkctxUri.parse(canonical)
        ObservationId.parse(parsed.entity_id)
    except ValueError as exc:
        raise _invalid_input("An observation reference is invalid.", path=path) from exc
    if parsed.context_id != context_id:
        raise EvidenceContextError(path=path)
    if parsed.entity_type != "observation":
        raise _invalid_input("An observation reference has the wrong entity type.", path=path)
    if canonical not in staged.values() and projection.get_observation(canonical) is None:
        raise _invalid_input("An observation reference does not resolve.", path=path)
    return canonical


def _task_reference(value: object, *, path: str, resolver: _EntityResolver) -> str:
    if not isinstance(value, str):
        raise _invalid_input("Task relations must be strings.", path=path)
    if _TASK_ID.fullmatch(value) is not None:
        resolved = resolver.resolve_entity(value, path=path)
        if WorkctxUri.parse(resolved).entity_type != "task":
            raise _invalid_input("Task relations must identify tasks.", path=path)
        return value
    resolved = resolver.resolve_entity(value, path=path)
    if WorkctxUri.parse(resolved).entity_type != "task":
        raise _invalid_input("Task relations must identify tasks.", path=path)
    return resolved


def _require_local_document_uri(uri: str, context_id: str, path: str) -> None:
    try:
        parsed = WorkctxUri.parse(uri)
        EntityType(parsed.entity_type)
    except ValueError as exc:
        raise _invalid_input("A proposed document URI is invalid.", path=path) from exc
    if parsed.context_id != context_id:
        raise EvidenceContextError(path=path)


def _entity_target(entity_type: str, identifier: str) -> str:
    directory = _ENTITY_DIRECTORIES[entity_type]
    return f"{directory}/{quote(identifier, safe='-._~')}.md"


def _hash_document(store: CanonicalStore, relative_path: str) -> str:
    path = store.resolve_path(relative_path, zones=_DOCUMENT_ZONES)
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _staged_entity(state: _EntityState) -> StagedEntityDocument:
    return StagedEntityDocument(
        operation="create" if state.expected_hash is None else "update",
        target=state.target,
        document=state.document,
        body=state.body,
        expected_hash=state.expected_hash,
    )


def _staged_task(state: _TaskState) -> StagedTaskDocument:
    return StagedTaskDocument(
        operation="create" if state.expected_hash is None else "update",
        target=state.target,
        document=state.document,
        body=state.body,
        expected_hash=state.expected_hash,
    )


def _proposal_id(created_at: datetime, artifact_id: str) -> str:
    stamp = created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = artifact_id.casefold().replace("_", "-")
    return f"TXP-{stamp}-process-{slug}"


def _reject_possible_secrets(value: object) -> None:
    if isinstance(value, BaseModel):
        _reject_possible_secrets(value.model_dump(mode="python"))
        return
    if isinstance(value, str):
        if contains_possible_secret(value):
            raise EvidenceInputError(
                "EVIDENCE-POSSIBLE-SECRET",
                "The staging payload contains a possible secret.",
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and contains_possible_secret(key):
                raise EvidenceInputError(
                    "EVIDENCE-POSSIBLE-SECRET",
                    "The staging payload contains a possible secret.",
                )
            _reject_possible_secrets(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _reject_possible_secrets(item)


def _source_mismatch(path: str) -> EvidenceInputError:
    return EvidenceInputError(
        "EVIDENCE-SOURCE-MISMATCH",
        "A source reference does not match the registered artifact hash.",
        path=path,
    )


def _unknown_entity(path: str) -> EvidenceInputError:
    return EvidenceInputError(
        "EVIDENCE-UNKNOWN-ENTITY",
        "A proposed entity reference is unknown and has no explicit declaration.",
        path=path,
    )


def _invalid_input(message: str, *, path: str | None = None) -> EvidenceInputError:
    return EvidenceInputError("EVIDENCE-INVALID-PAYLOAD", message, path=path)


def _validation_error(
    error: ValidationError,
    *,
    prefix: str = "$",
    code: str = "EVIDENCE-INVALID-PAYLOAD",
) -> EvidenceInputError:
    first = error.errors(include_url=False)[0]
    path = prefix + _format_location(first.get("loc", ()))
    return EvidenceInputError(code, "The staging payload violates a typed contract.", path=path)


def _format_location(location: object) -> str:
    if not isinstance(location, tuple):
        return ""
    rendered = ""
    for part in location:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


__all__ = [
    "begin_processing",
    "build_evidence_proposal",
    "complete_processing",
    "stage_observations",
]
