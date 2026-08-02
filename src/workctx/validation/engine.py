from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast
from urllib.parse import quote, urlparse

import yaml
from pydantic import BaseModel, ValidationError

from workctx.domain import (
    ArtifactManifest,
    ArtifactReference,
    Claim,
    ClaimStatus,
    DecisionId,
    EntityFrontmatter,
    EntityType,
    EvidenceId,
    Observation,
    PersonId,
    QuestionId,
    RepoReference,
    RiskId,
    StableId,
    SystemId,
    Task,
    WorkctxUri,
    normalize_workctx_uri,
    parse_durable_reference,
    validate_task_hierarchy,
)
from workctx.domain.frontmatter import parse_frontmatter
from workctx.domain.tasks import TaskHierarchyError
from workctx.models.context import ContextConfig
from workctx.validation.diagnostics import DIAGNOSTIC_DEFINITIONS
from workctx.validation.freshness import (
    CanonicalEdge,
    FreshnessProbe,
    FreshnessState,
)
from workctx.validation.report import Severity, ValidationIssue, ValidationReport

REQUIRED_DIRECTORIES = (
    "00_inbox",
    "01_processed",
    "02_knowledge",
    "03_work",
    "04_views",
    "05_outbox",
    "90_integrations",
    "98_state",
    "99_meta",
)

_TEXT_SUFFIXES = {
    ".bat",
    ".bash",
    ".cfg",
    ".cmd",
    ".conf",
    ".config",
    ".cs",
    ".env",
    ".fish",
    ".go",
    ".graphql",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".key",
    ".kt",
    ".log",
    ".md",
    ".pem",
    ".php",
    ".properties",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".txt",
    ".ts",
    ".tsx",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
_TEXT_FILENAMES = {"dockerfile", "makefile", ".netrc", ".npmrc", ".pypirc"}
_CANONICAL_ZONES = {"02_knowledge", "03_work", "05_outbox"}
_STRUCTURED_SUFFIXES = {".yaml", ".yml", ".json"}
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_REPO_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
_TASK_ID = re.compile(r"^TASK-[0-9]{4}-[0-9]{3}(?:-ST[0-9]{2})?$")
_STABLE_FILENAME = re.compile(
    r"^(?:ART|CLM|DEC|DRAFT|EVD|FLOW|INC|INT|INV|MOD|PER|PRJ|Q|RISK|SVC|SYS|TASK|TEAM)"
    r"-[A-Za-z0-9._%#-]+(?:\.manifest)?$"
)
_BODY_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9+._-])(?:"
    r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\[\]{}\"'`]*|"
    r"(?i:workctx|artifact|repo):[^\s<>\[\]{}\"'`]+)"
)
_SECRET_MARKERS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:\$env:)?(?P<key>[A-Za-z][A-Za-z0-9_-]*)"
    r"['\"]?\s*[:=]\s*['\"]?(?P<value>[^\s'\",}]{12,})"
)
_SECRET_KEY_SUFFIXES = (
    "api_key",
    "access_token",
    "client_secret",
    "secret_access_key",
    "password",
    "authorization",
    "private_key",
    "token",
)
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_ENTITY_REFERENCE_PATHS = frozenset(
    {
        ("uri",),
        ("artifact_ref",),
        ("references", "*", "target"),
        ("references", "*", "source_observations"),
        ("observations", "*", "source", "ref"),
        ("observations", "*", "derived_from"),
        ("observations", "*", "related", "*", "target"),
        ("observations", "*", "related", "*", "source_observations"),
    }
)
_OBSERVATION_REFERENCE_PATHS = frozenset(
    {
        ("source", "ref"),
        ("derived_from",),
        ("related", "*", "target"),
        ("related", "*", "source_observations"),
    }
)
_CLAIM_REFERENCE_PATHS = frozenset({("subject",), ("source_observations",)})

type CanonicalModel = EntityFrontmatter | Task | Claim | ArtifactManifest | Observation

_ENTITY_ID_TYPES: dict[str, type[StableId]] = {
    "evidence": EvidenceId,
    "person": PersonId,
    "system": SystemId,
    "decision": DecisionId,
    "risk": RiskId,
    "question": QuestionId,
}


@dataclass(frozen=True, slots=True)
class _ParsedDocument:
    path: Path
    relative_path: str
    data: dict[str, Any]
    body: str
    kind: str


@dataclass(frozen=True, slots=True)
class _ModelRecord:
    document: _ParsedDocument
    model: CanonicalModel
    nested: bool = False


@dataclass(frozen=True, slots=True)
class _ReferenceCandidate:
    value: str
    is_document_identity: bool = False


