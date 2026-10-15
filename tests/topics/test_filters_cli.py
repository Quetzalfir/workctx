from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from workctx.adapters.sqlite import SQLiteProjection, TaskQuery
from workctx.cli import app
from workctx.domain import Topic, TopicRegistry, TopicStatus
from workctx.retrieval import build_pack, serialize_context_pack
from workctx.services.contexts import initialize_context
from workctx.views import brief

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
runner = CliRunner()


def _write_markdown(path: Path, payload: dict[str, Any], body: str) -> None:
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{rendered}---\n\n{body}\n", encoding="utf-8")


def _entity(
    identifier: str,
    title: str,
    *,
    topics: list[str] | None = None,
    references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": identifier,
        "entity_type": "project",
        "title": title,
        "uri": f"workctx://topic-filter/project/{identifier}",
        "aliases": [],
        "status": "active",
        "confidence": "high",
        "tags": [],
        "topics": [] if topics is None else topics,
        "references": [] if references is None else references,
        "created_at": "2026-10-14T12:00:00Z",
        "updated_at": "2026-10-14T12:00:00Z",
    }


def _task(identifier: str, title: str, topics: list[str] | None = None) -> dict[str, Any]:
    return {
        **_entity(identifier, title, topics=topics),
        "entity_type": "task",
        "uri": f"workctx://topic-filter/task/{identifier}",
        "task_type": "parent",
        "parent_task": None,
        "root_task": identifier,
        "priority": "P2",
        "status": "active",
        "owner": None,
        "requester": None,
        "waiting_on": [],
        "due_at": None,
        "next_action": "Continue the fictional topic-filter test.",
        "dependencies": [],
        "blockers": [],
        "source_observations": [],
    }


