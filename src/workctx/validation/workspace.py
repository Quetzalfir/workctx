from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from workctx.validation.engine import REQUIRED_DIRECTORIES, WorkspaceValidator
from workctx.validation.freshness import FreshnessProbe
from workctx.validation.report import Severity, ValidationIssue, ValidationReport


def validate_workspace(
    root: Path,
    *,
    strict: bool = False,
    strict_paths: Collection[str] | None = None,
    freshness_probe: FreshnessProbe | None = None,
    overlay_files: Mapping[str, bytes | None] | None = None,
) -> ValidationReport:
    """Validate canonical workspace integrity without mutating the workspace.

    When ``strict_paths`` is provided, warning escalation is limited to those
    context-relative files. All workspace content and relationships are still
    validated, and existing errors remain errors regardless of path. ``overlay_files``
    supplies an in-memory final-state view for transaction dry-runs without writing it.
    """

    return WorkspaceValidator(
        root=root,
        strict=strict,
        strict_paths=strict_paths,
        freshness_probe=freshness_probe,
        overlay_files=overlay_files,
    ).validate()


__all__ = [
    "REQUIRED_DIRECTORIES",
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "validate_workspace",
]