class WorkspaceValidator:
    """Read-only, deterministic integrity engine for one context root."""

    def __init__(
        self,
        *,
        root: Path,
        strict: bool,
        freshness_probe: FreshnessProbe | None,
    ) -> None:
        resolved_root = root.expanduser().resolve()
        self._root = resolved_root
        self._strict = strict
        self._freshness_probe = freshness_probe
        self._report = ValidationReport(context_root=resolved_root)
        self._texts: dict[Path, str] = {}
        self._documents: list[_ParsedDocument] = []
        self._records: list[_ModelRecord] = []
        self._identities: dict[str, _ModelRecord] = {}
        self._record_identities: dict[int, str] = {}
        self._valid_document_paths: set[Path] = set()
        self._artifact_digests: set[str] = set()
        self._tasks: dict[str, _ModelRecord] = {}
        self._claims: dict[str, _ModelRecord] = {}
        self._canonical_edges: set[CanonicalEdge] = set()

    def validate(self) -> ValidationReport:
        self._check_required_directories()
        self._read_workspace_text()
        self._load_context_config()
        self._load_canonical_documents()
        self._build_identity_index()
        if self._report.context_id is not None:
            self._check_references()
            self._check_task_relations()
            self._check_claim_rules()
        self._check_task_hierarchy()
        self._check_projection_freshness()
        self._finalize_issues()
        return self._report

    def _add(self, code: str, path: str | None, message: str | None = None) -> None:
        definition = DIAGNOSTIC_DEFINITIONS[code]
        self._report.issues.append(
            ValidationIssue(
                severity=definition.severity,
                code=code,
                message=message or definition.cause,
                path=path,
                repair_action=definition.repair_action,
            )
        )

    def _check_required_directories(self) -> None:
        try:
            children = {path.name: path for path in self._root.iterdir()}
        except OSError:
            children = {}
        for directory in REQUIRED_DIRECTORIES:
            path = children.get(directory)
            if path is None or not path.is_dir():
                self._add(
                    "CTX-MISSING-DIRECTORY",
                    directory,
                    f"Required directory is missing: {directory}",
                )

    def _read_workspace_text(self) -> None:
        for path, failure in _iter_text_files(self._root):
            relative = path.relative_to(self._root).as_posix()
            if failure == "link":
                self._add("CTX-PATH-ESCAPE", relative)
                continue
            if failure == "unreadable":
                self._add("CTX-UNREADABLE-PATH", relative)
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self._add("CTX-NON-UTF8", relative)
                if _is_canonical_file_candidate(path, Path(relative)):
                    self._add("DOC-PARSE", relative)
                continue
            except OSError:
                self._add("CTX-UNREADABLE-PATH", relative)
                if _is_canonical_file_candidate(path, Path(relative)):
                    self._add("DOC-PARSE", relative)
                continue

            self._texts[path] = content
            if not _defer_absolute_path_scan(path, Path(relative), content) and (
                _contains_durable_absolute_path(content)
            ):
                self._add("CTX-ABSOLUTE-PATH", relative)
            if _contains_possible_secret(content):
                self._add("CTX-POSSIBLE-SECRET", relative)

    def _load_context_config(self) -> None:
        path = self._root / "context.yaml"
        try:
            has_exact_name = any(child.name == "context.yaml" for child in self._root.iterdir())
        except OSError:
            has_exact_name = False
        if not has_exact_name:
            self._add("CTX-CONFIG", "context.yaml")
            return
        content = self._texts.get(path)
        if content is None:
            self._add("CTX-CONFIG", "context.yaml")
            return

        try:
            loaded: Any = yaml.safe_load(content)
        except (RecursionError, ValueError, yaml.YAMLError):
            self._add("CTX-CONFIG", "context.yaml")
            return
        if not isinstance(loaded, dict):
            self._add("CTX-CONFIG", "context.yaml")
            return

        raw = cast(dict[str, Any], loaded)
        if _contains_container_cycle(raw):
            self._add("CTX-CONFIG", "context.yaml")
            return
        try:
            candidate = deepcopy(raw)
        except RecursionError:
            self._add("CTX-CONFIG", "context.yaml")
            return
        federated_enabled = _federated_search_enabled(raw)
        if federated_enabled:
            self._add("CTX-FEDERATED-SEARCH", "context.yaml")
            policies = candidate.get("policies")
            if isinstance(policies, dict):
                policies["federated_search"] = False

        try:
            config = ContextConfig.model_validate(candidate)
        except (RecursionError, TypeError, ValidationError):
            self._add("CTX-CONFIG", "context.yaml")
            return
        self._report.context_id = config.id

    def _load_canonical_documents(self) -> None:
        for path, content in sorted(
            self._texts.items(), key=lambda item: item[0].relative_to(self._root).as_posix()
        ):
            if path == self._root / "context.yaml":
                continue
            relative = path.relative_to(self._root)
            if not relative.parts:
                continue

            document: _ParsedDocument | None = None
            if (
                path.suffix.lower() == ".md"
                and _is_canonical_zone(relative)
                and path.name.lower() != "readme.md"
            ):
                document = self._parse_markdown_document(path, relative, content)
            elif path.suffix.lower() in _STRUCTURED_SUFFIXES and (
                _is_manifest_path(relative) or _is_canonical_zone(relative)
            ):
                document = self._parse_structured_document(path, relative, content)

            if document is not None:
                self._documents.append(document)
                self._validate_document(document)

    def _parse_markdown_document(
        self,
        path: Path,
        relative: Path,
        content: str,
    ) -> _ParsedDocument | None:
        if not content.startswith("---\n") and not content.startswith("---\r\n"):
            if _STABLE_FILENAME.fullmatch(path.stem):
                self._add("DOC-PARSE", relative.as_posix())
            return None
        try:
            data, body = parse_frontmatter(content)
        except (RecursionError, ValueError, yaml.YAMLError):
            self._add("DOC-PARSE", relative.as_posix())
            return None
        kind = _document_kind(data) or "entity"
        return _ParsedDocument(path, relative.as_posix(), data, body, kind)

    def _parse_structured_document(
        self,
        path: Path,
        relative: Path,
        content: str,
    ) -> _ParsedDocument | None:
        try:
            loaded: Any = (
                json.loads(content) if path.suffix.lower() == ".json" else yaml.safe_load(content)
            )
        except (RecursionError, ValueError, yaml.YAMLError):
            self._add("DOC-PARSE", relative.as_posix())
            return None
        if not isinstance(loaded, dict):
            self._add("DOC-PARSE", relative.as_posix())
            return None
        data = cast(dict[str, Any], loaded)
        kind = (
            "artifact"
            if _is_manifest_path(relative)
            else _document_kind(data) or _document_kind_from_filename(path) or "entity"
        )
        return _ParsedDocument(path, relative.as_posix(), data, "", kind)

    def _validate_document(self, document: _ParsedDocument) -> None:
        self._check_structured_absolute_paths(document)
        if _contains_container_cycle(document.data):
            self._add("DOC-MODEL", document.relative_path)
            return
        model_type: type[BaseModel]
        diagnostic_code = "DOC-MODEL"
        if document.kind == "task":
            model_type = Task
        elif document.kind == "claim":
            model_type = Claim
        elif document.kind == "artifact":
            model_type = ArtifactManifest
        elif document.kind == "observation":
            model_type = Observation
            diagnostic_code = "OBS-INVALID"
        else:
            model_type = EntityFrontmatter

        try:
            validated = model_type.model_validate(document.data)
        except (RecursionError, TypeError, ValidationError):
            self._add(diagnostic_code, document.relative_path)
            return

        model = validated
        if isinstance(model, EntityFrontmatter) and not _entity_id_is_valid(model):
            self._add("DOC-MODEL", document.relative_path)
            return
        record = _ModelRecord(document, model)
        self._records.append(record)
        self._valid_document_paths.add(document.path)
        self._check_filename_id(record)
        if isinstance(model, ArtifactManifest):
            self._check_artifact_paths(record, model)
        if isinstance(model, EntityFrontmatter) and model.entity_type == "evidence":
            self._validate_embedded_observations(record)

    def _validate_embedded_observations(self, evidence_record: _ModelRecord) -> None:
        raw_observations = evidence_record.document.data.get("observations")
        if raw_observations is None:
            return
        if not isinstance(raw_observations, list):
            self._add("OBS-INVALID", evidence_record.document.relative_path)
            return

        evidence_id = cast(EntityFrontmatter, evidence_record.model).id
        for raw_observation in raw_observations:
            try:
                observation = Observation.model_validate(raw_observation)
            except (RecursionError, TypeError, ValidationError):
                self._add("OBS-INVALID", evidence_record.document.relative_path)
                continue
            if observation.id.rpartition("#")[0] != evidence_id:
                self._add("OBS-EVIDENCE-ID", evidence_record.document.relative_path)
            self._records.append(_ModelRecord(evidence_record.document, observation, nested=True))

    def _check_filename_id(self, record: _ModelRecord) -> None:
        if record.nested:
            return
        identifier = getattr(record.model, "id", None)
        if not isinstance(identifier, str):
            return
        if not _filename_matches_id(record.document.path, identifier):
            self._add("DOC-FILENAME-ID", record.document.relative_path)

    def _check_structured_absolute_paths(self, document: _ParsedDocument) -> None:
        has_absolute_path = any(
            _contains_durable_absolute_path(value)
            for value in _iter_structured_path_values(document.data, kind=document.kind)
        ) or _contains_durable_absolute_path(document.body)
        if has_absolute_path and not any(
            issue.code == "CTX-ABSOLUTE-PATH" and issue.path == document.relative_path
            for issue in self._report.issues
        ):
            self._add("CTX-ABSOLUTE-PATH", document.relative_path)

    def _check_artifact_paths(
        self,
        record: _ModelRecord,
        manifest: ArtifactManifest,
    ) -> None:
        paths = (manifest.preserved_path, *manifest.sidecars)
        if any(_is_unsafe_workspace_path(self._root, value) for value in paths):
            self._add("CTX-PATH-ESCAPE", record.document.relative_path)

    def _build_identity_index(self) -> None:
        context_id = self._report.context_id
        for record in self._records:
            model = record.model
            identity: str | None = None
            if isinstance(model, EntityFrontmatter):
                identity = model.uri
            elif context_id is not None and isinstance(model, Claim):
                identity = str(WorkctxUri(context_id, "claim", model.id))
            elif context_id is not None and isinstance(model, Observation):
                identity = str(WorkctxUri(context_id, "observation", model.id))
            elif context_id is not None and isinstance(model, ArtifactManifest):
                identity = str(WorkctxUri(context_id, "artifact", model.id))

            if identity is not None:
                self._record_identities[id(record)] = identity
                if identity in self._identities:
                    self._add("DOC-DUPLICATE-ID", record.document.relative_path)
                else:
                    self._identities[identity] = record

            if isinstance(model, Task) and model.id not in self._tasks:
                self._tasks[model.id] = record
            elif isinstance(model, Claim) and model.id not in self._claims:
                self._claims[model.id] = record
            elif isinstance(model, ArtifactManifest):
                self._artifact_digests.add(model.content_hash.removeprefix("sha256:"))

    def _check_references(self) -> None:
        for document in self._documents:
            resolve_identity = document.path in self._valid_document_paths
            for candidate in _document_references(document):
                self._check_reference(
                    candidate.value,
                    document.relative_path,
                    resolve=resolve_identity or not candidate.is_document_identity,
                )
        for record in self._records:
            self._collect_typed_edges(record)

    def _check_reference(self, value: str, relative_path: str, *, resolve: bool = True) -> None:
        context_id = self._report.context_id
        if context_id is None:
            return

        if value.lower().startswith("workctx:"):
            try:
                normalized = normalize_workctx_uri(value)
                uri = WorkctxUri.parse(normalized)
            except ValueError:
                self._add("REF-INVALID-URI", relative_path)
                return
            if normalized != value:
                self._add("REF-INVALID-URI", relative_path)
                return
            try:
                uri.require_context(context_id)
            except ValueError:
                self._add("REF-CONTEXT-MISMATCH", relative_path)
                return
            try:
                EntityType(uri.entity_type)
            except ValueError:
                self._add("REF-UNKNOWN-ENTITY-TYPE", relative_path)
                return
            if resolve and normalized not in self._identities:
                self._add("REF-UNRESOLVED", relative_path)
            return

        if value.lower().startswith("artifact:"):
            try:
                artifact = ArtifactReference.parse(value)
            except ValueError:
                self._add("REF-INVALID-URI", relative_path)
                return
            if artifact.digest not in self._artifact_digests:
                self._add("REF-ARTIFACT-UNAVAILABLE", relative_path)
            return

        if value.lower().startswith("repo:"):
            try:
                parsed = urlparse(value)
            except ValueError:
                self._add("REF-INVALID-URI", relative_path)
                return
            commit = parsed.netloc.rpartition("@")[2]
            if not commit or _REPO_COMMIT.fullmatch(commit) is None:
                self._add("REF-REPO-SHA", relative_path)
                return
            try:
                repo_reference = RepoReference.parse(value)
            except ValueError:
                self._add("REF-INVALID-URI", relative_path)
                return
            if _is_nonportable_relative_path(repo_reference.path):
                self._add("REF-INVALID-URI", relative_path)
                return
            self._add("REF-EXTERNAL-UNAVAILABLE", relative_path)
            return

        try:
            parsed_reference = parse_durable_reference(value)
        except ValueError:
            self._add("REF-INVALID-URI", relative_path)
            return
        if isinstance(parsed_reference, (WorkctxUri, ArtifactReference, RepoReference)):
            self._add("REF-INVALID-URI", relative_path)
            return
        self._add("REF-EXTERNAL-UNAVAILABLE", relative_path)

    def _collect_typed_edges(self, record: _ModelRecord) -> None:
        source = self._identity_for(record)
        if source is None:
            return
        if isinstance(record.model, EntityFrontmatter):
            for entity_reference in record.model.references:
                canonical_target = _canonical_reference(entity_reference.target)
                if canonical_target is not None:
                    self._canonical_edges.add(
                        CanonicalEdge(source, str(entity_reference.relation), canonical_target)
                    )

        if isinstance(record.model, Observation):
            for observation_reference in record.model.related:
                canonical_target = _canonical_reference(observation_reference.target)
                if canonical_target is not None:
                    self._canonical_edges.add(
                        CanonicalEdge(
                            source,
                            observation_reference.relation.value,
                            canonical_target,
                        )
                    )
            for target in record.model.derived_from:
                canonical_target = _canonical_reference(target)
                if canonical_target is not None:
                    self._canonical_edges.add(
                        CanonicalEdge(source, "derived_from", canonical_target)
                    )

        if isinstance(record.model, Claim) and self._report.context_id is not None:
            if record.model.supersedes is not None:
                target = str(WorkctxUri(self._report.context_id, "claim", record.model.supersedes))
                self._canonical_edges.add(CanonicalEdge(source, "supersedes", target))
            if record.model.superseded_by is not None:
                newer = str(
                    WorkctxUri(self._report.context_id, "claim", record.model.superseded_by)
                )
                self._canonical_edges.add(CanonicalEdge(newer, "supersedes", source))

    def _identity_for(self, record: _ModelRecord) -> str | None:
        return self._record_identities.get(id(record))

    def _check_task_hierarchy(self) -> None:
        task_records = [record for record in self._records if isinstance(record.model, Task)]
        tasks = [cast(Task, record.model) for record in task_records]
        if not tasks:
            return

        issues_before = sum(issue.code == "TASK-HIERARCHY" for issue in self._report.issues)
        records_by_id: dict[str, list[_ModelRecord]] = defaultdict(list)
        contexts: dict[str, list[_ModelRecord]] = defaultdict(list)
        for record in task_records:
            task = cast(Task, record.model)
            records_by_id[task.id].append(record)
            contexts[WorkctxUri.parse(task.uri).context_id].append(record)

        for records in records_by_id.values():
            for duplicate in records[1:]:
                self._add("TASK-HIERARCHY", duplicate.document.relative_path)

        known_ids = set(records_by_id)
        for record in task_records:
            task = cast(Task, record.model)
            if task.parent_task is not None and task.parent_task not in known_ids:
                self._add("TASK-HIERARCHY", record.document.relative_path)

        active_context = self._report.context_id
        if len(contexts) > 1 or (active_context is not None and active_context not in contexts):
            for context_id, records in contexts.items():
                if context_id == active_context:
                    continue
                for record in records:
                    self._add("TASK-HIERARCHY", record.document.relative_path)

        try:
            validate_task_hierarchy(tasks)
        except TaskHierarchyError:
            issues_after = sum(issue.code == "TASK-HIERARCHY" for issue in self._report.issues)
            if issues_after == issues_before:
                first_path = min(record.document.relative_path for record in task_records)
                self._add("TASK-HIERARCHY", first_path)

    def _check_task_relations(self) -> None:
        if not self._tasks or self._report.context_id is None:
            return
        graph: dict[str, set[str]] = {task_id: set() for task_id in self._tasks}

        for task_id, record in self._tasks.items():
            task = cast(Task, record.model)
            for value in (*task.dependencies, *task.blockers):
                target_id = self._task_target_id(value, record.document.relative_path)
                if target_id is None:
                    if "://" not in value and _TASK_ID.fullmatch(value) is not None:
                        self._add("REF-UNRESOLVED", record.document.relative_path)
                    continue
                graph[target_id].add(task_id)
                target_uri = str(WorkctxUri(self._report.context_id, "task", target_id))
                source_uri = str(WorkctxUri(self._report.context_id, "task", task_id))
                self._canonical_edges.add(CanonicalEdge(source_uri, "depends_on", target_uri))

            for reference in task.references:
                if reference.relation not in {"depends_on", "blocks"}:
                    continue
                target_id = self._task_target_id(reference.target, record.document.relative_path)
                if target_id is None:
                    continue
                if reference.relation == "depends_on":
                    graph[target_id].add(task_id)
                else:
                    graph[task_id].add(target_id)

        for component in _cyclic_components(graph):
            first_id = min(component)
            self._add("TASK-RELATION-CYCLE", self._tasks[first_id].document.relative_path)

    def _task_target_id(self, value: str, relative_path: str) -> str | None:
        if value in self._tasks:
            return value
        try:
            normalized = normalize_workctx_uri(value)
            uri = WorkctxUri.parse(normalized)
        except ValueError:
            if "://" not in value and _TASK_ID.fullmatch(value) is None:
                self._add("TASK-RELATION-TARGET", relative_path)
            if "://" in value:
                self._add("TASK-RELATION-TARGET", relative_path)
            return None
        if normalized == value and uri.entity_type != "task":
            self._add("TASK-RELATION-TARGET", relative_path)
            return None
        if (
            normalized != value
            or uri.context_id != self._report.context_id
            or uri.entity_id not in self._tasks
        ):
            return None
        return uri.entity_id

    def _check_claim_rules(self) -> None:
        if not self._claims:
            return
        invalid_intervals: set[str] = set()
        grouped: dict[tuple[str, str], list[tuple[Claim, _ModelRecord]]] = defaultdict(list)

        for claim_id, record in self._claims.items():
            claim = cast(Claim, record.model)
            if (
                claim.valid_from is not None
                and claim.valid_to is not None
                and claim.valid_to <= claim.valid_from
            ):
                invalid_intervals.add(claim_id)
                self._add("CLAIM-INTERVAL", record.document.relative_path)
            if claim.status is ClaimStatus.CURRENT:
                grouped[(claim.subject, claim.predicate)].append((claim, record))

        earliest = datetime.min.replace(tzinfo=UTC)
        latest = datetime.max.replace(tzinfo=UTC)
        for claims in grouped.values():
            valid_claims = [item for item in claims if item[0].id not in invalid_intervals]
            ordered = sorted(
                valid_claims,
                key=lambda item: (item[0].valid_from or earliest, item[0].id),
            )
            furthest_end: datetime | None = None
            for claim, record in ordered:
                start = claim.valid_from or earliest
                end = claim.valid_to or latest
                if furthest_end is not None and start < furthest_end:
                    self._add("CLAIM-CURRENT-OVERLAP", record.document.relative_path)
                if furthest_end is None or end > furthest_end:
                    furthest_end = end

        graph: dict[str, set[str]] = {claim_id: set() for claim_id in self._claims}
        missing_reported: set[tuple[str, str]] = set()
        for claim_id, record in self._claims.items():
            claim = cast(Claim, record.model)
            if claim.supersedes is not None:
                if claim.supersedes in self._claims:
                    graph[claim_id].add(claim.supersedes)
                else:
                    key = (record.document.relative_path, claim.supersedes)
                    if key not in missing_reported:
                        missing_reported.add(key)
                        self._add("CLAIM-SUPERSESSION-MISSING", key[0])
            if claim.superseded_by is not None:
                if claim.superseded_by in self._claims:
                    graph[claim.superseded_by].add(claim_id)
                else:
                    key = (record.document.relative_path, claim.superseded_by)
                    if key not in missing_reported:
                        missing_reported.add(key)
                        self._add("CLAIM-SUPERSESSION-MISSING", key[0])

        for component in _cyclic_components(graph):
            first_id = min(component)
            self._add(
                "CLAIM-SUPERSESSION-CYCLE",
                self._claims[first_id].document.relative_path,
            )

    def _check_projection_freshness(self) -> None:
        if self._freshness_probe is None or self._report.context_id is None:
            return
        try:
            result = self._freshness_probe.probe(
                self._root,
                context_id=self._report.context_id,
                canonical_edges=tuple(sorted(self._canonical_edges)),
            )
            state = FreshnessState(result.state)
        except Exception:  # probes are optional boundaries; canonical validation must continue
            self._add("PROJECTION-PROBE-FAILED", "98_state")
            return

        code = {
            FreshnessState.STALE: "PROJECTION-STALE",
            FreshnessState.BACKLINK_MISMATCH: "PROJECTION-BACKLINK-MISMATCH",
            FreshnessState.UNKNOWN: "PROJECTION-FRESHNESS-UNKNOWN",
        }.get(state)
        if code is not None:
            self._add(code, "98_state")

    def _finalize_issues(self) -> None:
        normalized_issues: list[ValidationIssue] = []
        for issue in self._report.issues:
            severity = (
                Severity.ERROR
                if self._strict and issue.severity is Severity.WARNING
                else issue.severity
            )
            normalized_issues.append(replace(issue, severity=severity))
        self._report.issues = sorted(
            normalized_issues,
            key=lambda issue: (
                issue.path or "",
                issue.code,
                issue.message,
                issue.severity.value,
            ),
        )


