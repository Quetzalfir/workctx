"""Deterministic typed-relation graph traversal over the projection reader."""

from __future__ import annotations

from dataclasses import dataclass

from workctx.adapters.sqlite import EdgeRecord
from workctx.domain import RelationType, WorkctxUri, parse_durable_reference
from workctx.errors import ContextBoundaryError as BaseContextBoundaryError
from workctx.retrieval.protocols import ProjectionReader
from workctx.retrieval.records import (
    EdgeDirection,
    RelatedNode,
    RelatedResult,
    TraversalDirection,
    TraversedEdge,
)
from workctx.retrieval.references import (
    ContextBoundaryError,
    ResolvableReference,
    resolve,
)

_DIRECTION_ORDER = {
    EdgeDirection.OUTBOUND: 0,
    EdgeDirection.INBOUND: 1,
}


@dataclass(frozen=True, slots=True)
class _Candidate:
    direction: EdgeDirection
    edge: EdgeRecord
    neighbor: str


def related(
    reader: ProjectionReader,
    reference: ResolvableReference,
    *,
    direction: TraversalDirection = TraversalDirection.BOTH,
    depth: int = 1,
    relations: frozenset[RelationType] | None = None,
) -> RelatedResult:
    """Traverse inbound, outbound, or both directions with deterministic BFS ordering."""

    if type(depth) is not int or depth < 0:
        raise ValueError("depth must be a non-negative integer")

    focal = resolve(reader, reference)
    if depth == 0 or (relations is not None and not relations):
        return RelatedResult(
            focal=focal,
            max_depth=depth,
            direction=direction,
            relations=relations,
            nodes=(),
            edges=(),
        )

    visited_references = {focal.reference}
    visited_edges: set[tuple[object, ...]] = set()
    nodes: list[RelatedNode] = []
    traversed_edges: list[TraversedEdge] = []
    frontier = [focal.reference]

    for current_depth in range(depth):
        next_frontier: list[str] = []
        for current in sorted(frontier):
            for candidate in _candidates(reader, current, direction, relations):
                edge_key = _edge_key(candidate.edge)
                if edge_key not in visited_edges:
                    visited_edges.add(edge_key)
                    traversed_edges.append(
                        TraversedEdge(
                            depth=current_depth + 1,
                            direction=candidate.direction,
                            edge=candidate.edge,
                        )
                    )

                if candidate.neighbor in visited_references:
                    continue
                visited_references.add(candidate.neighbor)
                try:
                    resolution = resolve(reader, candidate.neighbor)
                except ValueError:
                    # Placeholder external schemes remain visible on the traversed edge.
                    continue
                nodes.append(RelatedNode(depth=current_depth + 1, resolution=resolution))
                next_frontier.append(resolution.reference)
        frontier = sorted(set(next_frontier))
        if not frontier:
            break

    return RelatedResult(
        focal=focal,
        max_depth=depth,
        direction=direction,
        relations=relations,
        nodes=tuple(nodes),
        edges=tuple(traversed_edges),
    )


def _candidates(
    reader: ProjectionReader,
    current: str,
    direction: TraversalDirection,
    relations: frozenset[RelationType] | None,
) -> tuple[_Candidate, ...]:
    parsed = parse_durable_reference(current)
    candidates: list[_Candidate] = []
    try:
        if direction in {TraversalDirection.OUTBOUND, TraversalDirection.BOTH} and isinstance(
            parsed, WorkctxUri
        ):
            candidates.extend(
                _Candidate(EdgeDirection.OUTBOUND, edge, edge.target)
                for edge in reader.outbound_edges(parsed, relations=relations)
            )
        if direction in {TraversalDirection.INBOUND, TraversalDirection.BOTH}:
            candidates.extend(
                _Candidate(EdgeDirection.INBOUND, edge, str(edge.source_uri))
                for edge in reader.inbound_edges(current, relations=relations)
            )
    except BaseContextBoundaryError as exc:
        if isinstance(exc, ContextBoundaryError):
            raise
        raise ContextBoundaryError("Reference belongs to another context") from exc
    return tuple(sorted(candidates, key=_candidate_key))


def _candidate_key(candidate: _Candidate) -> tuple[object, ...]:
    return (
        _DIRECTION_ORDER[candidate.direction],
        candidate.edge.relation.value,
        str(candidate.edge.source_uri),
        candidate.edge.target,
        candidate.edge.ordinal,
        candidate.neighbor,
    )


def _edge_key(edge: EdgeRecord) -> tuple[object, ...]:
    return (
        str(edge.source_uri),
        edge.relation.value,
        edge.target,
        None if edge.confidence is None else edge.confidence.value,
        None if edge.valid_from is None else edge.valid_from.isoformat(),
        None if edge.valid_to is None else edge.valid_to.isoformat(),
        edge.note,
        edge.source_path,
        edge.ordinal,
        edge.source_observations,
    )
