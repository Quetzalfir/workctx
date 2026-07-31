from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from workctx.domain.frontmatter import parse_frontmatter

TIMESTAMP = "2026-07-30T12:00:00Z"
ARTIFACT_REFERENCE = f"artifact://sha256/{'a' * 64}"


def create_fictional_context(
    root: Path,
    context_id: str,
    *,
    identity_title: str = "Identity Service",
    identity_alias: str = "IdP",
    context_timestamp: str = TIMESTAMP,
) -> dict[str, Path]:
    for relative in ("02_knowledge", "03_work", "98_state"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "02_knowledge" / "README.md").write_text("# Knowledge zone\n", encoding="utf-8")
    (root / "03_work" / "README.md").write_text("# Work zone\n", encoding="utf-8")
    _write_yaml(
        root / "context.yaml",
        {
            "schema_version": 1,
            "id": context_id,
            "name": f"Fictional {context_id}",
            "kind": "project",
            "profile": "hybrid",
            "languages": {"repository": "en", "user_interaction": "en"},
            "timezone": "UTC",
            "classification": "internal",
            "security_boundary": "isolated",
            "policies": {
                "local_mutations": "review_required",
                "external_writes": "approval_required",
                "raw_evidence_retention": "preserve",
                "federated_search": False,
            },
            "created_at": context_timestamp,
            "updated_at": context_timestamp,
        },
    )

    evidence_id = "EVD-20260730-auth-flow-01"
    observation_id = f"{evidence_id}#OBS-001"
    observation_uri = f"workctx://{context_id}/observation/{evidence_id}%23OBS-001"
    system_uri = f"workctx://{context_id}/system/SYS-identity-service"
    person_uri = f"workctx://{context_id}/person/PER-alex-rivera"
    task_uri = f"workctx://{context_id}/task/TASK-2026-001"

    paths = {
        "evidence": root / "02_knowledge" / "evidence-auth-flow.md",
        "system": root / "02_knowledge" / "system-identity.md",
        "person": root / "02_knowledge" / "person-alex.md",
        "claim_old": root / "02_knowledge" / "claim-status-old.md",
        "claim_current": root / "02_knowledge" / "claim-status-current.md",
        "task": root / "03_work" / "task-auth-review.md",
        "subtask": root / "03_work" / "task-auth-review-step.md",
    }
    write_markdown(
        paths["system"],
        entity_frontmatter(
            context_id,
            "SYS-identity-service",
            "system",
            identity_title,
            aliases=[identity_alias],
        ),
        "The central policy enforcement service supports Café-enabled login flows.",
    )
    write_markdown(
        paths["person"],
        entity_frontmatter(
            context_id,
            "PER-alex-rivera",
            "person",
            "Alex Rivera",
            aliases=["A. Rivera"],
        ),
        "Alex coordinates the fictional migration.",
    )
    evidence = entity_frontmatter(
        context_id,
        evidence_id,
        "evidence",
        "Authentication flow review",
        aliases=["Identity review"],
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
    write_markdown(
        paths["evidence"],
        evidence,
        "The authentication review documents delegation and rollout readiness.",
    )

    task = entity_frontmatter(
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
            "owner": person_uri,
            "requester": None,
            "waiting_on": [person_uri],
            "due_at": "2026-08-15T17:00:00Z",
            "next_action": "Collect the fictional rollout decision.",
            "dependencies": [],
            "blockers": ["Vendor test response"],
            "source_observations": [observation_uri],
            "references": [
                {
                    "relation": "owned_by",
                    "target": person_uri,
                    "confidence": "high",
                    "source_observations": [observation_uri],
                }
            ],
        }
    )
    write_markdown(
        paths["task"],
        task,
        "Rollout readiness depends on the documented authentication behavior.",
    )
    subtask = entity_frontmatter(
        context_id,
        "TASK-2026-001-ST01",
        "task",
        "Confirm gateway configuration",
        status="active",
    )
    subtask.update(
        {
            "task_type": "subtask",
            "parent_task": "TASK-2026-001",
            "root_task": "TASK-2026-001",
            "priority": "P2",
            "owner": person_uri,
            "requester": None,
            "waiting_on": [],
            "due_at": None,
            "next_action": "Inspect the fictional configuration snapshot.",
            "dependencies": [],
            "blockers": [],
            "source_observations": [observation_uri],
        }
    )
    write_markdown(paths["subtask"], subtask, "Configuration review details.")

    write_markdown(
        paths["claim_old"],
        claim_frontmatter(
            context_id,
            "CLM-2026-00001",
            task_uri,
            observation_uri,
            status="superseded",
            object_value="active",
            observed_at="2026-07-30T18:00:00+00:00",
            superseded_by="CLM-2026-00002",
        ),
        "Historic active status from the earlier review.",
    )
    write_markdown(
        paths["claim_current"],
        claim_frontmatter(
            context_id,
            "CLM-2026-00002",
            task_uri,
            observation_uri,
            status="current",
            object_value="waiting",
            observed_at="2026-07-30T12:00:00-07:00",
            supersedes="CLM-2026-00001",
        ),
        "Current waiting state supported by the fictional review.",
    )
    return paths


def entity_frontmatter(
    context_id: str,
    entity_id: str,
    entity_type: str,
    title: str,
    *,
    aliases: list[str] | None = None,
    status: str = "active",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": entity_id,
        "entity_type": entity_type,
        "title": title,
        "uri": f"workctx://{context_id}/{entity_type}/{entity_id}",
        "aliases": [] if aliases is None else aliases,
        "status": status,
        "confidence": "high",
        "tags": ["fictional"],
        "references": [],
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
    }


def claim_frontmatter(
    context_id: str,
    claim_id: str,
    subject_uri: str,
    observation_uri: str,
    *,
    status: str,
    object_value: object,
    observed_at: str = TIMESTAMP,
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


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    path.write_text(f"---\n{rendered}---\n\n{body}\n", encoding="utf-8", newline="\n")


def rewrite_entity(path: Path, *, title: str, aliases: list[str]) -> None:
    raw, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    raw["title"] = title
    raw["aliases"] = aliases
    write_markdown(path, raw, body.strip())


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
