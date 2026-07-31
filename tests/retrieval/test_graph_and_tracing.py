from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain import RelationType
from workctx.domain.frontmatter import parse_frontmatter
from workctx.domain.locators import LineRangeLocator
from workctx.retrieval.graph import related
from workctx.retrieval.records import (
    EdgeDirection,
    MissingObservationReason,
    TraversalDirection,
)
from workctx.retrieval.tracing import trace
from workctx.services.contexts import initialize_context

ARTIFACT_REFERENCE = f"artifact://sha256/{'a' * 64}"
TIMESTAMP = "2026-07-30T12:00:00Z"


def test_related_supports_direction_depth_filtering_and_cycle_deduplication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    _create_base_context(root)
    uri_a = "workctx://fictional-context/system/SYS-alpha"
    uri_b = "workctx://fictional-context/system/SYS-beta"
    uri_c = "workctx://fictional-context/system/SYS-gamma"
    alpha = _entity_frontmatter(
        "fictional-context",
        "SYS-alpha",
        "system",
        "Alpha system",
    )
    alpha["references"] = [
        {"relation": "depends_on", "target": uri_b, "source_observations": []},
        {"relation": "mentions", "target": uri_c, "source_observations": []},
    ]
    beta = _entity_frontmatter(
        "fictional-context",
        "SYS-beta",
        "system",
        "Beta system",
    )
    beta["references"] = [{"relation": "blocks", "target": uri_c, "source_observations": []}]
    gamma = _entity_frontmatter(
        "fictional-context",
        "SYS-gamma",
        "system",
        "Gamma system",
    )
    gamma["references"] = [{"relation": "related_to", "target": uri_a, "source_observations": []}]
    _write_markdown(root / "02_knowledge" / "system-alpha.md", alpha, "Alpha.")
    _write_markdown(root / "02_knowledge" / "system-beta.md", beta, "Beta.")
    _write_markdown(root / "02_knowledge" / "system-gamma.md", gamma, "Gamma.")
    projection = SQLiteProjection(root)
    projection.rebuild()

    outbound = related(
        projection,
        uri_a,
        direction=TraversalDirection.OUTBOUND,
        depth=1,
    )
    inbound = related(
        projection,
        uri_a,
        direction=TraversalDirection.INBOUND,
        depth=1,
    )
    filtered = related(
        projection,
        uri_a,
        direction=TraversalDirection.BOTH,
        depth=2,
        relations=frozenset({RelationType.DEPENDS_ON}),
    )
    cyclic = related(
        projection,
        uri_a,
        direction=TraversalDirection.OUTBOUND,
        depth=2,
    )

    assert [node.reference for node in outbound.nodes] == [uri_b, uri_c]
    assert {edge.direction for edge in outbound.edges} == {EdgeDirection.OUTBOUND}
    assert [node.reference for node in inbound.nodes] == [uri_c]
    assert {edge.direction for edge in inbound.edges} == {EdgeDirection.INBOUND}
    assert [node.reference for node in filtered.nodes] == [uri_b]
    assert [edge.edge.relation for edge in filtered.edges] == [RelationType.DEPENDS_ON]
    assert [node.reference for node in cyclic.nodes] == [uri_b, uri_c]
    assert len({node.reference for node in cyclic.nodes}) == len(cyclic.nodes)
    assert {edge.edge.relation for edge in cyclic.edges} == {
        RelationType.DEPENDS_ON,
        RelationType.MENTIONS,
        RelationType.BLOCKS,
        RelationType.RELATED_TO,
    }
    assert [node.depth for node in cyclic.nodes] == [1, 1]


