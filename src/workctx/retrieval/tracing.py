"""Exact claim and observation source tracing."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC

from workctx.adapters.sqlite import ClaimRecord, ObservationRecord, TaskRecord
from workctx.domain import ClaimStatus, EntityType, WorkctxUri
from workctx.errors import ContextBoundaryError as BaseContextBoundaryError
from workctx.retrieval.protocols import ProjectionReader
from workctx.retrieval.records import (
    MissingObservation,
    MissingObservationReason,
    ResolutionStatus,
    TracedObservation,
    TraceResult,
    WorkctxReferenceDescriptor,
)
from workctx.retrieval.references import ResolvableReference, resolve

__all__ = ["TraceResult", "trace"]

_CURRENT_CLAIM_STATUSES = frozenset({ClaimStatus.CURRENT, ClaimStatus.UNCERTAIN})


def trace(
    reader: ProjectionReader,
    reference: ResolvableReference,
    *,
    include_history: bool = False,
) -> TraceResult:
    """Trace a focal document through claims and observations to exact source locators."""

    focal = resolve(reader, reference)
    if (
        focal.status is ResolutionStatus.NOT_FOUND
        or not isinstance(focal.descriptor, WorkctxReferenceDescriptor)
        or focal.record is None
    ):
        return TraceResult(
            focal=focal,
            include_history=include_history,
            claims=(),
            observations=(),
            missing_observations=(),
        )

    focal_uri = focal.descriptor.uri
    trace_uri = focal.record.subject if isinstance(focal.record, ClaimRecord) else focal_uri
    claims = _claims(reader, focal.record, trace_uri, include_history)

    observation_records: dict[str, ObservationRecord] = {}
    observation_referrers: defaultdict[str, set[str]] = defaultdict(set)
    requested_references: defaultdict[str, set[str]] = defaultdict(set)

    if isinstance(focal.record, ObservationRecord):
        _add_observation(
            observation_records,
            observation_referrers,
            focal.record,
            str(focal_uri),
        )

    for observation in reader.observations_for_parent(trace_uri):
        _add_observation(
            observation_records,
            observation_referrers,
            observation,
            str(trace_uri),
        )

    for claim in claims:
        for observation_uri in claim.source_observations:
            requested_references[str(observation_uri)].add(str(claim.uri))

    if isinstance(focal.record, TaskRecord):
        for observation_reference in focal.record.source_observations:
            requested_references[observation_reference].add(str(focal.record.uri))

    for edge in (*reader.outbound_edges(trace_uri), *reader.inbound_edges(trace_uri)):
        edge_referrer = str(edge.source_uri)
        for observation_reference in edge.source_observations:
            requested_references[observation_reference].add(edge_referrer)
        if edge.source_uri.entity_type == EntityType.OBSERVATION.value:
            requested_references[str(edge.source_uri)].add(str(trace_uri))

    missing: list[MissingObservation] = []
    for observation_reference in sorted(requested_references):
        referrers = tuple(sorted(requested_references[observation_reference]))
        try:
            resolved_observation = reader.get_observation(observation_reference)
        except BaseContextBoundaryError:
            missing.append(
                MissingObservation(
                    reference=observation_reference,
                    reason=MissingObservationReason.CONTEXT_BOUNDARY,
                    referenced_by=referrers,
                )
            )
            continue
        except ValueError:
            missing.append(
                MissingObservation(
                    reference=observation_reference,
                    reason=MissingObservationReason.INVALID_REFERENCE,
                    referenced_by=referrers,
                )
            )
            continue
        if resolved_observation is None:
            missing.append(
                MissingObservation(
                    reference=observation_reference,
                    reason=MissingObservationReason.NOT_FOUND,
                    referenced_by=referrers,
                )
            )
            continue
        for referrer in referrers:
            _add_observation(
                observation_records,
                observation_referrers,
                resolved_observation,
                referrer,
            )

    traced = tuple(
        TracedObservation(
            observation=observation_records[uri],
            referenced_by=tuple(sorted(observation_referrers[uri])),
        )
        for uri in sorted(observation_records)
    )
    return TraceResult(
        focal=focal,
        include_history=include_history,
        claims=claims,
        observations=traced,
        missing_observations=tuple(missing),
    )


def _claims(
    reader: ProjectionReader,
    focal_record: object,
    subject: WorkctxUri,
    include_history: bool,
) -> tuple[ClaimRecord, ...]:
    statuses = None if include_history else _CURRENT_CLAIM_STATUSES
    claims_by_uri = {
        str(claim.uri): claim for claim in reader.claims_for_subject(subject, statuses=statuses)
    }
    if isinstance(focal_record, ClaimRecord):
        claims_by_uri[str(focal_record.uri)] = focal_record
    claims = list(claims_by_uri.values())
    claims.sort(key=lambda claim: claim.id)
    claims.sort(key=lambda claim: claim.observed_at.astimezone(UTC), reverse=True)
    return tuple(claims)


def _add_observation(
    records: dict[str, ObservationRecord],
    referrers: defaultdict[str, set[str]],
    observation: ObservationRecord,
    referrer: str,
) -> None:
    uri = str(observation.uri)
    records[uri] = observation
    referrers[uri].add(referrer)
