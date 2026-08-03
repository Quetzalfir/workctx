"""Meaning-preserving normalization through the integrated domain models."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from pydantic import JsonValue, ValidationError

from workctx.domain import (
    Claim,
    ClaimStatus,
    Confidence,
    EntityFrontmatter,
    Observation,
    ObservationKind,
    RelationType,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
    WorkctxUri,
    validate_durable_reference,
)
from workctx.domain.transactions import (
    ClaimDocumentPayload,
    CreateOperation,
    EntityDocumentPayload,
    ObservationDocumentPayload,
    TaskDocumentPayload,
)
from workctx.migration.errors import MigrationError
from workctx.migration.inventory import LegacyDocument
from workctx.migration.mapping import MigrationPlan, PlannedDocument
from workctx.migration.models import MappingAction, MappingRecord, PrecisionLoss

_BASE_FIELDS = frozenset(
    {
        "aliases",
        "artifact_path",
        "artifact_ref",
        "classification",
        "confidence",
        "created_at",
        "derived_only",
        "entity_type",
        "id",
        "observations",
        "original_path",
        "raw_path",
        "raw_unavailable",
        "references",
        "schema_version",
        "source_path",
        "tags",
        "title",
        "type",
        "updated_at",
        "uri",
    }
)
_TASK_FIELDS = frozenset(
    {
        "blockers",
        "deadline",
        "dependencies",
        "due",
        "due_at",
        "next_action",
        "owner",
        "parent",
        "parent_task",
        "priority",
        "requester",
        "root_task",
        "source_observations",
        "status",
        "task_type",
        "waiting_on",
    }
)
_MUTABLE_FIELD_ORDER = (
    "status",
    "owner",
    "ownership",
    "deadline",
    "due",
    "due_at",
    "waiting_on",
    "next_action",
    "dependencies",
    "blockers",
    "architecture",
)
_WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s<>'\"]+)")
_POSIX_ABSOLUTE = re.compile(
    r"(?<![:A-Za-z0-9])/(?:Users|Volumes|etc|home|mnt|opt|private|tmp|usr|var)/[^\s<>'\"]+"
)
_MARKDOWN_LINK = re.compile(r"(?P<image>!)?\[(?P<label>[^\]]*)\]\((?P<target>[^)]+)\)")
_WIKI_LINK = re.compile(r"\[\[(?P<target>[^\]|#]+)(?:#[^\]|]+)?(?:\|(?P<label>[^\]]+))?\]\]")
_WORKCTX_URI = re.compile(r"workctx://[^\s<>()\[\]{}\"'`]+")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


type MigrationCreateOperation = CreateOperation


@dataclass(frozen=True, slots=True)
class NormalizedMigration:
    operations: tuple[MigrationCreateOperation, ...]
    mappings: tuple[MappingRecord, ...]
    precision_losses: tuple[PrecisionLoss, ...]
    source_references: tuple[str, ...]


class _ClaimAllocator:
    def __init__(self, year: int, used: set[str]) -> None:
        self._year = year
        self._used = set(used)
        self._sequence = 0

    def allocate(self) -> str:
        while self._sequence < 99_999:
            self._sequence += 1
            candidate = f"CLM-{self._year:04d}-{self._sequence:05d}"
            if candidate not in self._used:
                self._used.add(candidate)
                return candidate
        raise MigrationError("The claim ID sequence is exhausted for the migration year.")


class _Normalizer:
    def __init__(self, plan: MigrationPlan, migration_time: datetime) -> None:
        self._plan = plan
        self._migration_time = _utc_time(migration_time)
        self._documents = plan.document_by_source()
        self._documents_casefold = {key.casefold(): value for key, value in self._documents.items()}
        self._old_ids = plan.documents_by_old_id()
        self._artifacts = plan.artifact_by_source()
        self._artifacts_casefold = {key.casefold(): value for key, value in self._artifacts.items()}
        used_claim_ids = {
            document.target_id
            for document in plan.documents
            if document.source.entity_type == "claim"
        }
        self._claim_ids = _ClaimAllocator(self._migration_time.year, used_claim_ids)
        self._operations: list[MigrationCreateOperation] = []
        self._mappings: list[MappingRecord] = list(plan.mappings)
        self._precision: list[PrecisionLoss] = list(plan.precision_losses)

    def normalize(self) -> NormalizedMigration:
        for planned in sorted(
            self._plan.documents,
            key=lambda item: (
                item.source.entity_type == "task",
                item.source.relative_path.casefold(),
            ),
        ):
            try:
                self._normalize_document(planned)
            except (TypeError, ValueError, ValidationError) as exc:
                raise MigrationError(
                    f"Legacy document normalization failed at {planned.source.report_path}."
                ) from exc
        paths = [operation.target for operation in self._operations]
        if len(paths) != len({path.casefold() for path in paths}):
            raise MigrationError("Normalized migration targets are not unique.")
        return NormalizedMigration(
            operations=tuple(sorted(self._operations, key=lambda item: item.target.casefold())),
            mappings=tuple(
                sorted(
                    set(self._mappings),
                    key=lambda item: (
                        item.source_path.casefold(),
                        item.action.value,
                        item.target_uri or "",
                    ),
                )
            ),
            precision_losses=tuple(
                sorted(
                    set(self._precision),
                    key=lambda item: (item.path.casefold(), item.code, item.message),
                )
            ),
            source_references=tuple(
                sorted({artifact.reference for artifact in self._plan.artifacts})
            ),
        )

    def _normalize_document(self, planned: PlannedDocument) -> None:
        source = planned.source
        if source.entity_type == "observation":
            self._normalize_standalone_observation(planned)
            return

        timestamps = self._timestamps(source)
        observation_fields = (
            ("object",)
            if source.entity_type == "claim"
            else tuple(key for key in _MUTABLE_FIELD_ORDER if key in source.frontmatter)
        )
        observations, evidence_operation = self._provenance_observations(
            planned,
            observation_fields,
            timestamps.updated_at,
        )
        if evidence_operation is not None:
            self._operations.append(evidence_operation)

        if source.entity_type == "claim":
            self._normalize_legacy_claim(planned, observations, timestamps)
            return
        if source.entity_type == "task":
            task_model, body = self._task(planned, observations, timestamps)
            self._operations.append(
                CreateOperation(
                    op="create",
                    target=planned.target_path,
                    payload=TaskDocumentPayload(kind="task", document=task_model, body=body),
                )
            )
            self._generate_mutable_claims(planned, observations, timestamps.updated_at)
            return

        embedded = observations if source.entity_type == "evidence" else ()
        embedded = (*embedded, *self._legacy_embedded_observations(planned, len(embedded)))
        entity_model, body = self._entity(planned, embedded, timestamps)
        self._operations.append(
            CreateOperation(
                op="create",
                target=planned.target_path,
                payload=EntityDocumentPayload(kind="entity", document=entity_model, body=body),
            )
        )
        self._generate_mutable_claims(planned, observations, timestamps.updated_at)

    def _entity(
        self,
        planned: PlannedDocument,
        observations: tuple[Observation, ...],
        timestamps: _Timestamps,
    ) -> tuple[EntityFrontmatter, str]:
        source = planned.source
        data = self._base_entity_data(planned, timestamps)
        excluded = _BASE_FIELDS | {"status"}
        data.update(self._extras(source, excluded))
        if source.entity_type == "evidence":
            artifact_ref, raw_unavailable = self._evidence_artifact(source)
            data["artifact_ref"] = artifact_ref
            data["raw_unavailable"] = raw_unavailable
            data["provenance_quality"] = "derived_only" if raw_unavailable else "preserved_raw"
            if observations:
                data["observations"] = [
                    observation.model_dump(mode="json") for observation in observations
                ]
        model = EntityFrontmatter.model_validate(data)
        return model, self._rewrite_body(source.body, source)

    def _task(
        self,
        planned: PlannedDocument,
        observations: tuple[Observation, ...],
        timestamps: _Timestamps,
    ) -> tuple[Task, str]:
        source = planned.source
        base = self._base_entity_data(planned, timestamps)
        base.update(self._extras(source, _BASE_FIELDS | _TASK_FIELDS))
        status = self._task_status(source)
        priority = self._task_priority(source)
        due_at = self._optional_timestamp(
            source,
            source.frontmatter.get(
                "due_at",
                source.frontmatter.get("due", source.frontmatter.get("deadline")),
            ),
            "due_at",
        )
        dependencies = self._task_relations(source, "dependencies")
        blockers = self._task_relations(source, "blockers")
        next_action = source.frontmatter.get("next_action")
        if not isinstance(next_action, str) or not next_action.strip():
            next_action = "Review the migrated task and define the next observable action."
            self._loss(
                source,
                "MIG-TASK-NEXT-ACTION-DEFAULTED",
                "A missing next action was replaced by an explicit review action.",
            )
        base.update(
            {
                "entity_type": "task",
                "task_type": (
                    TaskType.PARENT if planned.task_parent_id is None else TaskType.SUBTASK
                ),
                "parent_task": planned.task_parent_id,
                "root_task": planned.task_root_id or planned.target_id,
                "priority": priority,
                "status": status,
                "owner": self._optional_text(source.frontmatter.get("owner"), source),
                "requester": self._optional_text(source.frontmatter.get("requester"), source),
                "waiting_on": self._text_list(source.frontmatter.get("waiting_on"), source),
                "due_at": due_at,
                "next_action": self._sanitize_text(next_action),
                "dependencies": dependencies,
                "blockers": blockers,
                "source_observations": [
                    self._observation_uri(observation.id) for observation in observations
                ],
            }
        )
        authored_parent = source.frontmatter.get("parent_task", source.frontmatter.get("parent"))
        if (
            isinstance(authored_parent, str)
            and planned.task_parent_id is not None
            and authored_parent != planned.task_parent_id
        ):
            base["legacy_parent"] = self._sanitize_text(authored_parent)
        model = Task.model_validate(base)
        return model, self._rewrite_body(source.body, source)

    def _normalize_legacy_claim(
        self,
        planned: PlannedDocument,
        observations: tuple[Observation, ...],
        timestamps: _Timestamps,
    ) -> None:
        source = planned.source
        if not observations:
            raise MigrationError(
                f"Legacy claim at {source.report_path} has no recoverable source locator."
            )
        subject_value = source.frontmatter.get("subject")
        subject = self._map_reference(subject_value, source)
        if not subject.startswith("workctx://"):
            raise MigrationError(f"Legacy claim subject did not resolve at {source.report_path}.")
        predicate = source.frontmatter.get("predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            raise MigrationError(f"Legacy claim predicate is missing at {source.report_path}.")
        claim = Claim.model_validate(
            {
                "schema_version": 1,
                "id": planned.target_id,
                "subject": subject,
                "predicate": predicate.strip(),
                "object": self._json_value(source.frontmatter.get("object"), source),
                "observed_at": timestamps.updated_at,
                "valid_from": self._optional_timestamp(
                    source, source.frontmatter.get("valid_from"), "valid_from"
                ),
                "valid_to": self._optional_timestamp(
                    source, source.frontmatter.get("valid_to"), "valid_to"
                ),
                "status": self._claim_status(source.frontmatter.get("status")),
                "supersedes": self._mapped_claim_id(source.frontmatter.get("supersedes")),
                "superseded_by": self._mapped_claim_id(source.frontmatter.get("superseded_by")),
                "confidence": self._confidence(source.frontmatter.get("confidence")),
                "source_observations": [
                    self._observation_uri(observation.id) for observation in observations
                ],
            }
        )
        self._operations.append(
            CreateOperation(
                op="create",
                target=planned.target_path,
                payload=ClaimDocumentPayload(
                    kind="claim",
                    document=claim,
                    body=self._rewrite_body(source.body, source),
                ),
            )
        )

    def _normalize_standalone_observation(self, planned: PlannedDocument) -> None:
        source = planned.source
        statement = source.frontmatter.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise MigrationError(
                f"Legacy observation statement is missing at {source.report_path}."
            )
        span = source.frontmatter_spans.get("statement")
        if span is None:
            raise MigrationError(
                f"Legacy observation locator is not recoverable at {source.report_path}."
            )
        artifact = self._artifacts[source.relative_path]
        observation = Observation.model_validate(
            {
                "id": planned.target_id,
                "kind": self._observation_kind(source.frontmatter.get("kind")),
                "statement": statement.strip(),
                "confidence": self._confidence(source.frontmatter.get("confidence")),
                "source": {
                    "ref": artifact.reference,
                    "locator": {
                        "type": "line_range",
                        "start_line": span[0],
                        "end_line": span[1],
                    },
                },
                "observed_at": self._timestamps(source).updated_at,
                "valid_from": None,
                "valid_to": None,
                "derived_from": [],
                "related": [],
            }
        )
        self._operations.append(
            CreateOperation(
                op="create",
                target=planned.target_path,
                payload=ObservationDocumentPayload(
                    kind="observation",
                    document=observation,
                    body=self._rewrite_body(source.body, source),
                ),
            )
        )

    def _base_entity_data(
        self,
        planned: PlannedDocument,
        timestamps: _Timestamps,
    ) -> dict[str, Any]:
        source = planned.source
        title = source.frontmatter.get("title", source.frontmatter.get("name"))
        if not isinstance(title, str) or not title.strip():
            title = PurePosixPath(source.relative_path).stem.replace("-", " ").strip().title()
            self._loss(
                source,
                "MIG-TITLE-DERIVED",
                "A missing title was derived from the legacy filename.",
            )
        status = source.frontmatter.get("status")
        return {
            "schema_version": 1,
            "id": planned.target_id,
            "entity_type": source.entity_type,
            "title": self._sanitize_text(title),
            "uri": planned.target_uri,
            "aliases": self._text_list(source.frontmatter.get("aliases"), source),
            "status": self._sanitize_text(status) if isinstance(status, str) else None,
            "confidence": self._optional_confidence(source.frontmatter.get("confidence")),
            "tags": self._text_list(source.frontmatter.get("tags"), source),
            "references": self._references(source),
            "created_at": timestamps.created_at,
            "updated_at": timestamps.updated_at,
        }

    def _provenance_observations(
        self,
        planned: PlannedDocument,
        fields: tuple[str, ...],
        observed_at: datetime,
    ) -> tuple[tuple[Observation, ...], CreateOperation | None]:
        source = planned.source
        evidence_id = (
            planned.target_id
            if source.entity_type == "evidence"
            else planned.provenance_evidence_id
        )
        if evidence_id is None:
            return (), None
        artifact = self._artifacts[source.relative_path]
        observations: list[Observation] = []
        for field_name in fields:
            span = source.frontmatter_spans.get(field_name)
            if span is None:
                self._loss(
                    source,
                    "MIG-LOCATOR-UNAVAILABLE",
                    (
                        f"Mutable field '{field_name}' had no recoverable line locator; "
                        "no claim was made."
                    ),
                )
                continue
            observation_id = f"{evidence_id}#OBS-{len(observations) + 1:03d}"
            observations.append(
                Observation.model_validate(
                    {
                        "id": observation_id,
                        "kind": "fact",
                        "statement": f"The legacy source records mutable field '{field_name}'.",
                        "confidence": "medium",
                        "source": {
                            "ref": artifact.reference,
                            "locator": {
                                "type": "line_range",
                                "start_line": span[0],
                                "end_line": span[1],
                            },
                        },
                        "observed_at": observed_at,
                        "valid_from": None,
                        "valid_to": None,
                        "derived_from": [],
                        "related": [],
                    }
                )
            )
        if source.entity_type == "evidence" or not observations:
            return tuple(observations), None

        evidence_uri = str(WorkctxUri(self._plan.context_id, "evidence", evidence_id))
        evidence = EntityFrontmatter.model_validate(
            {
                "schema_version": 1,
                "id": evidence_id,
                "entity_type": "evidence",
                "title": f"Migration provenance for {source.report_path}",
                "uri": evidence_uri,
                "aliases": [],
                "status": "active",
                "confidence": "medium",
                "tags": ["legacy-migration", "provenance"],
                "references": [
                    {
                        "relation": "evidenced_by",
                        "target": artifact.reference,
                        "confidence": "high",
                    }
                ],
                "created_at": observed_at,
                "updated_at": observed_at,
                "artifact_ref": artifact.reference,
                "raw_unavailable": False,
                "provenance_quality": "legacy_source_record",
                "observations": [
                    observation.model_dump(mode="json") for observation in observations
                ],
            }
        )
        target = f"02_knowledge/evidence/{evidence_id}.md"
        operation = CreateOperation(
            op="create",
            target=target,
            payload=EntityDocumentPayload(
                kind="entity",
                document=evidence,
                body=f"Migration provenance for `{source.report_path}`.\n",
            ),
        )
        return tuple(observations), operation

    def _legacy_embedded_observations(
        self,
        planned: PlannedDocument,
        starting_count: int,
    ) -> tuple[Observation, ...]:
        raw = planned.source.frontmatter.get("observations")
        if not isinstance(raw, list):
            return ()
        migrated: list[Observation] = []
        for item in raw:
            if not isinstance(item, dict):
                self._loss(
                    planned.source,
                    "MIG-OBSERVATION-SKIPPED",
                    "A legacy observation without structured source data was skipped.",
                )
                continue
            statement = item.get("statement")
            source_payload = item.get("source")
            if not isinstance(statement, str) or not isinstance(source_payload, dict):
                self._loss(
                    planned.source,
                    "MIG-OBSERVATION-SKIPPED",
                    "A legacy observation without a recoverable locator was skipped.",
                )
                continue
            locator = source_payload.get("locator")
            source_ref = self._map_artifact_reference(source_payload.get("ref"), planned.source)
            if source_ref is None or not isinstance(locator, dict):
                self._loss(
                    planned.source,
                    "MIG-OBSERVATION-SKIPPED",
                    "A legacy observation without a recoverable artifact locator was skipped.",
                )
                continue
            identifier = f"{planned.target_id}#OBS-{starting_count + len(migrated) + 1:03d}"
            try:
                observation = Observation.model_validate(
                    {
                        "id": identifier,
                        "kind": self._observation_kind(item.get("kind")),
                        "statement": statement,
                        "confidence": self._confidence(item.get("confidence")),
                        "source": {"ref": source_ref, "locator": locator},
                        "observed_at": item.get("observed_at"),
                        "valid_from": item.get("valid_from"),
                        "valid_to": item.get("valid_to"),
                        "derived_from": [],
                        "related": [],
                    }
                )
            except ValidationError:
                self._loss(
                    planned.source,
                    "MIG-OBSERVATION-SKIPPED",
                    "A legacy observation failed the canonical locator model and was skipped.",
                )
                continue
            migrated.append(observation)
        return tuple(migrated)

    def _generate_mutable_claims(
        self,
        planned: PlannedDocument,
        observations: tuple[Observation, ...],
        observed_at: datetime,
    ) -> None:
        source = planned.source
        observations_by_field = {
            observation.statement.removeprefix(
                "The legacy source records mutable field '"
            ).removesuffix("'."): observation
            for observation in observations
        }
        values = self._mutable_values(source)
        for field_name, predicate, value in values:
            observation = observations_by_field.get(field_name)
            if observation is None:
                continue
            claim_id = self._claim_ids.allocate()
            claim = Claim.model_validate(
                {
                    "schema_version": 1,
                    "id": claim_id,
                    "subject": planned.target_uri,
                    "predicate": predicate,
                    "object": value,
                    "observed_at": observed_at,
                    "valid_from": observed_at,
                    "valid_to": None,
                    "status": ClaimStatus.CURRENT,
                    "supersedes": None,
                    "superseded_by": None,
                    "confidence": self._confidence(source.frontmatter.get("confidence")),
                    "source_observations": [self._observation_uri(observation.id)],
                }
            )
            target_path = f"02_knowledge/claims/{claim_id}.md"
            self._operations.append(
                CreateOperation(
                    op="create",
                    target=target_path,
                    payload=ClaimDocumentPayload(
                        kind="claim",
                        document=claim,
                        body=(
                            f"Migrated mutable field `{predicate}` from legacy source "
                            f"`{source.report_path}`.\n"
                        ),
                    ),
                )
            )
            claim_uri = str(WorkctxUri(self._plan.context_id, "claim", claim_id))
            self._mappings.append(
                MappingRecord(
                    source_path=source.report_path,
                    target_id=claim_id,
                    target_uri=claim_uri,
                    target_path=target_path,
                    action=MappingAction.MIGRATE,
                    note=f"Mutable-state claim for {predicate}.",
                )
            )

    def _mutable_values(
        self,
        source: LegacyDocument,
    ) -> list[tuple[str, str, JsonValue]]:
        values: list[tuple[str, str, JsonValue]] = []
        for field_name in _MUTABLE_FIELD_ORDER:
            if field_name not in source.frontmatter:
                continue
            if field_name in {"dependencies", "blockers"}:
                authored = source.frontmatter[field_name]
                items = authored if isinstance(authored, list) else [authored]
                value: object = [self._map_reference(item, source) for item in items]
            else:
                value = self._json_value(source.frontmatter[field_name], source)
            predicate = {
                "deadline": "due",
                "due_at": "due",
                "ownership": "owner",
            }.get(field_name, field_name)
            values.append((field_name, predicate, cast(JsonValue, value)))
        return values

    def _references(self, source: LegacyDocument) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        raw = source.frontmatter.get("references")
        values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        for authored in values:
            relation: RelationType = RelationType.RELATED_TO
            target_value: object = authored
            confidence: str | None = None
            note: str | None = None
            if isinstance(authored, dict):
                target_value = authored.get("target", authored.get("ref", authored.get("path")))
                relation_value = authored.get("relation", authored.get("type"))
                try:
                    relation = RelationType(str(relation_value))
                except ValueError:
                    self._loss(
                        source,
                        "MIG-REFERENCE-RELATION-DOWNGRADED",
                        "An unknown relation was downgraded to related_to.",
                    )
                confidence_value = authored.get("confidence")
                if confidence_value in {item.value for item in Confidence}:
                    confidence = str(confidence_value)
                note_value = authored.get("note")
                if isinstance(note_value, str):
                    note = self._sanitize_text(note_value)
            target = self._map_reference(target_value, source)
            if relation in {RelationType.DEPENDS_ON, RelationType.BLOCKS} and not target.startswith(
                f"workctx://{self._plan.context_id}/task/"
            ):
                relation = RelationType.RELATED_TO
                self._loss(
                    source,
                    "MIG-REFERENCE-RELATION-DOWNGRADED",
                    "An unresolved task relation was retained as related_to.",
                )
            item: dict[str, Any] = {"relation": relation.value, "target": target}
            if confidence is not None:
                item["confidence"] = confidence
            if note is not None:
                item["note"] = note
            references.append(item)

        artifact = self._artifacts[source.relative_path]
        references.append(
            {
                "relation": "evidenced_by",
                "target": artifact.reference,
                "confidence": "high",
            }
        )
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for item in references:
            unique[(str(item["relation"]), str(item["target"]))] = item
        return [unique[key] for key in sorted(unique)]

    def _task_relations(self, source: LegacyDocument, field_name: str) -> list[str]:
        values = source.frontmatter.get(field_name)
        authored = values if isinstance(values, list) else ([] if values is None else [values])
        mapped: list[str] = []
        for value in authored:
            target = self._map_reference(value, source)
            try:
                uri = WorkctxUri.parse(target)
            except ValueError:
                self._loss(
                    source,
                    "MIG-TASK-RELATION-UNAVAILABLE",
                    f"An unresolved {field_name} entry was omitted from canonical task state.",
                )
                continue
            if uri.context_id != self._plan.context_id or uri.entity_type != "task":
                self._loss(
                    source,
                    "MIG-TASK-RELATION-UNAVAILABLE",
                    f"A non-task {field_name} entry was omitted from canonical task state.",
                )
                continue
            mapped.append(uri.entity_id)
        return list(dict.fromkeys(mapped))

    def _map_reference(self, value: object, source: LegacyDocument) -> str:
        if not isinstance(value, str) or not value.strip():
            return _unavailable_marker("missing-reference")
        authored = value.strip()
        if _contains_absolute_path(authored):
            return _unavailable_marker(authored)
        if authored.startswith("workctx://"):
            try:
                uri = WorkctxUri.parse(authored)
            except ValueError:
                return _unavailable_marker(authored)
            if uri.context_id == self._plan.context_id and authored in {
                document.target_uri for document in self._plan.documents
            }:
                return authored
            matches = self._old_ids.get(uri.entity_id)
            if matches:
                self._ambiguous_reference_loss(source, uri.entity_id, matches)
                return matches[0].target_uri
            return _unavailable_marker(authored)
        if authored.startswith("artifact://"):
            if authored in {artifact.reference for artifact in self._plan.artifacts}:
                return authored
            return _unavailable_marker(authored)
        if _URI_SCHEME.match(authored):
            try:
                return validate_durable_reference(authored)
            except ValueError:
                return _unavailable_marker(authored)
        matches = self._old_ids.get(authored)
        if matches:
            self._ambiguous_reference_loss(source, authored, matches)
            return matches[0].target_uri
        relative = self._resolve_relative_source(source, authored)
        if relative is not None:
            document = self._documents.get(relative) or self._documents_casefold.get(
                relative.casefold()
            )
            if document is not None:
                return document.target_uri
            artifact = self._artifacts.get(relative) or self._artifacts_casefold.get(
                relative.casefold()
            )
            if artifact is not None:
                return artifact.reference
        return _unavailable_marker(authored)

    def _map_artifact_reference(
        self,
        value: object,
        source: LegacyDocument,
    ) -> str | None:
        mapped = self._map_reference(value, source)
        return mapped if mapped.startswith("artifact://") else None

    def _resolve_relative_source(self, source: LegacyDocument, authored: str) -> str | None:
        target = authored.strip().strip("<>")
        if " " in target:
            target = target.split(maxsplit=1)[0]
        try:
            path = unquote(urlsplit(target).path)
        except ValueError:
            path = target
        if not path or path.startswith(("/", "\\")):
            return None
        candidate = PurePosixPath(source.relative_path).parent.joinpath(
            PurePosixPath(path.replace("\\", "/"))
        )
        normalized = _normalize_relative(candidate)
        if normalized in self._documents or normalized in self._artifacts:
            return normalized
        if not PurePosixPath(normalized).suffix:
            markdown = f"{normalized}.md"
            if markdown in self._documents:
                return markdown
        return normalized or None

    def _rewrite_body(self, body: str, source: LegacyDocument) -> str:
        rewritten = _replace_absolute_paths(body)

        def replace_markdown(match: re.Match[str]) -> str:
            target = match.group("target")
            if target.startswith("#") or _URI_SCHEME.match(target):
                mapped = (
                    self._map_reference(target, source)
                    if target.startswith("workctx://")
                    else target
                )
            else:
                mapped = self._map_reference(target, source)
            prefix = "!" if match.group("image") else ""
            return f"{prefix}[{match.group('label')}]({mapped})"

        def replace_wiki(match: re.Match[str]) -> str:
            target = match.group("target")
            label = match.group("label") or target
            return f"[{label}]({self._map_reference(target, source)})"

        rewritten = _MARKDOWN_LINK.sub(replace_markdown, rewritten)
        rewritten = _WIKI_LINK.sub(replace_wiki, rewritten)

        def replace_uri(match: re.Match[str]) -> str:
            return self._map_reference(match.group(0).rstrip(".,;:"), source)

        return _WORKCTX_URI.sub(replace_uri, rewritten)

    def _extras(self, source: LegacyDocument, excluded: frozenset[str]) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        for key, value in source.frontmatter.items():
            if key in excluded:
                continue
            extras[key] = self._json_value(value, source)
        return extras

    def _json_value(self, value: object, source: LegacyDocument) -> JsonValue:
        if value is None or isinstance(value, (bool, int, float)):
            return cast(JsonValue, value)
        if isinstance(value, str):
            return self._sanitize_text(value)
        if isinstance(value, datetime):
            normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
            return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, list):
            return [self._json_value(item, source) for item in value]
        if isinstance(value, dict):
            result: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    self._loss(
                        source,
                        "MIG-METADATA-KEY-SKIPPED",
                        "A non-string metadata key was skipped during normalization.",
                    )
                    continue
                result[key] = self._json_value(item, source)
            return result
        self._loss(
            source,
            "MIG-METADATA-VALUE-STRINGIFIED",
            "An unsupported metadata value was converted to a descriptive string.",
        )
        return self._sanitize_text(str(value))

    def _timestamps(self, source: LegacyDocument) -> _Timestamps:
        created = self._required_timestamp(
            source,
            source.frontmatter.get("created_at", source.frontmatter.get("created")),
            "created_at",
            self._migration_time,
        )
        updated = self._required_timestamp(
            source,
            source.frontmatter.get("updated_at", source.frontmatter.get("updated")),
            "updated_at",
            created,
        )
        return _Timestamps(created_at=created, updated_at=updated)

    def _required_timestamp(
        self,
        source: LegacyDocument,
        value: object,
        field_name: str,
        fallback: datetime,
    ) -> datetime:
        parsed = _parse_timestamp(value)
        if parsed is None:
            self._loss(
                source,
                "MIG-TIMESTAMP-DEFAULTED",
                f"Missing or invalid {field_name} used a documented migration-time fallback.",
            )
            return fallback
        if _timestamp_lost_precision(value):
            self._loss(
                source,
                "MIG-TIMEZONE-ASSUMED",
                f"Timezone precision for {field_name} was unavailable; UTC was assumed.",
            )
        return parsed

    def _optional_timestamp(
        self,
        source: LegacyDocument,
        value: object,
        field_name: str,
    ) -> datetime | None:
        if value is None or value == "":
            return None
        parsed = _parse_timestamp(value)
        if parsed is None:
            self._loss(
                source,
                "MIG-TIMESTAMP-OMITTED",
                f"Invalid {field_name} could not be preserved and was omitted.",
            )
            return None
        if _timestamp_lost_precision(value):
            self._loss(
                source,
                "MIG-TIMEZONE-ASSUMED",
                f"Timezone precision for {field_name} was unavailable; UTC was assumed.",
            )
        return parsed

    def _task_status(self, source: LegacyDocument) -> TaskStatus:
        value = source.frontmatter.get("status")
        try:
            return TaskStatus(str(value).casefold())
        except ValueError:
            self._loss(
                source,
                "MIG-TASK-STATUS-DEFAULTED",
                "A missing or unknown task status was normalized to backlog.",
            )
            return TaskStatus.BACKLOG

    def _task_priority(self, source: LegacyDocument) -> TaskPriority:
        value = source.frontmatter.get("priority")
        try:
            return TaskPriority(str(value).upper())
        except ValueError:
            self._loss(
                source,
                "MIG-TASK-PRIORITY-DEFAULTED",
                "A missing or unknown task priority was normalized to P2.",
            )
            return TaskPriority.P2

    def _claim_status(self, value: object) -> ClaimStatus:
        try:
            return ClaimStatus(str(value).casefold())
        except ValueError:
            return ClaimStatus.UNCERTAIN

    def _observation_kind(self, value: object) -> ObservationKind:
        try:
            return ObservationKind(str(value).casefold())
        except ValueError:
            return ObservationKind.FACT

    def _confidence(self, value: object) -> Confidence:
        try:
            return Confidence(str(value).casefold())
        except ValueError:
            return Confidence.MEDIUM

    def _optional_confidence(self, value: object) -> str | None:
        try:
            return Confidence(str(value).casefold()).value
        except ValueError:
            return None

    def _text_list(self, value: object, source: LegacyDocument) -> list[str]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        normalized = [
            self._sanitize_text(str(item)).strip()
            for item in values
            if item is not None and str(item).strip()
        ]
        if len(normalized) != len(values):
            self._loss(
                source,
                "MIG-EMPTY-LIST-VALUE-SKIPPED",
                "An empty list value was omitted during normalization.",
            )
        return list(dict.fromkeys(normalized))

    def _optional_text(self, value: object, source: LegacyDocument) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            self._loss(
                source,
                "MIG-TEXT-VALUE-STRINGIFIED",
                "A non-string text field was converted to text.",
            )
        normalized = self._sanitize_text(str(value)).strip()
        return normalized or None

    def _sanitize_text(self, value: str) -> str:
        return _replace_absolute_paths(value)

    def _evidence_artifact(self, source: LegacyDocument) -> tuple[str, bool]:
        explicit_unavailable = bool(
            source.frontmatter.get("raw_unavailable") or source.frontmatter.get("derived_only")
        )
        for key in ("raw_path", "artifact_path", "source_path", "original_path"):
            value = source.frontmatter.get(key)
            if not isinstance(value, str):
                continue
            relative = self._resolve_relative_source(source, value)
            if relative is None:
                continue
            artifact = self._artifacts.get(relative)
            if artifact is not None:
                return artifact.reference, explicit_unavailable
        artifact_ref = self._map_artifact_reference(source.frontmatter.get("artifact_ref"), source)
        if artifact_ref is not None:
            return artifact_ref, explicit_unavailable
        own = self._artifacts[source.relative_path]
        return own.reference, True

    def _mapped_claim_id(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        matches = self._old_ids.get(value)
        if not matches or matches[0].source.entity_type != "claim":
            return None
        return matches[0].target_id

    def _ambiguous_reference_loss(
        self,
        source: LegacyDocument,
        authored_id: str,
        matches: tuple[PlannedDocument, ...],
    ) -> None:
        if len(matches) <= 1:
            return
        self._loss(
            source,
            "MIG-AMBIGUOUS-REFERENCE",
            (
                f"A reference to duplicated legacy ID '{authored_id}' was resolved to the "
                "first source path in deterministic order."
            ),
        )

    def _observation_uri(self, observation_id: str) -> str:
        return str(WorkctxUri(self._plan.context_id, "observation", observation_id))

    def _loss(self, source: LegacyDocument, code: str, message: str) -> None:
        self._precision.append(PrecisionLoss(code=code, path=source.report_path, message=message))


@dataclass(frozen=True, slots=True)
class _Timestamps:
    created_at: datetime
    updated_at: datetime


def normalize_migration(
    plan: MigrationPlan,
    *,
    migration_time: datetime,
) -> NormalizedMigration:
    return _Normalizer(plan, migration_time).normalize()


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
            except ValueError:
                return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def _timestamp_lost_precision(value: object) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is None or value.utcoffset() is None
    if isinstance(value, date) and not isinstance(value, datetime):
        return True
    if isinstance(value, str):
        text = value.strip()
        return "T" not in text or not (text.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", text))
    return False


def _utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Migration clocks must return timezone-aware datetimes.")
    return value.astimezone(UTC).replace(microsecond=0)


def _contains_absolute_path(value: str) -> bool:
    return _WINDOWS_ABSOLUTE.search(value) is not None or _POSIX_ABSOLUTE.search(value) is not None


def _replace_absolute_paths(value: str) -> str:
    return _POSIX_ABSOLUTE.sub(
        lambda match: _unavailable_marker(match.group(0)),
        _WINDOWS_ABSOLUTE.sub(
            lambda match: _unavailable_marker(match.group(0)),
            value,
        ),
    )


def _unavailable_marker(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"unavailable://legacy/reference-{digest}"


def _normalize_relative(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


__all__ = ["NormalizedMigration", "normalize_migration"]