def _iter_text_files(root: Path) -> Iterator[tuple[Path, str | None]]:
    if not root.is_dir():
        return
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda candidate: candidate.name)
        except OSError:
            yield directory, "unreadable"
            continue
        for path in children:
            relative = path.relative_to(root)
            if _is_link_or_junction(path):
                yield path, "link"
            elif any(part.casefold() in {".git", "98_state"} for part in relative.parts):
                continue
            elif _is_opaque_evidence_path(relative):
                # D-036: preserved evidence is opaque to content checks; ingestion
                # guards (WP-310) scan it bounded at registration time.
                continue
            elif path.is_dir():
                pending.append(path)
            elif path.is_file() and _is_text_file(path):
                yield path, None


_OPAQUE_EVIDENCE_PREFIXES = (
    ("00_inbox", "raw"),
    ("00_inbox", "quarantine"),
    ("01_processed",),
)


def _is_opaque_evidence_path(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    return any(parts[: len(prefix)] == prefix for prefix in _OPAQUE_EVIDENCE_PREFIXES)


def _is_text_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in _TEXT_SUFFIXES
        or name in _TEXT_FILENAMES
        or name == ".env"
        or name.startswith(".env.")
    )


def _contains_durable_absolute_path(content: str) -> bool:
    for line in content.splitlines():
        stripped = line.strip().strip("`'\"")
        if _is_absolute_machine_path(stripped):
            return True
    return False


