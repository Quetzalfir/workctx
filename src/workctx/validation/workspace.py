from __future__ import annotations

from pathlib import Path

from workctx.validation.engine import REQUIRED_DIRECTORIES, WorkspaceValidator
from workctx.validation.freshness import FreshnessProbe
from workctx.validation.report import Severity, ValidationIssue, ValidationReport


def validate_workspace(
    root: Path,
    *,
    strict: bool = False,
    freshness_probe: FreshnessProbe | None = None,
) -> ValidationReport:
    """Validate canonical workspace integrity without mutating the workspace."""

    return WorkspaceValidator(
        root=root,
        strict=strict,
        freshness_probe=freshness_probe,
    ).validate()


__all__ = [
    "REQUIRED_DIRECTORIES",
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "validate_workspace",
]
