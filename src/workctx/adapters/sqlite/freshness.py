"""SQLite-backed freshness probe for the validation engine.

Lead integration glue at Wave 2 close: implements the WP-220
``FreshnessProbe`` protocol over the WP-210 projection adapter. The probe is
strictly read-only — it inspects readiness without triggering the adapter's
auto-rebuild and compares canonical outbound edges against projected edges
for the sources present in the canonical set.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from workctx.adapters.sqlite.models import ContextIsolationError, ProjectionError
from workctx.adapters.sqlite.projection import SQLiteProjection
from workctx.validation.freshness import (
    CanonicalEdge,
    FreshnessResult,
    FreshnessState,
)


class SqliteFreshnessProbe:
    """Probe derived-state freshness from the context's SQLite projection."""

    def probe(
        self,
        root: Path,
        *,
        context_id: str,
        canonical_edges: Sequence[CanonicalEdge],
    ) -> FreshnessResult:
        try:
            projection = SQLiteProjection(root)
            if projection.readiness_trigger() is not None:
                return FreshnessResult(FreshnessState.STALE)
            projected: set[CanonicalEdge] = set()
            for source in sorted({edge.source for edge in canonical_edges}):
                for record in projection.outbound_edges(source):
                    projected.add(
                        CanonicalEdge(
                            source=str(record.source_uri),
                            relation=record.relation.value,
                            target=record.target,
                        )
                    )
        except ContextIsolationError:
            raise
        except ProjectionError:
            return FreshnessResult(FreshnessState.STALE)

        if projected != set(canonical_edges):
            return FreshnessResult(FreshnessState.BACKLINK_MISMATCH)
        return FreshnessResult(FreshnessState.FRESH)
