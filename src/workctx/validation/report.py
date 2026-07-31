from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    ADVISORY = "advisory"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    path: str | None = None
    repair_action: str | None = None


@dataclass(slots=True)
class ValidationReport:
    context_root: Path
    context_id: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors
