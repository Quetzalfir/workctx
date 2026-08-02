"""Read-only, context-bound MCP resource application service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from workctx.adapters.filesystem import CanonicalStore, ContextZone
from workctx.adapters.sqlite import (
    ClaimRecord,
    EntityRecord,
    ObservationRecord,
    SQLiteProjection,
    TaskRecord,
)
from workctx.domain import Claim, EntityFrontmatter, EntityType, Observation, Task, WorkctxUri
from workctx.domain.frontmatter import parse_frontmatter
from workctx.errors import ContextBoundaryError
from workctx.mcp.models import (
    DiagnosticSeverity,
    ErrorCategory,
    McpDiagnostic,
    ResourceAccessError,
)
from workctx.mcp.serialization import safe_diagnostic_text, safe_result_object
from workctx.retrieval import resolve


@dataclass(frozen=True, slots=True)
class ResourceDescriptor:
    uri: str
    name: str
    title: str
    description: str
    mime_type: str = "application/json"


@dataclass(frozen=True, slots=True)
class ResourceTemplateDescriptor:
    uri_template: str
    name: str
    title: str
    description: str
    mime_type: str = "application/json"


@dataclass(frozen=True, slots=True)
class ResourceContent:
    uri: str
    text: str
    mime_type: str = "application/json"


class _ResourcePathEscape(ContextBoundaryError):
    """Canonical projection data attempted to leave an allowed context zone."""


class McpResourceService:
    """Resolve canonical resources without accepting filesystem paths from clients."""

    def __init__(self, context_root: Path) -> None:
        store = CanonicalStore(context_root)
        self._root = store.context_root
        self._context_id = store.context_id
        self._configuration_uri = f"workctx://{self._context_id}/context/configuration"

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def configuration_uri(self) -> str:
        return self._configuration_uri

    def list_resources(self) -> tuple[ResourceDescriptor, ...]:
        self._store()
        return (
            ResourceDescriptor(
                uri=self._configuration_uri,
                name="context_configuration",
                title="Work Context configuration",
                description="Typed configuration for the context bound to this MCP server.",
            ),
        )

    def list_templates(self) -> tuple[ResourceTemplateDescriptor, ...]:
        self._store()
        return (
            ResourceTemplateDescriptor(
                uri_template=f"workctx://{self._context_id}/{{entity_type}}/{{entity_id}}",
                name="canonical_entity",
                title="Canonical Work Context entity",
                description=(
                    "Sanitized, validated canonical frontmatter addressed by its workctx:// URI."
                ),
            ),
        )

    def read(self, uri: str) -> ResourceContent:
        """Read the fixed configuration or one canonical entity frontmatter resource."""

        try:
            if uri == self._configuration_uri:
                return self._read_configuration()
            parsed = WorkctxUri.parse(uri)
            if parsed.context_id != self._context_id:
                raise ContextBoundaryError("The requested resource belongs to another context")
            EntityType(parsed.entity_type)
            outcome = resolve(SQLiteProjection(self._root), parsed)
            if not outcome.found or outcome.record is None:
                raise self._error(
                    "REF-NOT-FOUND",
                    ErrorCategory.USER_CORRECTABLE,
                    "The requested canonical resource was not found.",
                )
            frontmatter = self._read_frontmatter(uri, outcome.record)
            return ResourceContent(
                uri=uri,
                text=_json_text(
                    {
                        "schema_version": 1,
                        "context_id": self._context_id,
                        "trust": "untrusted_data",
                        "kind": "canonical_frontmatter",
                        "uri": uri,
                        "frontmatter": frontmatter,
                    }
                ),
            )
        except ResourceAccessError:
            raise
        except _ResourcePathEscape as exc:
            raise self._error(
                "CTX-PATH-ESCAPE",
                ErrorCategory.CONTEXT_BOUNDARY,
                "The requested resource path leaves the bound context.",
            ) from exc
        except (ContextBoundaryError, PermissionError) as exc:
            raise self._error(
                "REF-CONTEXT-MISMATCH",
                ErrorCategory.CONTEXT_BOUNDARY,
                "The requested resource is outside the bound context.",
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise self._error(
                "REF-INVALID-URI",
                ErrorCategory.USAGE_CONFIGURATION,
                "The requested resource URI or canonical document is invalid.",
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise self._error(
                "USER_CORRECTABLE",
                ErrorCategory.USER_CORRECTABLE,
                "The requested canonical resource could not be read.",
            ) from exc
        except Exception as exc:
            raise self._error(
                "INTERNAL_ERROR",
                ErrorCategory.INTERNAL_FAILURE,
                "Unexpected internal failure.",
            ) from exc

    def _read_configuration(self) -> ResourceContent:
        config = self._store().read_context_config()
        return ResourceContent(
            uri=self._configuration_uri,
            text=_json_text(
                {
                    "schema_version": 1,
                    "context_id": self._context_id,
                    "trust": "trusted_configuration",
                    "kind": "context_configuration",
                    "configuration": config.model_dump(mode="json"),
                }
            ),
        )

    def _read_frontmatter(
        self,
        requested_uri: str,
        record: EntityRecord | TaskRecord | ClaimRecord | ObservationRecord,
    ) -> dict[str, Any]:
        store = self._store()
        try:
            path = store.resolve_path(
                record.source_path,
                zones=(ContextZone.KNOWLEDGE, ContextZone.WORK, ContextZone.OUTBOX),
            )
        except (ContextBoundaryError, PermissionError) as exc:
            raise _ResourcePathEscape("Canonical resource path escaped its zone") from exc
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise _ResourcePathEscape("Canonical resources cannot be symbolic links")
        if not path.is_file():
            raise FileNotFoundError(path)
        raw, _body = parse_frontmatter(path.read_text(encoding="utf-8"))

        if isinstance(record, TaskRecord):
            task = Task.model_validate(raw)
            _require_entity_uri(task.uri, requested_uri, self._context_id)
            return task.model_dump(mode="json")
        if isinstance(record, ClaimRecord):
            claim = Claim.model_validate(raw)
            if claim.id != record.id:
                raise ValueError("Claim identity does not match the requested resource")
            return claim.model_dump(mode="json")
        if isinstance(record, ObservationRecord):
            observation = _observation_from_frontmatter(raw, record.id)
            return observation.model_dump(mode="json")

        entity = EntityFrontmatter.model_validate(raw)
        _require_entity_uri(entity.uri, requested_uri, self._context_id)
        return entity.model_dump(mode="json")

    def _store(self) -> CanonicalStore:
        store = CanonicalStore(self._root)
        if store.context_root != self._root or store.context_id != self._context_id:
            raise ContextBoundaryError("The bound context changed while the server was running")
        return store

    @staticmethod
    def _error(code: str, category: ErrorCategory, message: object) -> ResourceAccessError:
        return ResourceAccessError(
            McpDiagnostic(
                code=code,
                category=category,
                severity=DiagnosticSeverity.ERROR,
                message=safe_diagnostic_text(message),
            )
        )


def _observation_from_frontmatter(raw: dict[str, Any], observation_id: str) -> Observation:
    try:
        direct = Observation.model_validate(raw)
    except ValidationError:
        candidates = raw.get("observations")
        if not isinstance(candidates, list):
            raise ValueError("Observation source document has no observation collection") from None
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("id") == observation_id:
                return Observation.model_validate(candidate)
        raise ValueError("Observation is absent from its canonical source document") from None
    if direct.id != observation_id:
        raise ValueError("Observation identity does not match the requested resource")
    return direct


def _require_entity_uri(value: str, requested_uri: str, context_id: str) -> None:
    parsed = WorkctxUri.parse(value)
    if parsed.context_id != context_id:
        raise ContextBoundaryError("Canonical entity belongs to another context")
    if str(parsed) != requested_uri:
        raise ValueError("Canonical entity URI does not match the requested resource")


def _json_text(value: object) -> str:
    return json.dumps(
        safe_result_object(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


__all__ = [
    "McpResourceService",
    "ResourceContent",
    "ResourceDescriptor",
    "ResourceTemplateDescriptor",
]
