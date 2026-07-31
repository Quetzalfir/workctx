from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from projections.support import (
    create_fictional_context,
    entity_frontmatter,
    write_markdown,
)

from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain.frontmatter import parse_frontmatter


def create_context_pack_projection(
    root: Path,
    *,
    include_secret: bool = False,
    long_focal_body: bool = False,
    observation_kind: str = "fact",
    observation_observed_at: str | None = None,
    distinct_historical_observation: bool = False,
) -> SQLiteProjection:
    paths = create_fictional_context(root, "fictional-context")
    observation_uri = "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-001"
    related_entities = (
        ("decision", "DEC-2026-001", "Adopt the staged fictional rollout"),
        ("risk", "RISK-2026-001", "Vendor readiness may delay the rollout"),
        ("question", "Q-2026-001", "When will the vendor test complete?"),
    )
    for entity_type, entity_id, title in related_entities:
        write_markdown(
            root / "02_knowledge" / f"{entity_type}-{entity_id.lower()}.md",
            entity_frontmatter(
                "fictional-context",
                entity_id,
                entity_type,
                title,
            ),
            f"Fictional {entity_type} context for retrieval tests.",
        )

    if (
        observation_kind != "fact"
        or observation_observed_at is not None
        or distinct_historical_observation
    ):
        evidence, evidence_body = parse_frontmatter(paths["evidence"].read_text(encoding="utf-8"))
        evidence["observations"][0]["kind"] = observation_kind
        if observation_observed_at is not None:
            evidence["observations"][0]["observed_at"] = observation_observed_at
        if distinct_historical_observation:
            historical_observation = deepcopy(evidence["observations"][0])
            historical_observation["id"] = "EVD-20260730-auth-flow-01#OBS-002"
            historical_observation["kind"] = "assumption"
            historical_observation["statement"] = (
                "The earlier rollout state was inferred from a fictional review."
            )
            historical_observation["observed_at"] = "2026-07-29T12:00:00Z"
            historical_observation["source"]["locator"] = {
                "type": "line_range",
                "start_line": 8,
                "end_line": 9,
            }
            evidence["observations"].append(historical_observation)
        write_markdown(paths["evidence"], evidence, evidence_body.strip())
        if distinct_historical_observation:
            historical_uri = (
                "workctx://fictional-context/observation/EVD-20260730-auth-flow-01%23OBS-002"
            )
            historical_claim, historical_claim_body = parse_frontmatter(
                paths["claim_old"].read_text(encoding="utf-8")
            )
            historical_claim["source_observations"] = [historical_uri]
            write_markdown(
                paths["claim_old"],
                historical_claim,
                historical_claim_body.strip(),
            )

    task, task_body = parse_frontmatter(paths["task"].read_text(encoding="utf-8"))
    task["dependencies"] = [
        "workctx://fictional-context/task/TASK-2026-001-ST01",
    ]
    task["references"].extend(
        [
            _reference("depends_on", "task", "TASK-2026-001-ST01", observation_uri),
            _reference("affects", "decision", "DEC-2026-001", observation_uri),
            _reference("blocks", "risk", "RISK-2026-001", observation_uri),
            _reference("waiting_on", "question", "Q-2026-001", observation_uri),
            _reference("mentions", "system", "SYS-identity-service", observation_uri),
            _reference("contradicts", "claim", "CLM-2026-00001", observation_uri),
        ]
    )
    body = "Focal detail " * 500 if long_focal_body else task_body.strip()
    write_markdown(paths["task"], task, body)

    if include_secret:
        claim, claim_body = parse_frontmatter(paths["claim_current"].read_text(encoding="utf-8"))
        claim["predicate"] = "api_key"
        claim["object"] = "fictional-secret-value-12345"
        write_markdown(paths["claim_current"], claim, claim_body.strip())

    projection = SQLiteProjection(root)
    projection.rebuild()
    return projection


def _reference(
    relation: str,
    entity_type: str,
    entity_id: str,
    observation_uri: str,
) -> dict[str, object]:
    return {
        "relation": relation,
        "target": f"workctx://fictional-context/{entity_type}/{entity_id}",
        "confidence": "high",
        "source_observations": [observation_uri],
    }