def test_trace_claim_and_task_to_exact_locator_with_history_and_missing_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    paths = _create_trace_context(root)
    task_uri = "workctx://fictional-context/task/TASK-2026-001"
    observation_uri = "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-001"
    missing_uri = "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-999"
    uncertain = _claim_frontmatter(
        "fictional-context",
        "CLM-2026-00003",
        task_uri,
        observation_uri,
        status="uncertain",
        object_value="pending-review",
        observed_at="2026-07-30T20:00:00Z",
    )
    _write_markdown(
        root / "02_knowledge" / "claim-status-uncertain.md",
        uncertain,
        "Fictional uncertain status.",
    )
    subtask, subtask_body = parse_frontmatter(paths["subtask"].read_text(encoding="utf-8"))
    subtask["source_observations"] = [observation_uri, missing_uri]
    _write_markdown(paths["subtask"], subtask, subtask_body.strip())
    projection = SQLiteProjection(root)
    projection.rebuild()

    current_trace = trace(projection, task_uri)
    historic_trace = trace(projection, task_uri, include_history=True)
    claim_trace = trace(
        projection,
        "workctx://fictional-context/claim/CLM-2026-00002",
    )
    direct_task_trace = trace(
        projection,
        "workctx://fictional-context/task/TASK-2026-001-ST01",
    )

    assert [claim.id for claim in current_trace.claims] == [
        "CLM-2026-00003",
        "CLM-2026-00002",
    ]
    assert [claim.id for claim in historic_trace.claims] == [
        "CLM-2026-00003",
        "CLM-2026-00002",
        "CLM-2026-00001",
    ]
    assert [claim.id for claim in claim_trace.claims] == [
        "CLM-2026-00003",
        "CLM-2026-00002",
    ]
    traced_observation = claim_trace.observations[0]
    assert str(traced_observation.source_ref) == ARTIFACT_REFERENCE
    assert isinstance(traced_observation.locator, LineRangeLocator)
    assert traced_observation.locator.start_line == 4
    assert traced_observation.locator.end_line == 7
    assert direct_task_trace.claims == ()
    assert [str(item.observation.uri) for item in direct_task_trace.observations] == [
        observation_uri
    ]
    assert direct_task_trace.missing_observations[0].reference == missing_uri
    assert direct_task_trace.missing_observations[0].reason is MissingObservationReason.NOT_FOUND
    assert direct_task_trace.missing_observations[0].referenced_by == (
        "workctx://fictional-context/task/TASK-2026-001-ST01",
    )


def test_trace_observation_and_generic_entity_use_exact_projected_observation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    _create_trace_context(root)
    projection = SQLiteProjection(root)
    projection.rebuild()
    observation_uri = "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-001"

    observation_trace = trace(projection, observation_uri)
    entity_trace = trace(
        projection,
        "workctx://fictional-context/system/SYS-identity-service",
    )

    assert [str(item.observation.uri) for item in observation_trace.observations] == [
        observation_uri
    ]
    assert [str(item.observation.uri) for item in entity_trace.observations] == [observation_uri]
    assert isinstance(entity_trace.observations[0].locator, LineRangeLocator)


def _create_base_context(root: Path) -> None:
    initialize_context(
        root,
        name="Fictional context",
        context_id="fictional-context",
    )