def _context(root: Path) -> SQLiteProjection:
    initialize_context(root, name="Topic Filter", context_id="topic-filter")
    registry = TopicRegistry(
        schema_version=1,
        topics=[
            Topic(
                slug="alpha",
                name="Alpha",
                scope="Alpha workstream.",
                aliases=["alpha-team"],
                status=TopicStatus.ACTIVE,
                created_at=NOW,
            ),
            Topic(
                slug="beta",
                name="Beta",
                scope="Beta workstream.",
                aliases=[],
                status=TopicStatus.ACTIVE,
                created_at=NOW,
            ),
        ],
    )
    (root / "99_meta" / "topics.yaml").write_text(
        yaml.safe_dump(
            registry.model_dump(mode="json", exclude_none=True),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    alpha_uri = "workctx://topic-filter/project/PRJ-alpha"
    beta_uri = "workctx://topic-filter/project/PRJ-beta"
    _write_markdown(
        root / "02_knowledge" / "PRJ-common.md",
        _entity(
            "PRJ-common",
            "Needle Common",
            references=[
                {"relation": "mentions", "target": alpha_uri},
                {"relation": "mentions", "target": beta_uri},
            ],
        ),
        "Common needle material.",
    )
    _write_markdown(
        root / "02_knowledge" / "PRJ-alpha.md",
        _entity("PRJ-alpha", "Needle Alpha", topics=["alpha-team"]),
        "Alpha needle material.",
    )
    _write_markdown(
        root / "02_knowledge" / "PRJ-beta.md",
        _entity("PRJ-beta", "Needle Beta", topics=["beta"]),
        "Beta needle material.",
    )
    _write_markdown(
        root / "03_work" / "TASK-2026-001.md",
        _task("TASK-2026-001", "Needle common task"),
        "Common task.",
    )
    _write_markdown(
        root / "03_work" / "TASK-2026-002.md",
        _task("TASK-2026-002", "Needle alpha task", ["alpha"]),
        "Alpha task.",
    )
    _write_markdown(
        root / "03_work" / "TASK-2026-003.md",
        _task("TASK-2026-003", "Needle beta task", ["beta"]),
        "Beta task.",
    )
    projection = SQLiteProjection(root)
    projection.rebuild()
    return projection


def test_projection_filters_include_common_by_default_and_canonicalize_aliases(
    tmp_path: Path,
) -> None:
    projection = _context(tmp_path / "context")

    alpha = projection.get_entity_by_id("PRJ-alpha")
    assert alpha is not None and alpha.topics == ("alpha",)
    assert {hit.id for hit in projection.search("Needle", topic="alpha")} == {
        "PRJ-alpha",
        "PRJ-common",
        "TASK-2026-001",
        "TASK-2026-002",
    }
    assert {hit.id for hit in projection.search("Needle", topic="alpha", topic_only=True)} == {
        "PRJ-alpha",
        "TASK-2026-002",
    }
    assert {task.id for task in projection.query_tasks(TaskQuery(topic="alpha"))} == {
        "TASK-2026-001",
        "TASK-2026-002",
    }
    assert {
        task.id for task in projection.query_tasks(TaskQuery(topic="alpha", topic_only=True))
    } == {"TASK-2026-002"}


def test_context_pack_and_brief_honor_topic_scope(tmp_path: Path) -> None:
    projection = _context(tmp_path / "context")

    outcome = build_pack(
        projection,
        "workctx://topic-filter/project/PRJ-common",
        topic="alpha",
    )
    assert outcome.built and outcome.pack is not None
    serialized = serialize_context_pack(outcome.pack)
    assert "workctx://topic-filter/project/PRJ-alpha" in serialized
    assert "workctx://topic-filter/project/PRJ-beta" not in serialized

    scoped = brief(projection.context_root, topic="alpha")
    assert {item.id for item in scoped.today_focus} == {
        "TASK-2026-001",
        "TASK-2026-002",
    }
    strict = brief(projection.context_root, topic="alpha", topic_only=True)
    assert {item.id for item in strict.today_focus} == {"TASK-2026-002"}


def test_cli_resolves_alias_for_all_topic_filtered_commands(tmp_path: Path) -> None:
    projection = _context(tmp_path / "context")
    context = str(projection.context_root)

    search = runner.invoke(
        app,
        ["search", "Needle", "--topic", "alpha-team", "--context", context, "--json"],
    )
    assert search.exit_code == 0, search.output
    search_payload = json.loads(search.stdout)["result"]
    assert search_payload["topic"] == "alpha"
    assert {hit["id"] for hit in search_payload["hits"]} == {
        "PRJ-alpha",
        "PRJ-common",
        "TASK-2026-001",
        "TASK-2026-002",
    }

    tasks = runner.invoke(
        app,
        [
            "task",
            "list",
            "--topic",
            "alpha-team",
            "--topic-only",
            "--context",
            context,
            "--json",
        ],
    )
    assert tasks.exit_code == 0, tasks.output
    task_payload = json.loads(tasks.stdout)["result"]
    assert [task["id"] for task in task_payload["tasks"]] == ["TASK-2026-002"]
    assert task_payload["tasks"][0]["topics"] == ["alpha"]

    daily = runner.invoke(
        app,
        ["brief", "--topic", "alpha-team", "--context", context, "--json"],
    )
    assert daily.exit_code == 0, daily.output
    focus_ids = {item["id"] for item in json.loads(daily.stdout)["result"]["today_focus"]}
    assert focus_ids == {"TASK-2026-001", "TASK-2026-002"}

    pack = runner.invoke(
        app,
        [
            "context-pack",
            "workctx://topic-filter/project/PRJ-common",
            "--topic",
            "alpha-team",
            "--context",
            context,
            "--json",
        ],
    )
    assert pack.exit_code == 0, pack.output
    pack_text = json.dumps(json.loads(pack.stdout)["result"]["pack"])
    assert "workctx://topic-filter/project/PRJ-alpha" in pack_text
    assert "workctx://topic-filter/project/PRJ-beta" not in pack_text


def test_topic_only_without_topic_is_user_correctable(tmp_path: Path) -> None:
    projection = _context(tmp_path / "context")

    result = runner.invoke(
        app,
        ["search", "Needle", "--topic-only", "--context", str(projection.context_root)],
    )

    assert result.exit_code == 1
    assert "--topic-only requires --topic" in result.stderr


def test_topic_cli_preview_apply_list_guard_alias_review_and_merge(tmp_path: Path) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Topic CLI", context_id="topic-cli")
    context = str(root)

    preview = runner.invoke(
        app,
        [
            "topic",
            "add",
            "Banorte",
            "--scope",
            "Fictional account.",
            "--context",
            context,
            "--json",
        ],
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["result"]["applied"] is False
    assert not (root / "99_meta" / "topics.yaml").exists()

    applied = runner.invoke(
        app,
        [
            "topic",
            "add",
            "Banorte",
            "--scope",
            "Fictional account.",
            "--yes",
            "--context",
            context,
            "--json",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["result"]["applied"] is True

    duplicate = runner.invoke(
        app,
        [
            "topic",
            "add",
            "Banorte Test 2",
            "--scope",
            "Forbidden test variant.",
            "--yes",
            "--context",
            context,
        ],
    )
    assert duplicate.exit_code == 1
    assert "Reuse the existing topic" in " ".join(duplicate.stderr.split())
    assert "--distinct-from banorte --reason" in duplicate.stderr

    source = runner.invoke(
        app,
        [
            "topic",
            "add",
            "Banorte Sandbox",
            "--scope",
            "Separate environment.",
            "--distinct-from",
            "banorte",
            "--reason",
            "Permanent separately owned environment.",
            "--yes",
            "--context",
            context,
        ],
    )
    assert source.exit_code == 0, source.output

    alias = runner.invoke(
        app,
        [
            "topic",
            "alias",
            "banorte-sandbox",
            "Sandbox Banorte",
            "--yes",
            "--context",
            context,
        ],
    )
    assert alias.exit_code == 0, alias.output

    review = runner.invoke(
        app,
        ["topic", "review", "--context", context, "--json"],
    )
    assert review.exit_code == 0, review.output
    assert json.loads(review.stdout)["result"]["count"] == 0

    merged = runner.invoke(
        app,
        [
            "topic",
            "merge",
            "banorte-sandbox",
            "banorte",
            "--yes",
            "--context",
            context,
            "--json",
        ],
    )
    assert merged.exit_code == 0, merged.output
    assert json.loads(merged.stdout)["result"]["applied"] is True

    listed = runner.invoke(
        app,
        ["topic", "list", "--all", "--context", context, "--json"],
    )
    assert listed.exit_code == 0, listed.output
    topics = json.loads(listed.stdout)["result"]["topics"]
    assert {topic["slug"]: topic["status"] for topic in topics} == {
        "banorte": "active",
        "banorte-sandbox": "merged",
    }
