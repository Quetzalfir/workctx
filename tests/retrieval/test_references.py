from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workctx.adapters.sqlite import EntityRecord, SQLiteProjection
from workctx.domain import WorkctxUri
from workctx.retrieval.protocols import ProjectionReader
from workctx.retrieval.records import (
    ArtifactReferenceDescriptor,
    RepoReferenceDescriptor,
    ResolutionStatus,
    WorkctxReferenceDescriptor,
)
from workctx.retrieval.references import ContextBoundaryError, resolve
from workctx.services.contexts import initialize_context

ARTIFACT_REFERENCE = f"artifact://sha256/{'a' * 64}"
TIMESTAMP = "2026-07-30T12:00:00Z"


def test_resolve_workctx_reference_and_explicit_not_found(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    _create_context(root)
    entity_uri = "workctx://fictional-context/system/SYS-known"
    _write_markdown(
        root / "02_knowledge" / "system-known.md",
        _entity_frontmatter("SYS-known", "Known system"),
        "Known fictional system.",
    )
    projection = SQLiteProjection(root)
    projection.rebuild()

    assert isinstance(projection, ProjectionReader)
    resolved = resolve(projection, entity_uri)
    missing = resolve(
        projection,
        "workctx://fictional-context/system/SYS-not-present",
    )

    assert resolved.status is ResolutionStatus.RESOLVED
    assert isinstance(resolved.descriptor, WorkctxReferenceDescriptor)
    assert resolved.descriptor.context_id == "fictional-context"
    assert resolved.descriptor.entity_type == "system"
    assert resolved.descriptor.entity_id == "SYS-known"
    assert isinstance(resolved.record, EntityRecord)
    assert missing.status is ResolutionStatus.NOT_FOUND
    assert isinstance(missing.descriptor, WorkctxReferenceDescriptor)
    assert missing.record is None


def test_resolve_refuses_foreign_workctx_reference_at_retrieval_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    _create_context(root)
    projection = SQLiteProjection(root)

    with pytest.raises(ContextBoundaryError, match="another context"):
        resolve(projection, "workctx://other-context/task/TASK-2026-001")


def test_resolve_rejects_unknown_entity_type_even_for_typed_uri(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    _create_context(root)
    projection = SQLiteProjection(root)
    unknown = WorkctxUri(
        context_id="fictional-context",
        entity_type="unknown",
        entity_id="UNKNOWN-001",
    )

    with pytest.raises(ValueError, match="Unknown Work Context entity type"):
        resolve(projection, unknown)


def test_resolve_returns_structural_artifact_and_repository_descriptors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    _create_context(root)
    projection = SQLiteProjection(root)
    repo_reference = "repo://fictional.repo@abcdef1/src/auth.py#L12-L19"

    artifact = resolve(projection, ARTIFACT_REFERENCE)
    repository = resolve(projection, repo_reference)

    assert artifact.status is ResolutionStatus.RESOLVED
    assert isinstance(artifact.descriptor, ArtifactReferenceDescriptor)
    assert artifact.descriptor.algorithm == "sha256"
    assert artifact.descriptor.digest == "a" * 64
    assert artifact.reference == ARTIFACT_REFERENCE
    assert artifact.record is None
    assert repository.status is ResolutionStatus.RESOLVED
    assert isinstance(repository.descriptor, RepoReferenceDescriptor)
    assert repository.descriptor.repo_id == "fictional.repo"
    assert repository.descriptor.commit == "abcdef1"
    assert repository.descriptor.path == "src/auth.py"
    assert repository.descriptor.start_line == 12
    assert repository.descriptor.end_line == 19
    assert repository.reference == repo_reference
    assert repository.record is None


def _create_context(root: Path) -> None:
    initialize_context(
        root,
        name="Fictional context",
        context_id="fictional-context",
    )


def _entity_frontmatter(entity_id: str, title: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": entity_id,
        "entity_type": "system",
        "title": title,
        "uri": f"workctx://fictional-context/system/{entity_id}",
        "aliases": [],
        "status": "active",
        "confidence": "high",
        "tags": ["fictional"],
        "references": [],
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
    }


def _write_markdown(path: Path, frontmatter: dict[str, object], body: str) -> None:
    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    path.write_text(f"---\n{rendered}---\n\n{body}\n", encoding="utf-8", newline="\n")