def _create_trace_context(root: Path) -> dict[str, Path]:
    _create_base_context(root)
    context_id = "fictional-context"
    evidence_id = "EVD-20260730-auth-flow-01"
    observation_id = f"{evidence_id}#OBS-001"
    observation_uri = f"workctx://{context_id}/observation/{evidence_id}%23OBS-001"
    system_uri = f"workctx://{context_id}/system/SYS-identity-service"
    task_uri = f"workctx://{context_id}/task/TASK-2026-001"
    paths = {
        "system": root / "02_knowledge" / "system-identity.md",
        "evidence": root / "02_knowledge" / "evidence-auth-flow.md",
        "task": root / "03_work" / "task-auth-review.md",
        "subtask": root / "03_work" / "task-auth-review-step.md",
        "claim_old": root / "02_knowledge" / "claim-status-old.md",
        "claim_current": root / "02_knowledge" / "claim-status-current.md",
    }

    _write_markdown(
        paths["system"],
        _entity_frontmatter(
            context_id,
            "SYS-identity-service",
            "system",
            "Identity Service",
        ),
        "Fictional identity service.",
    )
    evidence = _entity_frontmatter(
        context_id,
        evidence_id,
        "evidence",
        "Authentication flow review",
    )
    evidence["references"] = [
        {
            "relation": "mentions",
            "target": system_uri,
            "confidence": "high",
            "source_observations": [observation_uri],
        }
    ]
    evidence["observations"] = [
        {
            "id": observation_id,
            "kind": "fact",
            "statement": "The gateway delegates authentication to the identity service.",
            "confidence": "high",
            "source": {
                "ref": ARTIFACT_REFERENCE,
                "locator": {"type": "line_range", "start_line": 4, "end_line": 7},
            },
            "derived_from": [],
            "related": [
                {
                    "relation": "supports",
                    "target": system_uri,
                    "confidence": "high",
                    "source_observations": [observation_uri],
                }
            ],
        }
    ]
    _write_markdown(paths["evidence"], evidence, "Fictional authentication evidence.")

    task = _entity_frontmatter(
        context_id,
        "TASK-2026-001",
        "task",
        "Review authentication migration",
        status="waiting",
    )
    task.update(
        {
            "task_type": "parent",
            "parent_task": None,
            "root_task": "TASK-2026-001",
            "priority": "P1",
            "owner": None,
            "requester": None,
            "waiting_on": [],
            "due_at": None,
            "next_action": "Review the fictional evidence.",
            "dependencies": [],
            "blockers": [],
            "source_observations": [observation_uri],
        }
    )
    _write_markdown(paths["task"], task, "Fictional parent task.")

    subtask = _entity_frontmatter(
        context_id,
        "TASK-2026-001-ST01",
        "task",
        "Confirm gateway configuration",
    )
    subtask.update(
        {
            "task_type": "subtask",
            "parent_task": "TASK-2026-001",
            "root_task": "TASK-2026-001",
            "priority": "P2",
            "owner": None,
            "requester": None,
            "waiting_on": [],
            "due_at": None,
            "next_action": "Inspect the fictional snapshot.",
            "dependencies": [],
            "blockers": [],
            "source_observations": [observation_uri],
        }
    )
    _write_markdown(paths["subtask"], subtask, "Fictional subtask.")

    _write_markdown(
        paths["claim_old"],
        _claim_frontmatter(
            context_id,
            "CLM-2026-00001",
            task_uri,
            observation_uri,
            status="superseded",
            object_value="active",
            observed_at="2026-07-30T18:00:00Z",
            superseded_by="CLM-2026-00002",
        ),
        "Historic fictional state.",
    )
    _write_markdown(
        paths["claim_current"],
        _claim_frontmatter(
            context_id,
            "CLM-2026-00002",
            task_uri,
            observation_uri,
            status="current",
            object_value="waiting",
            observed_at="2026-07-30T19:00:00Z",
            supersedes="CLM-2026-00001",
        ),
        "Current fictional state.",
    )
    return paths


def _entity_frontmatter(
    context_id: str,
    entity_id: str,
    entity_type: str,
    title: str,
    *,
    status: str = "active",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": entity_id,
        "entity_type": entity_type,
        "title": title,
        "uri": f"workctx://{context_id}/{entity_type}/{entity_id}",
        "aliases": [],
        "status": status,
        "confidence": "high",
        "tags": ["fictional"],
        "references": [],
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
    }


def _claim_frontmatter(
    context_id: str,
    claim_id: str,
    subject_uri: str,
    observation_uri: str,
    *,
    status: str,
    object_value: object,
    observed_at: str,
    supersedes: str | None = None,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": claim_id,
        "subject": subject_uri,
        "predicate": "status",
        "object": object_value,
        "observed_at": observed_at,
        "valid_from": None,
        "valid_to": None,
        "status": status,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "confidence": "high",
        "source_observations": [observation_uri],
    }


def _write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    path.write_text(f"---\n{rendered}---\n\n{body}\n", encoding="utf-8", newline="\n")
