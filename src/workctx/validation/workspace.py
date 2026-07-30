from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from workctx.errors import InvalidContextError
from workctx.services.contexts import load_context_config

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

_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret)"
        r"\s*[:=]\s*['\"]?[^\s'\"]{12,}"
    ),
)


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


def validate_workspace(root: Path) -> ValidationReport:
    root = root.expanduser().resolve()
    report = ValidationReport(context_root=root)

    try:
        config = load_context_config(root)
        report.context_id = config.id
        if config.policies.federated_search:
            report.issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "CTX-FEDERATED-SEARCH",
                    "Phase 1 contexts must keep federated_search disabled",
                    "context.yaml",
                )
            )
    except InvalidContextError as exc:
        report.issues.append(
            ValidationIssue(Severity.ERROR, "CTX-CONFIG", str(exc), "context.yaml")
        )
    except Exception as exc:  # validation must aggregate user-correctable failures
        report.issues.append(
            ValidationIssue(Severity.ERROR, "CTX-CONFIG", str(exc), "context.yaml")
        )

    for directory in REQUIRED_DIRECTORIES:
        path = root / directory
        if not path.is_dir():
            report.issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "CTX-MISSING-DIRECTORY",
                    f"Required directory is missing: {directory}",
                    directory,
                )
            )

    for path in _iter_text_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report.issues.append(
                ValidationIssue(
                    Severity.WARNING,
                    "CTX-NON-UTF8",
                    "Canonical text files should use UTF-8",
                    relative,
                )
            )
            continue
        if _contains_durable_absolute_path(content):
            report.issues.append(
                ValidationIssue(
                    Severity.WARNING,
                    "CTX-ABSOLUTE-PATH",
                    "Machine-specific absolute path found; use a canonical URI or relative path",
                    relative,
                )
            )
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            report.issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "CTX-POSSIBLE-SECRET",
                    "Possible secret value found in workspace text",
                    relative,
                )
            )

    return report


def _iter_text_files(root: Path) -> Iterator[Path]:
    allowed = {".md", ".yaml", ".yml", ".json", ".toml", ".txt"}
    excluded_parts = {".git", "98_state"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if any(part in excluded_parts for part in path.relative_to(root).parts):
            continue
        yield path


def _contains_durable_absolute_path(content: str) -> bool:
    for line in content.splitlines():
        stripped = line.strip().strip("`'\"")
        if stripped.startswith("/") and not stripped.startswith("//"):
            return True
        if _WINDOWS_ABSOLUTE.match(stripped):
            return True
    return False
