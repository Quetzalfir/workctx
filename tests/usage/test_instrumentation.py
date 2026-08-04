from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from workctx.adapters.sqlite import SQLiteProjection
from workctx.mcp import McpToolService
from workctx.retrieval import build_pack, resolve, trace

from .support import REPO_URI, initialize_usage_context, write_task


def test_projection_retrieval_and_mcp_read_seams_emit_only_approved_api_names(
    tmp_path: Path,
) -> None:
    root = initialize_usage_context(tmp_path / "instrumentation", enabled=True)
    task_uri = write_task(
        root,
        "TASK-2026-020",
        updated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    projection = SQLiteProjection(root)
    projection.rebuild()

    projection.search("fictional private query")
    resolve(projection, REPO_URI)
    trace(projection, task_uri)
    build_pack(projection, task_uri, budget=1000)
    response = McpToolService(root).invoke("task_show", {"schema_version": 1, "task": task_uri})
    assert response.envelope.ok is True

    path = root / "98_state" / "usage" / "usage.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    apis = {event["api"] for event in events}
    assert {"search", "resolve", "trace", "build_pack", "mcp.task_show"} <= apis
    assert "fictional private query" not in path.read_text(encoding="utf-8")
    assert any(event.get("target_uri") == REPO_URI for event in events)
    assert any(event.get("target_uri") == task_uri for event in events)