def contains_possible_secret(content: str) -> bool:
    """Public secret-pattern predicate (lead addition, D-035) for ingestion guards."""

    return _contains_possible_secret(content)


def _contains_possible_secret(content: str) -> bool:
    if any(pattern.search(content) for pattern in _SECRET_MARKERS):
        return True
    for match in _SECRET_ASSIGNMENT.finditer(content):
        normalized_key = match.group("key").lower().replace("-", "_")
        compact_key = normalized_key.replace("_", "")
        if any(
            normalized_key == suffix
            or normalized_key.endswith(f"_{suffix}")
            or compact_key == suffix.replace("_", "")
            or compact_key.endswith(suffix.replace("_", ""))
            for suffix in _SECRET_KEY_SUFFIXES
        ):
            return True
    return False


def _defer_absolute_path_scan(path: Path, relative: Path, content: str) -> bool:
    if _is_manifest_path(relative) or _is_canonical_zone(relative):
        if path.suffix.lower() in _STRUCTURED_SUFFIXES:
            return True
        return path.suffix.lower() == ".md" and content.startswith(("---\n", "---\r\n"))
    return False


def _is_absolute_machine_path(value: str) -> bool:
    if value.startswith("///"):
        return re.match(r"^/{3,}[^/\s]", value) is not None
    if value.startswith("//"):
        return re.match(r"^//[^/\s]+/[^/\s]+", value) is not None
    return value.startswith(("/", "\\")) or bool(_WINDOWS_ABSOLUTE.match(value))


