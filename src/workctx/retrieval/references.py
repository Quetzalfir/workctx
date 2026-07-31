"""Context-bound deterministic durable-reference resolution."""

from __future__ import annotations

from workctx.domain import (
    ArtifactReference,
    EntityType,
    RepoReference,
    WorkctxUri,
    parse_durable_reference,
)
from workctx.errors import ContextBoundaryError as BaseContextBoundaryError
from workctx.retrieval.protocols import ProjectionReader
from workctx.retrieval.records import (
    ArtifactReferenceDescriptor,
    RepoReferenceDescriptor,
    ResolutionResult,
    ResolutionStatus,
    WorkctxReferenceDescriptor,
)

type ResolvableReference = str | WorkctxUri | ArtifactReference | RepoReference


class ContextBoundaryError(BaseContextBoundaryError):
    """Raised when retrieval would leave the reader's bound context."""


def resolve(reader: ProjectionReader, reference: ResolvableReference) -> ResolutionResult:
    """Resolve a supported durable reference without filesystem or SQL access."""

    parsed = _parse_reference(reference)
    if isinstance(parsed, WorkctxUri):
        try:
            EntityType(parsed.entity_type)
        except ValueError as exc:
            raise ValueError(f"Unknown Work Context entity type: {parsed.entity_type!r}") from exc
        _require_context(parsed, reader.context_id)
        descriptor = WorkctxReferenceDescriptor(uri=parsed)
        try:
            record = reader.get_document_by_uri(parsed)
        except BaseContextBoundaryError as exc:
            if isinstance(exc, ContextBoundaryError):
                raise
            raise ContextBoundaryError("Reference belongs to another context") from exc
        return ResolutionResult(
            status=(
                ResolutionStatus.RESOLVED if record is not None else ResolutionStatus.NOT_FOUND
            ),
            descriptor=descriptor,
            record=record,
        )
    if isinstance(parsed, ArtifactReference):
        return ResolutionResult(
            status=ResolutionStatus.RESOLVED,
            descriptor=ArtifactReferenceDescriptor.from_reference(parsed),
        )
    if isinstance(parsed, RepoReference):
        return ResolutionResult(
            status=ResolutionStatus.RESOLVED,
            descriptor=RepoReferenceDescriptor.from_reference(parsed),
        )
    raise ValueError("Retrieval supports only workctx://, artifact://, and repo:// references")


def _parse_reference(
    reference: ResolvableReference,
) -> WorkctxUri | ArtifactReference | RepoReference | str:
    if isinstance(reference, (WorkctxUri, ArtifactReference, RepoReference)):
        return reference
    return parse_durable_reference(reference)


def _require_context(uri: WorkctxUri, active_context_id: str) -> None:
    try:
        uri.require_context(active_context_id)
    except ValueError as exc:
        raise ContextBoundaryError("Reference belongs to another context") from exc
