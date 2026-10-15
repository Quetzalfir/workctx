from __future__ import annotations

from pathlib import Path

from workctx.mcp.application import McpToolService

from .test_filters_cli import _context


def test_mcp_topic_list_and_filtered_reads_match_cli_semantics(tmp_path: Path) -> None:
    projection = _context(tmp_path / "context")
    service = McpToolService(projection.context_root)

    listed = service.invoke("topic_list", {"schema_version": 1})
    assert listed.envelope.ok
    assert [topic["slug"] for topic in listed.envelope.result["topics"]] == [
        "alpha",
        "beta",
    ]
    assert all(
        {"heat", "score", "last_activity"} <= set(topic)
        for topic in listed.envelope.result["topics"]
    )

    searched = service.invoke(
        "search",
        {
            "schema_version": 1,
            "query": "Needle",
            "topic": "alpha-team",
        },
    )
    assert searched.envelope.ok
    assert searched.envelope.result["topic"] == "alpha"
    assert {hit["id"] for hit in searched.envelope.result["hits"]} == {
        "PRJ-alpha",
        "PRJ-common",
        "TASK-2026-001",
        "TASK-2026-002",
    }

    tasks = service.invoke(
        "task_list",
        {"schema_version": 1, "topic": "alpha-team"},
    )
    assert tasks.envelope.ok
    assert {task["id"] for task in tasks.envelope.result["tasks"]} == {
        "TASK-2026-001",
        "TASK-2026-002",
    }

    packed = service.invoke(
        "context_pack",
        {
            "schema_version": 1,
            "uri": "workctx://topic-filter/project/PRJ-common",
            "topic": "alpha-team",
        },
    )
    assert packed.envelope.ok
    serialized = str(packed.envelope.result["pack"])
    assert "PRJ-alpha" in serialized
    assert "PRJ-beta" not in serialized


def test_mcp_unknown_topic_is_a_sanitized_user_correctable_failure(tmp_path: Path) -> None:
    projection = _context(tmp_path / "context")

    response = McpToolService(projection.context_root).invoke(
        "task_list",
        {"schema_version": 1, "topic": "missing-topic"},
    )

    assert not response.envelope.ok
    assert response.envelope.errors[0].category == "user_correctable"
    assert "missing-topic" not in response.model_dump_json()
