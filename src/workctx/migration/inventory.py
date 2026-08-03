"""Read-only inventory, hashing, classification, and finding detection."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from workctx.domain import EntityType
from workctx.domain.frontmatter import parse_frontmatter
from workctx.ingestion import IngestionPolicy, QuarantineReason
from workctx.ingestion.errors import ArtifactReadError
from workctx.ingestion.scanning import scan_artifact
from workctx.migration.errors import MigrationBoundaryError
from workctx.migration.models import (
    FileClassification,
    FindingSeverity,
    InventoryRecord,
    MigrationFinding,
)
from workctx.validation.engine import contains_possible_secret

_TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".conf",
        ".ini",
        ".json",
        ".log",
        ".md",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_GENERATED_PARTS = frozenset({"04_views", "generated", "indexes", "views"})
_GENERATED_NAMES = frozenset({"brief.md", "current-focus.md", "next-actions.md", "waiting-on.md"})
_OBSOLETE_PARTS = frozenset({".git", ".obsidian", ".venv", "archive", "node_modules", "obsolete"})
_OBSOLETE_NAMES = frozenset({"agents.md", "readme.md"})
_DIRECTORY_ENTITY_TYPES = {
    "claims": "claim",
    "decisions": "decision",
    "drafts": "draft",
    "evidence": "evidence",
    "flows": "flow",
    "incidents": "incident",
    "integrations": "integration",
    "investigations": "investigation",
    "modules": "module",
    "people": "person",
    "persons": "person",
    "projects": "project",
    "questions": "question",
    "risks": "risk",
    "services": "service",
    "systems": "system",
    "tasks": "task",
    "teams": "team",
}
_ID_ENTITY_TYPES = {
    "ART-": "artifact",
    "CLM-": "claim",
    "DEC-": "decision",
    "DRAFT-": "draft",
    "EVD-": "evidence",
    "FLOW-": "flow",
    "INC-": "incident",
    "INT-": "integration",
    "INV-": "investigation",
    "MOD-": "module",
    "PER-": "person",
    "PRJ-": "project",
    "Q-": "question",
    "RISK-": "risk",
    "SVC-": "service",
    "SYS-": "system",
    "TASK-": "task",
    "TEAM-": "team",
}
_MARKDOWN_LINK = re.compile(r"!?(?:\[[^\]]*\])\((?P<target>[^)]+)\)")
_WIKI_LINK = re.compile(r"\[\[(?P<target>[^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s<>'\"]+)")
_POSIX_ABSOLUTE = re.compile(
    r"(?<![:A-Za-z0-9])/(?:Users|Volumes|etc|home|mnt|opt|private|tmp|usr|var)/[^\s<>'\"]+"
)
_TOP_LEVEL_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True, slots=True)
class LegacyDocument:
    absolute_path: Path
    relative_path: str
    report_path: str
    content_hash: str
    text: str
    frontmatter: dict[str, Any]
    body: str
    entity_type: str
    old_id: str | None
    frontmatter_spans: dict[str, tuple[int, int]]
    contains_secret: bool
    unsafe_content: bool


@dataclass(frozen=True, slots=True)
class LegacyFile:
    absolute_path: Path
    relative_path: str
    report_path: str
    content_hash: str
    size_bytes: int
    classification: FileClassification
    entity_type: str | None
    contains_secret: bool
    unsafe_content: bool


@dataclass(frozen=True, slots=True)
class InventoryAnalysis:
    root: Path
    tree_hash: str
    records: tuple[InventoryRecord, ...]
    files: tuple[LegacyFile, ...]
    documents: tuple[LegacyDocument, ...]
    findings: tuple[MigrationFinding, ...]


def inventory_source(source_root: Path) -> InventoryAnalysis:
    """Inventory a legacy tree without following links or executing content."""

    root = _resolve_source_root(source_root)
    records: list[InventoryRecord] = []
    files: list[LegacyFile] = []
    documents: list[LegacyDocument] = []
    findings: list[MigrationFinding] = []
    digest_rows: list[tuple[str, int, str]] = []

    for path in _iter_regular_files(root):
        relative = path.relative_to(root).as_posix()
        report_path = safe_report_path(relative)
        content_hash, size_bytes = hash_file(path)
        digest_rows.append((relative, size_bytes, content_hash))
        text = _read_text(path)
        path_secret = contains_possible_secret(relative)
        content_secret = text is not None and contains_possible_secret(text)
        has_secret = path_secret or content_secret
        unsafe_content = False
        frontmatter: dict[str, Any] | None = None
        body = ""
        parse_failed = False
        if path.suffix.casefold() == ".md" and text is not None and text.startswith("---"):
            try:
                frontmatter, body = parse_frontmatter(text)
            except (RecursionError, ValueError, yaml.YAMLError):
                parse_failed = True

        classification, entity_type = _classify(path, relative, frontmatter)
        if classification is FileClassification.CANONICAL:
            try:
                safety_scan = scan_artifact(
                    path,
                    declared_media_type=None,
                    policy=IngestionPolicy(),
                )
            except ArtifactReadError as exc:
                raise MigrationBoundaryError(
                    "A canonical legacy source file changed or could not be scanned safely."
                ) from exc
            has_secret = has_secret or QuarantineReason.POSSIBLE_SECRET in safety_scan.reasons
            unsafe_content = (
                entity_type != "artifact"
                and QuarantineReason.PROMPT_INJECTION in safety_scan.reasons
            )
        if parse_failed and classification is FileClassification.UNKNOWN:
            findings.append(
                _finding(
                    "MIG-FRONTMATTER-PARSE",
                    FindingSeverity.WARNING,
                    report_path,
                    "Frontmatter could not be parsed; the file will be skipped.",
                )
            )

        if entity_type is not None and entity_type not in {item.value for item in EntityType}:
            findings.append(
                _finding(
                    "MIG-UNKNOWN-ENTITY-TYPE",
                    FindingSeverity.WARNING,
                    report_path,
                    "The declared entity type is outside the canonical vocabulary.",
                    locator="frontmatter:entity_type",
                )
            )

        if has_secret:
            findings.append(
                _finding(
                    "MIG-POSSIBLE-SECRET",
                    FindingSeverity.ERROR,
                    report_path,
                    "Secret-like content was detected; apply will not copy this file.",
                    blocks_apply=True,
                )
            )
        if unsafe_content:
            findings.append(
                _finding(
                    "MIG-UNSAFE-CONTENT",
                    FindingSeverity.ERROR,
                    report_path,
                    "Instruction-like content was detected; apply will not migrate this file.",
                    blocks_apply=True,
                )
            )
        if text is not None:
            findings.extend(_absolute_path_findings(text, report_path))
            if path.suffix.casefold() == ".md":
                findings.extend(_broken_link_findings(root, path, text, report_path))

        old_id_value = frontmatter.get("id") if frontmatter is not None else None
        old_id = old_id_value if isinstance(old_id_value, str) and old_id_value else None
        records.append(
            InventoryRecord(
                path=report_path,
                classification=classification,
                content_hash=content_hash,
                size_bytes=size_bytes,
                entity_type=entity_type,
            )
        )
        files.append(
            LegacyFile(
                absolute_path=path,
                relative_path=relative,
                report_path=report_path,
                content_hash=content_hash,
                size_bytes=size_bytes,
                classification=classification,
                entity_type=entity_type,
                contains_secret=has_secret,
                unsafe_content=unsafe_content,
            )
        )
        if (
            classification is FileClassification.CANONICAL
            and entity_type is not None
            and entity_type != "artifact"
            and frontmatter is not None
            and text is not None
        ):
            documents.append(
                LegacyDocument(
                    absolute_path=path,
                    relative_path=relative,
                    report_path=report_path,
                    content_hash=content_hash,
                    text=text,
                    frontmatter=frontmatter,
                    body=body,
                    entity_type=entity_type,
                    old_id=old_id,
                    frontmatter_spans=_frontmatter_spans(text),
                    contains_secret=has_secret,
                    unsafe_content=unsafe_content,
                )
            )

    findings.extend(_duplicate_id_findings(documents))
    return InventoryAnalysis(
        root=root,
        tree_hash=_tree_hash(digest_rows),
        records=tuple(sorted(records, key=lambda item: item.path.casefold())),
        files=tuple(sorted(files, key=lambda item: item.relative_path.casefold())),
        documents=tuple(sorted(documents, key=lambda item: item.relative_path.casefold())),
        findings=tuple(_deduplicate_findings(findings)),
    )


def fingerprint_source_tree(source_root: Path) -> str:
    """Return the byte-and-relative-path fingerprint used by stages 2 and 13."""

    root = _resolve_source_root(source_root)
    rows: list[tuple[str, int, str]] = []
    for path in _iter_regular_files(root):
        relative = path.relative_to(root).as_posix()
        content_hash, size_bytes = hash_file(path)
        rows.append((relative, size_bytes, content_hash))
    return _tree_hash(rows)


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise MigrationBoundaryError("A legacy source file could not be read safely.") from exc
    return f"sha256:{digest.hexdigest()}", size


def safe_report_path(relative_path: str) -> str:
    """Prevent a secret-looking filename from leaking through a report locator."""

    if contains_possible_secret(relative_path):
        suffix = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:12]
        return f"[redacted-path-{suffix}]"
    return relative_path


def is_raw_evidence_path(relative_path: str) -> bool:
    parts = tuple(part.casefold() for part in PurePosixPath(relative_path).parts)
    if not parts:
        return False
    if parts[0] == "raw":
        return True
    return any(
        part == "raw" and index > 0 and parts[index - 1] in {"00_inbox", "evidence", "inbox"}
        for index, part in enumerate(parts)
    )


def _resolve_source_root(source_root: Path) -> Path:
    expanded = source_root.expanduser()
    if expanded.is_symlink() or _is_junction(expanded):
        raise MigrationBoundaryError("The legacy source root cannot be a link or junction.")
    try:
        root = expanded.resolve(strict=True)
    except OSError as exc:
        raise MigrationBoundaryError(
            "The legacy source root does not exist or is unreadable."
        ) from exc
    if not root.is_dir():
        raise MigrationBoundaryError("The legacy source path must be a directory.")
    return root


def _iter_regular_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    try:
        for current, directories, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            directories.sort(key=lambda value: (value.casefold(), value))
            filenames.sort(key=lambda value: (value.casefold(), value))
            for directory in directories:
                candidate = current_path / directory
                if candidate.is_symlink() or _is_junction(candidate):
                    raise MigrationBoundaryError(
                        "The legacy source contains a directory link or junction."
                    )
            for filename in filenames:
                candidate = current_path / filename
                if candidate.is_symlink() or _is_junction(candidate):
                    raise MigrationBoundaryError("The legacy source contains a file link.")
                if not candidate.is_file():
                    raise MigrationBoundaryError(
                        "The legacy source contains an unsupported filesystem entry."
                    )
                files.append(candidate)
    except MigrationBoundaryError:
        raise
    except OSError as exc:
        raise MigrationBoundaryError("The legacy source tree could not be inventoried.") from exc
    return tuple(files)


def _read_text(path: Path) -> str | None:
    if path.suffix.casefold() not in _TEXT_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError as exc:
        raise MigrationBoundaryError("A legacy text file could not be read safely.") from exc


def _classify(
    path: Path,
    relative_path: str,
    frontmatter: dict[str, Any] | None,
) -> tuple[FileClassification, str | None]:
    parts = tuple(part.casefold() for part in PurePosixPath(relative_path).parts)
    name = path.name.casefold()
    if any(part in _GENERATED_PARTS for part in parts) or name in _GENERATED_NAMES:
        return FileClassification.GENERATED, None
    if frontmatter is not None and isinstance(frontmatter.get("generated_by"), str):
        return FileClassification.GENERATED, None
    if any(part in _OBSOLETE_PARTS for part in parts) or name in _OBSOLETE_NAMES:
        return FileClassification.OBSOLETE, None
    if is_raw_evidence_path(relative_path):
        return FileClassification.CANONICAL, "artifact"
    if path.suffix.casefold() != ".md" or frontmatter is None:
        return FileClassification.UNKNOWN, None

    explicit = frontmatter.get("entity_type", frontmatter.get("type"))
    entity_type = explicit.strip().casefold() if isinstance(explicit, str) else None
    if entity_type is None:
        entity_type = _infer_entity_type(parts, frontmatter.get("id"))
    if entity_type is None:
        return FileClassification.UNKNOWN, None
    if entity_type == "artifact":
        return FileClassification.OBSOLETE, entity_type
    if entity_type not in {item.value for item in EntityType}:
        return FileClassification.UNKNOWN, entity_type
    return FileClassification.CANONICAL, entity_type


def _infer_entity_type(parts: tuple[str, ...], identifier: object) -> str | None:
    for part in reversed(parts[:-1]):
        inferred = _DIRECTORY_ENTITY_TYPES.get(part)
        if inferred is not None:
            return inferred
    if isinstance(identifier, str):
        for prefix, entity_type in _ID_ENTITY_TYPES.items():
            if identifier.startswith(prefix):
                return entity_type
    return None


def _absolute_path_findings(text: str, report_path: str) -> list[MigrationFinding]:
    findings: list[MigrationFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _WINDOWS_ABSOLUTE.search(line) is None and _POSIX_ABSOLUTE.search(line) is None:
            continue
        findings.append(
            _finding(
                "MIG-ABSOLUTE-PATH",
                FindingSeverity.WARNING,
                report_path,
                "A machine-specific absolute path will be replaced by an unavailable marker.",
                locator=f"line:{line_number}",
            )
        )
    return findings


def _broken_link_findings(
    root: Path,
    document_path: Path,
    text: str,
    report_path: str,
) -> list[MigrationFinding]:
    findings: list[MigrationFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        targets = [match.group("target") for match in _MARKDOWN_LINK.finditer(line)]
        targets.extend(match.group("target") for match in _WIKI_LINK.finditer(line))
        for authored_target in targets:
            target = _link_path(authored_target)
            if target is None or _link_resolves(root, document_path, target):
                continue
            findings.append(
                _finding(
                    "MIG-BROKEN-LINK",
                    FindingSeverity.WARNING,
                    report_path,
                    "A legacy internal link does not resolve and will be marked unavailable.",
                    locator=f"line:{line_number}",
                )
            )
    return findings


def _link_path(authored_target: str) -> str | None:
    target = authored_target.strip().strip("<>")
    if not target:
        return None
    if " " in target:
        target = target.split(maxsplit=1)[0]
    if target.startswith("#") or _URI_SCHEME.match(target):
        return None
    try:
        parsed = urlsplit(target)
    except ValueError:
        return target
    path = unquote(parsed.path)
    return path or None


def _link_resolves(root: Path, document_path: Path, target: str) -> bool:
    if target.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE.match(target):
        return False
    candidate = document_path.parent.joinpath(*PurePosixPath(target.replace("\\", "/")).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return False
    return resolved.is_relative_to(root) and resolved.is_file()


def _frontmatter_spans(text: str) -> dict[str, tuple[int, int]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    key_lines: list[tuple[str, int]] = []
    closing = len(lines) + 1
    for index, line in enumerate(lines[1:], start=2):
        if line == "---":
            closing = index
            break
        match = _TOP_LEVEL_KEY.match(line)
        if match is not None:
            key_lines.append((match.group("key"), index))
    spans: dict[str, tuple[int, int]] = {}
    for index, (key, start) in enumerate(key_lines):
        end = key_lines[index + 1][1] - 1 if index + 1 < len(key_lines) else closing - 1
        spans[key] = (start, max(start, end))
    return spans


def _duplicate_id_findings(documents: list[LegacyDocument]) -> list[MigrationFinding]:
    by_id: dict[str, list[LegacyDocument]] = {}
    for document in documents:
        if document.old_id is not None:
            by_id.setdefault(document.old_id, []).append(document)
    findings: list[MigrationFinding] = []
    for duplicates in by_id.values():
        ordered = sorted(duplicates, key=lambda item: item.relative_path.casefold())
        for duplicate in ordered[1:]:
            findings.append(
                _finding(
                    "MIG-DUPLICATE-ID",
                    FindingSeverity.WARNING,
                    duplicate.report_path,
                    "A duplicate legacy ID will receive a new deterministic target ID.",
                    locator="frontmatter:id",
                )
            )
    return findings


def _tree_hash(rows: list[tuple[str, int, str]]) -> str:
    digest = hashlib.sha256()
    for relative, size, content_hash in sorted(rows, key=lambda item: item[0].casefold()):
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(content_hash.removeprefix("sha256:")))
    return f"sha256:{digest.hexdigest()}"


def _finding(
    code: str,
    severity: FindingSeverity,
    path: str,
    message: str,
    *,
    locator: str | None = None,
    blocks_apply: bool = False,
) -> MigrationFinding:
    return MigrationFinding(
        code=code,
        severity=severity,
        path=path,
        locator=locator,
        message=message,
        blocks_apply=blocks_apply,
    )


def _deduplicate_findings(findings: list[MigrationFinding]) -> list[MigrationFinding]:
    unique = {finding.model_dump_json(): finding for finding in findings}
    return sorted(
        unique.values(),
        key=lambda item: (item.path.casefold(), item.code, item.locator or ""),
    )


def _is_junction(path: Path) -> bool:
    try:
        return path.is_junction()
    except (AttributeError, OSError):
        return False


__all__ = [
    "InventoryAnalysis",
    "LegacyDocument",
    "LegacyFile",
    "fingerprint_source_tree",
    "hash_file",
    "inventory_source",
    "is_raw_evidence_path",
    "safe_report_path",
]