def _federated_search_enabled(raw: Mapping[str, Any]) -> bool:
    policies = raw.get("policies")
    return isinstance(policies, Mapping) and policies.get("federated_search") is True


def _is_manifest_path(relative: Path) -> bool:
    return (
        bool(relative.parts)
        and relative.parts[0].casefold() in {"00_inbox", "01_processed"}
        and any(part.casefold() == "manifests" for part in relative.parts)
    )


def _is_canonical_zone(relative: Path) -> bool:
    return bool(relative.parts) and relative.parts[0].casefold() in _CANONICAL_ZONES


def _is_canonical_file_candidate(path: Path, relative: Path) -> bool:
    if _is_manifest_path(relative):
        return True
    if not _is_canonical_zone(relative):
        return False
    if path.suffix.lower() in _STRUCTURED_SUFFIXES:
        return True
    return (
        path.suffix.lower() == ".md"
        and path.name.lower() != "readme.md"
        and _STABLE_FILENAME.fullmatch(path.stem) is not None
    )


def _document_kind(data: Mapping[str, Any]) -> str | None:
    identifier = data.get("id")
    entity_type = data.get("entity_type")
    if entity_type == "task":
        return "task"
    if entity_type == "claim":
        return "claim"
    if entity_type == "artifact":
        return "artifact"
    if entity_type == "observation":
        return "observation"
    if entity_type is not None:
        return "entity"
    if "task_type" in data or (isinstance(identifier, str) and identifier.startswith("TASK-")):
        return "task"
    if (isinstance(identifier, str) and identifier.startswith("CLM-")) or {
        "subject",
        "predicate",
    }.issubset(data):
        return "claim"
    if (isinstance(identifier, str) and identifier.startswith("ART-")) or "content_hash" in data:
        return "artifact"
    if (isinstance(identifier, str) and "#OBS-" in identifier) or {
        "kind",
        "statement",
        "source",
    }.issubset(data):
        return "observation"
    return None


