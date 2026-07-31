from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True, order=True)
class CanonicalEdge:
    source: str
    relation: str
    target: str


class FreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    BACKLINK_MISMATCH = "backlink_mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    state: FreshnessState


class FreshnessProbe(Protocol):
    """Read-only port for derived-state and backlink freshness checks."""

    def probe(
        self,
        root: Path,
        *,
        context_id: str,
        canonical_edges: Sequence[CanonicalEdge],
    ) -> FreshnessResult: ...


class NullFreshnessProbe:
    """Probe used when projection state is intentionally unavailable."""

    def probe(
        self,
        root: Path,
        *,
        context_id: str,
        canonical_edges: Sequence[CanonicalEdge],
    ) -> FreshnessResult:
        del root, context_id, canonical_edges
        return FreshnessResult(FreshnessState.UNKNOWN)
