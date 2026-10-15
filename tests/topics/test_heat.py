from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from workctx.adapters.sqlite import SQLiteProjection
from workctx.cli import app
from workctx.domain import (
    EntityFrontmatter,
    Topic,
    TopicHeatBucket,
    TopicRegistry,
    TopicStatus,
)
from workctx.domain.transactions import (
    AgentActor,
    CreateOperation,
    EntityDocumentPayload,
    TransactionProposal,
)
from workctx.services.contexts import initialize_context
from workctx.topics import (
    assign_heat_buckets,
    compute_topic_heat,
    decayed_activity_score,
)
from workctx.transactions import TransactionEngine, verify_ledger
from workctx.views import ViewName, ViewService, brief

from .test_filters_cli import NOW, _context, _entity, _task, _write_markdown

runner = CliRunner()


def test_decay_math_and_bucket_floors_are_deterministic() -> None:
    score = decayed_activity_score(
        (NOW, NOW - timedelta(days=7), NOW - timedelta(days=14)),
        evaluated_at=NOW,
    )

    assert score == pytest.approx(1.75)
    assert assign_heat_buckets({"only": 1.0}) == {"only": TopicHeatBucket.COOL}
    assert assign_heat_buckets({"cold": 0.0, "cool": 0.5, "warm": 1.5, "hot": 3.0}) == {
        "cold": TopicHeatBucket.COLD,
        "cool": TopicHeatBucket.COOL,
        "warm": TopicHeatBucket.WARM,
        "hot": TopicHeatBucket.HOT,
    }
    with pytest.raises(ValueError, match="timezone-aware"):
        decayed_activity_score((NOW,), evaluated_at=datetime(2026, 10, 14, 12))
    with pytest.raises(ValueError, match="finite"):
        assign_heat_buckets({"invalid": math.inf})


def test_configured_half_life_scores_committed_writes_with_an_injectable_clock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Heat Ledger", context_id="heat-ledger")
    registry = TopicRegistry(
        schema_version=1,
        topics=[
            Topic(
                slug="alpha",
                name="Alpha",
                scope="Fictional ledger activity.",
                aliases=[],
                status=TopicStatus.ACTIVE,
                created_at=NOW,
            )
        ],
    )
    (root / "99_meta" / "topics.yaml").write_text(
        yaml.safe_dump(registry.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    config_path = root / "context.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["topics"] = {"heat_half_life_days": 1}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    document = EntityFrontmatter(
        schema_version=1,
        id="PRJ-ledger-only",
        entity_type="project",
        title="Ledger-only heat fixture",
        uri="workctx://heat-ledger/project/PRJ-ledger-only",
        aliases=[],
        status="active",
        confidence="high",
        tags=[],
        topics=["alpha"],
        references=[],
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=30),
    )
    proposal = TransactionProposal(
        schema_version=1,
        id="TXP-20261014T120000Z-heat-ledger",
        context_id="heat-ledger",
        base_revision=verify_ledger(root).head_hash,
        actor=AgentActor(
            type="agent",
            id="heat-test",
            agent="pytest",
            model="deterministic",
        ),
        created_at=NOW,
        source_refs=[],
        operations=[
            CreateOperation(
                op="create",
                target="02_knowledge/PRJ-ledger-only.md",
                payload=EntityDocumentPayload(
                    kind="entity",
                    document=document,
                    body="Fictional committed activity.\n",
                ),
            )
        ],
        preconditions=[],
        postconditions=[],
        expected_views=["sqlite"],
        approval="required",
    )
    TransactionEngine(root, clock=lambda: NOW).apply(proposal, approved=True)

    evaluated_at = NOW + timedelta(days=1)
    first = compute_topic_heat(root, clock=lambda: evaluated_at)
    second = compute_topic_heat(root, clock=lambda: evaluated_at)

    assert first == second
    assert len(first) == 1
    assert first[0].slug == "alpha"
    assert first[0].score == 0.5
    assert first[0].heat is TopicHeatBucket.COOL
    assert first[0].last_activity == NOW


def test_heat_flows_to_list_view_and_brief_without_becoming_authored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path / "context").context_root
    evidence = _entity("EVD-alpha", "Alpha evidence", topics=["alpha"])
    evidence.update(
        {
            "entity_type": "evidence",
            "uri": "workctx://topic-filter/evidence/EVD-alpha",
            "created_at": (NOW - timedelta(days=1)).isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    task = _task("TASK-2026-004", "Additional alpha task", ["alpha"])
    task.update(
        {
            "created_at": (NOW - timedelta(days=1)).isoformat(),
            "updated_at": NOW.isoformat(),
        }
    )
    _write_markdown(
        root / "02_knowledge" / "EVD-alpha.md",
        evidence,
        "Fictional evidence body.",
    )
    _write_markdown(
        root / "03_work" / "TASK-2026-004.md",
        task,
        "Fictional task body.",
    )
    SQLiteProjection(root).rebuild()

    computed = compute_topic_heat(root, clock=lambda: NOW)
    assert [item.slug for item in computed] == ["alpha", "beta"]
    assert computed[0].heat is TopicHeatBucket.HOT
    assert computed[0].last_activity == NOW
    assert "heat:" not in (root / "99_meta" / "topics.yaml").read_text(encoding="utf-8")

    daily = brief(root, clock=lambda: NOW)
    assert [topic.slug for topic in daily.hot_topics] == ["alpha"]
    ViewService(root, clock=lambda: NOW).rebuild_view(ViewName.TOPICS)
    rendered = (root / "04_views" / "topics.md").read_text(encoding="utf-8")
    assert "# Topics" in rendered
    assert "`alpha` — Alpha | hot" in rendered

    monkeypatch.setattr("workctx.topics.heat._utc_now", lambda: NOW)
    heated = runner.invoke(app, ["topic", "heat", "--context", str(root), "--json"])
    assert heated.exit_code == 0, heated.output
    heat_payload = json.loads(heated.stdout)["result"]
    assert [item["slug"] for item in heat_payload["topics"]] == ["alpha", "beta"]
    assert heat_payload["topics"][0]["heat"] == "hot"

    listed = runner.invoke(app, ["topic", "list", "--context", str(root), "--json"])
    assert listed.exit_code == 0, listed.output
    list_payload = json.loads(listed.stdout)["result"]
    alpha = next(item for item in list_payload["topics"] if item["slug"] == "alpha")
    assert alpha["heat"] == "hot"
    assert alpha["score"] == computed[0].score
    assert alpha["last_activity"] == NOW.isoformat()