def _document_kind_from_filename(path: Path) -> str | None:
    stem = path.stem
    if stem.startswith("TASK-"):
        return "task"
    if stem.startswith("CLM-"):
        return "claim"
    if stem.startswith("ART-"):
        return "artifact"
    if "%23OBS-" in stem or "#OBS-" in stem:
        return "observation"
    if _STABLE_FILENAME.fullmatch(stem):
        return "entity"
    return None


def _filename_matches_id(path: Path, identifier: str) -> bool:
    encoded_identifier = quote(identifier, safe="-._~")
    stem = path.stem
    return stem in {
        identifier,
        encoded_identifier,
        f"{identifier}.manifest",
        f"{encoded_identifier}.manifest",
    }


def _entity_id_is_valid(entity: EntityFrontmatter) -> bool:
    id_type = _ENTITY_ID_TYPES.get(entity.entity_type)
    if id_type is None:
        return True
    try:
        id_type.parse(entity.id)
    except ValueError:
        return False
    return True


def _iter_structured_path_values(value: object, *, kind: str) -> Iterator[str]:
    visited: set[int] = set()
    pending: list[tuple[object, tuple[str, ...]]] = [(value, ())]
    while pending:
        current, path = pending.pop()
        if isinstance(current, str):
            yield current
            continue
        if not isinstance(current, (Mapping, list)):
            continue
        identity = id(current)
        if identity in visited:
            continue
        visited.add(identity)
        if isinstance(current, Mapping):
            is_json_pointer = current.get("type") == "json_pointer"
            pending.extend(
                (nested, (*path, str(key)))
                for key, nested in reversed(list(current.items()))
                if not (
                    is_json_pointer
                    and key == "pointer"
                    and _is_json_pointer_locator_path(kind, (*path, str(key)))
                )
            )
        else:
            pending.extend((nested, (*path, "*")) for nested in reversed(current))


