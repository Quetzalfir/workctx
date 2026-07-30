import pytest

from workctx.models.reference import WorkctxUri


def test_workctx_uri_round_trip_with_observation_fragment_in_id() -> None:
    uri = WorkctxUri(
        context_id="company-a",
        entity_type="observation",
        entity_id="EVD-20260730-auth-01#OBS-004",
    )
    rendered = str(uri)
    assert rendered.endswith("EVD-20260730-auth-01%23OBS-004")
    assert WorkctxUri.parse(rendered) == uri


def test_workctx_uri_enforces_context_boundary() -> None:
    uri = WorkctxUri.parse("workctx://company-a/task/TASK-2026-001")
    with pytest.raises(ValueError, match="not 'company-b'"):
        uri.require_context("company-b")


def test_workctx_uri_rejects_non_ascii_or_malformed_boundaries() -> None:
    with pytest.raises(ValueError, match="Invalid context ID"):
        WorkctxUri(context_id="compañia", entity_type="task", entity_id="TASK-1")
    with pytest.raises(ValueError, match="Entity types"):
        WorkctxUri(context_id="company-a", entity_type="Task", entity_id="TASK-1")


def test_workctx_uri_rejects_query_fragment_and_traversal_id() -> None:
    with pytest.raises(ValueError, match="query"):
        WorkctxUri.parse("workctx://company-a/task/TASK-1?other=context")
    with pytest.raises(ValueError, match="path traversal"):
        WorkctxUri.parse("workctx://company-a/task/..")
