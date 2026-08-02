"""Read-only MCP resource contract and context-boundary tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from projections.support import create_fictional_context

from workctx.adapters.sqlite import SQLiteProjection
from workctx.mcp.models import ErrorCategory, ResourceAccessError
from workctx.mcp.resources import McpResourceService

CONTEXT_ID = "fictional-context"
SYSTEM_URI = f"workctx://{CONTEXT_ID}/system/SYS-identity-service"


@pytest.fixture
def resource_context(tmp_path: Path) -> tuple[Path, dict[str, Path], McpResourceService]:
    root = tmp_path / "context"
    paths = create_fictional_context(root, CONTEXT_ID)
    SQLiteProjection(root).rebuild()
    return root, paths, McpResourceService(root)


def test_resource_discovery_is_read_only_and_bound_to_one_context(
    resource_context: tuple[Path, dict[str, Path], McpResourceService],
) -> None:
    _root, _paths, service = resource_context

    assert service.context_id == CONTEXT_ID
    resources = service.list_resources()
    assert len(resources) == 1
    configuration = resources[0]
    assert configuration.uri == f"workctx://{CONTEXT_ID}/context/configuration"
    assert configuration.name == "context_configuration"
    assert configuration.mime_type == "application/json"

    templates = service.list_templates()
    assert len(templates) == 1
    assert templates[0].uri_template == (f"workctx://{CONTEXT_ID}/{{entity_type}}/{{entity_id}}")
    assert templates[0].name == "canonical_entity"
    assert not hasattr(service, "write")


def test_configuration_resource_returns_typed_context_without_machine_paths(
    resource_context: tuple[Path, dict[str, Path], McpResourceService],
) -> None:
    root, _paths, service = resource_context

    content = service.read(service.configuration_uri)
    payload = json.loads(content.text)

    assert content.uri == service.configuration_uri
    assert content.mime_type == "application/json"
    assert payload["schema_version"] == 1
    assert payload["context_id"] == CONTEXT_ID
    assert payload["kind"] == "context_configuration"
    assert payload["trust"] == "trusted_configuration"
    assert payload["configuration"]["id"] == CONTEXT_ID
    assert str(root.resolve()) not in content.text


@pytest.mark.parametrize(
    ("uri", "expected_id", "expected_type"),
    [
        (SYSTEM_URI, "SYS-identity-service", "system"),
        (
            f"workctx://{CONTEXT_ID}/task/TASK-2026-001",
            "TASK-2026-001",
            "task",
        ),
        (
            f"workctx://{CONTEXT_ID}/claim/CLM-2026-00002",
            "CLM-2026-00002",
            None,
        ),
        (
            f"workctx://{CONTEXT_ID}/observation/EVD-20260730-auth-flow-01%23OBS-001",
            "EVD-20260730-auth-flow-01#OBS-001",
            None,
        ),
    ],
)
def test_canonical_entity_resources_return_validated_frontmatter_only(
    resource_context: tuple[Path, dict[str, Path], McpResourceService],
    uri: str,
    expected_id: str,
    expected_type: str | None,
) -> None:
    root, _paths, service = resource_context

    content = service.read(uri)
    payload = json.loads(content.text)

    assert payload["schema_version"] == 1
    assert payload["context_id"] == CONTEXT_ID
    assert payload["kind"] == "canonical_frontmatter"
    assert payload["trust"] == "untrusted_data"
    assert payload["uri"] == uri
    assert payload["frontmatter"]["id"] == expected_id
    if expected_type is not None:
        assert payload["frontmatter"]["entity_type"] == expected_type
    assert "source_path" not in payload["frontmatter"]
    assert str(root.resolve()) not in content.text
    assert "central policy enforcement service" not in content.text.lower()


def test_foreign_context_resource_is_refused(
    resource_context: tuple[Path, dict[str, Path], McpResourceService],
) -> None:
    _root, _paths, service = resource_context

    with pytest.raises(ResourceAccessError) as captured:
        service.read("workctx://other-context/system/SYS-identity-service")

    diagnostic = captured.value.diagnostic
    assert diagnostic.code == "REF-CONTEXT-MISMATCH"
    assert diagnostic.category is ErrorCategory.CONTEXT_BOUNDARY
    assert diagnostic.message == "The requested resource is outside the bound context."


def test_projected_path_escape_is_refused_before_file_read(
    resource_context: tuple[Path, dict[str, Path], McpResourceService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _paths, service = resource_context
    record = SQLiteProjection(root).get_entity_by_id("SYS-identity-service")
    assert record is not None
    escaped_record = replace(record, source_path="../outside.md")
    monkeypatch.setattr(
        "workctx.mcp.resources.resolve",
        lambda _reader, _reference: SimpleNamespace(found=True, record=escaped_record),
    )

    with pytest.raises(ResourceAccessError) as captured:
        service.read(SYSTEM_URI)

    diagnostic = captured.value.diagnostic
    assert diagnostic.category is ErrorCategory.CONTEXT_BOUNDARY
    assert diagnostic.code == "CTX-PATH-ESCAPE"


def test_canonical_symlink_escape_is_refused_without_leaking_target(
    resource_context: tuple[Path, dict[str, Path], McpResourceService],
    tmp_path: Path,
) -> None:
    _root, paths, service = resource_context
    secret = "api_key=fictional-should-not-cross-mcp"
    outside = tmp_path / "outside.md"
    outside.write_text(secret, encoding="utf-8")
    canonical = paths["system"]
    canonical.unlink()
    try:
        canonical.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"File symlink creation is unavailable for this test user: {exc}")

    with pytest.raises(ResourceAccessError) as captured:
        service.read(SYSTEM_URI)

    diagnostic = captured.value.diagnostic
    serialized = diagnostic.model_dump_json()
    assert diagnostic.category is ErrorCategory.CONTEXT_BOUNDARY
    assert secret not in serialized
    assert "Traceback" not in serialized


def test_unexpected_resource_failure_is_sanitized(
    resource_context: tuple[Path, dict[str, Path], McpResourceService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, _paths, service = resource_context

    def fail_resolve(_reader: object, _reference: object) -> object:
        raise RuntimeError("api_key=fictional-secret\nTraceback: private detail")

    monkeypatch.setattr("workctx.mcp.resources.resolve", fail_resolve)

    with pytest.raises(ResourceAccessError) as captured:
        service.read(SYSTEM_URI)

    diagnostic = captured.value.diagnostic
    serialized = diagnostic.model_dump_json()
    assert diagnostic.code == "INTERNAL_ERROR"
    assert diagnostic.category is ErrorCategory.INTERNAL_FAILURE
    assert diagnostic.message == "Unexpected internal failure."
    assert "fictional-secret" not in serialized
    assert "Traceback" not in serialized