def _is_json_pointer_locator_path(kind: str, path: tuple[str, ...]) -> bool:
    if kind == "observation":
        return path == ("source", "locator", "pointer")
    if kind == "entity":
        return path == ("observations", "*", "source", "locator", "pointer")
    return False


def _contains_container_cycle(value: object) -> bool:
    if not isinstance(value, (Mapping, list)):
        return False
    active: set[int] = set()
    visited: set[int] = set()
    pending: list[tuple[object, bool]] = [(value, False)]
    while pending:
        current, exiting = pending.pop()
        if not isinstance(current, (Mapping, list)):
            continue
        identity = id(current)
        if exiting:
            active.discard(identity)
            visited.add(identity)
            continue
        if identity in active:
            return True
        if identity in visited:
            continue
        active.add(identity)
        pending.append((current, True))
        nested_values = current.values() if isinstance(current, Mapping) else current
        pending.extend((nested, False) for nested in reversed(list(nested_values)))
    return False


def _is_unsafe_workspace_path(root: Path, value: str) -> bool:
    if _is_nonportable_relative_path(value):
        return True
    parts = value.split("/")
    path = PurePosixPath(value)
    if path.is_absolute():
        return True
    try:
        candidate = (root / Path(*parts)).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return True
    try:
        candidate.relative_to(root)
    except ValueError:
        return True
    return False


def _is_nonportable_relative_path(value: str) -> bool:
    if not value or _is_absolute_machine_path(value) or "\\" in value:
        return True
    if any(ord(character) < 32 for character in value):
        return True
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return True
    windows_path = PureWindowsPath(value)
    if windows_path.drive or windows_path.root:
        return True
    for part in parts:
        base_name = part.partition(".")[0].upper()
        if (
            ":" in part
            or any(character in part for character in '<>"|?*')
            or part.endswith((" ", "."))
            or base_name in _WINDOWS_RESERVED_NAMES
        ):
            return True
    return False


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def _document_references(document: _ParsedDocument) -> Iterator[_ReferenceCandidate]:
    yielded: set[tuple[str, bool]] = set()
    visited: set[int] = set()
    pending: list[tuple[object, tuple[str, ...]]] = [(document.data, ())]
    while pending:
        current, field_path = pending.pop()
        is_document_identity = field_path == ("uri",)
        if _is_required_reference_path(document.kind, field_path):
            if isinstance(current, str):
                candidate = _ReferenceCandidate(current, is_document_identity)
                key = (candidate.value, candidate.is_document_identity)
                if key not in yielded:
                    yielded.add(key)
                    yield candidate
                continue
            if isinstance(current, list):
                values = [item if isinstance(item, str) else "" for item in current]
            else:
                values = [""]
            for value in values:
                candidate = _ReferenceCandidate(value, is_document_identity)
                key = (candidate.value, candidate.is_document_identity)
                if key not in yielded:
                    yielded.add(key)
                    yield candidate
            continue

        if isinstance(current, str):
            for scalar_value in _references_in_scalar(current):
                candidate = _ReferenceCandidate(scalar_value, is_document_identity)
                key = (candidate.value, candidate.is_document_identity)
                if key not in yielded:
                    yielded.add(key)
                    yield candidate
            continue
        if not isinstance(current, (Mapping, list)):
            continue
        identity = id(current)
        if identity in visited:
            continue
        visited.add(identity)
        if isinstance(current, Mapping):
            items = list(current.items())
            pending.extend((nested, (*field_path, str(key))) for key, nested in reversed(items))
        else:
            pending.extend((nested, (*field_path, "*")) for nested in reversed(current))

    for match in _BODY_REFERENCE.finditer(document.body):
        body_value = match.group(0).rstrip(".,;:)")
        if body_value and (body_value, False) not in yielded:
            yielded.add((body_value, False))
            yield _ReferenceCandidate(body_value)


def _is_required_reference_path(kind: str, path: tuple[str, ...]) -> bool:
    if kind in {"entity", "task"}:
        return path in _ENTITY_REFERENCE_PATHS or (
            kind == "task" and path == ("source_observations",)
        )
    if kind == "claim":
        return path in _CLAIM_REFERENCE_PATHS
    if kind == "observation":
        return path in _OBSERVATION_REFERENCE_PATHS
    return False


def _references_in_scalar(value: str) -> Iterator[str]:
    for match in _BODY_REFERENCE.finditer(value):
        candidate = match.group(0).rstrip(".,;:)")
        if candidate:
            yield candidate


def _canonical_reference(value: str) -> str | None:
    try:
        parsed = parse_durable_reference(value)
    except ValueError:
        return None
    return str(parsed)


def _cyclic_components(graph: Mapping[str, set[str]]) -> list[set[str]]:
    nodes = set(graph)
    for outbound_targets in graph.values():
        nodes.update(outbound_targets)

    visited: set[str] = set()
    finish_order: list[str] = []
    for start in sorted(nodes):
        if start in visited:
            continue
        visited.add(start)
        pending: list[tuple[str, int, tuple[str, ...]]] = [
            (start, 0, tuple(sorted(graph.get(start, set()))))
        ]
        while pending:
            node, index, frame_targets = pending[-1]
            if index >= len(frame_targets):
                pending.pop()
                finish_order.append(node)
                continue
            target = frame_targets[index]
            pending[-1] = (node, index + 1, frame_targets)
            if target not in visited:
                visited.add(target)
                pending.append((target, 0, tuple(sorted(graph.get(target, set())))))

    transpose: dict[str, set[str]] = {node: set() for node in nodes}
    for source, targets in graph.items():
        for target in targets:
            transpose[target].add(source)

    assigned: set[str] = set()
    components: list[set[str]] = []
    for start in reversed(finish_order):
        if start in assigned:
            continue
        component: set[str] = set()
        assigned.add(start)
        pending_nodes = [start]
        while pending_nodes:
            node = pending_nodes.pop()
            component.add(node)
            for source in sorted(transpose[node], reverse=True):
                if source not in assigned:
                    assigned.add(source)
                    pending_nodes.append(source)
        if len(component) > 1 or any(member in graph.get(member, set()) for member in component):
            components.append(component)
    return sorted(components, key=lambda component: min(component))
