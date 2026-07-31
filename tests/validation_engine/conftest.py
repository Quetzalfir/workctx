from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from workctx.services.contexts import initialize_context

TIMESTAMP = "2026-07-30T12:00:00Z"
ARTIFACT_ID = "ART-20260730-fictional-note-01"
ARTIFACT_DIGEST = "a" * 64
EVIDENCE_ID = "EVD-20260730-fictional-note-01"
OBSERVATION_ID = f"{EVIDENCE_ID}#OBS-001"
PROJECT_ID = "PRJ-validation-lab"


@dataclass(slots=True)
class FixtureWorkspace:
    root: Path
    context_id: str = "validation-lab"

    def uri(self, entity_type: str, identifier: str) -> str:
        encoded_identifier = identifier.replace("#", "%23")
        return f"workctx://{self.context_id}/{entity_type}/{encoded_identifier}"

    def write_markdown(
        self,
        relative_directory: str,
        payload: dict[str, Any],
        *,
        filename: str | None = None,
        body: str = "# Fictional fixture\n",
    ) -> Path:
        identifier = payload.get("id")
        if filename is None:
            if not isinstance(identifier, str):
                raise TypeError("Fixture payload requires a string ID")
            filename = f"{identifier.replace('#', '%23')}.md"
        path = self.root / relative_directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = yaml.safe_dump(
            payload,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=4096,
        )
        path.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8", newline="\n")
        return path

    def write_json(
        self,
        relative_directory: str,
        payload: dict[str, Any],
        *,
        filename: str | None = None,
    ) -> Path:
        identifier = payload.get("id")
        if filename is None:
            if not isinstance(identifier, str):
                raise TypeError("Fixture payload requires a string ID")
            filename = f"{identifier}.json"
        path = self.root / relative_directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def entity_payload(
        self,
        identifier: str,
        entity_type: str,
        *,
        references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": identifier,
            "entity_type": entity_type,
            "title": f"Fictional {entity_type}",
            "uri": self.uri(entity_type, identifier),
            "aliases": [],
            "status": "active",
            "confidence": "high",
            "tags": ["fixture"],
            "references": references or [],
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
        }

    def task_payload(
        self,
        identifier: str,
        *,
        task_type: str = "parent",
        parent_task: str | None = None,
        root_task: str | None = None,
        dependencies: list[str] | None = None,
        blockers: list[str] | None = None,
        references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        parent_id = identifier.rsplit("-ST", maxsplit=1)[0]
        resolved_root = root_task or (parent_id if task_type == "subtask" else identifier)
        return {
            **self.entity_payload(identifier, "task", references=references),
            "status": "active",
            "task_type": task_type,
            "parent_task": parent_task,
            "root_task": resolved_root,
            "priority": "P1",
            "owner": None,
            "requester": None,
            "waiting_on": [],
            "due_at": None,
            "next_action": "Run the fictional validation scenario.",
            "dependencies": dependencies or [],
            "blockers": blockers or [],
            "source_observations": [],
        }

    def observation_payload(
        self,
        *,
        identifier: str = OBSERVATION_ID,
        locator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": identifier,
            "kind": "fact",
            "statement": "The fictional validation source is available.",
            "confidence": "high",
            "source": {
                "ref": f"artifact://sha256/{ARTIFACT_DIGEST}",
                "locator": locator or {"type": "line_range", "start_line": 1, "end_line": 1},
            },
            "derived_from": [],
            "related": [],
        }

    def claim_payload(
        self,
        identifier: str,
        *,
        predicate: str = "status",
        status: str = "current",
        valid_from: str | None = None,
        valid_to: str | None = None,
        supersedes: str | None = None,
        superseded_by: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": identifier,
            "subject": self.uri("project", PROJECT_ID),
            "predicate": predicate,
            "object": "active",
            "observed_at": TIMESTAMP,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "status": status,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
            "confidence": "high",
            "source_observations": [self.uri("observation", OBSERVATION_ID)],
        }


@pytest.fixture
def canonical_workspace(tmp_path: Path) -> FixtureWorkspace:
    root = tmp_path / "validation-lab"
    initialize_context(root, name="Validation Lab", context_id="validation-lab")
    fixture = FixtureWorkspace(root)

    processed = root / "01_processed" / "fictional-note.txt"
    processed.write_text("Fictional source.\n", encoding="utf-8", newline="\n")
    fixture.write_json(
        "00_inbox/manifests",
        {
            "schema_version": 1,
            "id": ARTIFACT_ID,
            "content_hash": f"sha256:{ARTIFACT_DIGEST}",
            "original_name": "fictional-note.txt",
            "media_type": "text/plain",
            "source_type": "note",
            "event_at_inferred": False,
            "ingested_at": TIMESTAMP,
            "participants": [],
            "status": "processed",
            "preserved_path": "01_processed/fictional-note.txt",
            "sidecars": [],
        },
    )
    fixture.write_markdown(
        "02_knowledge/projects",
        fixture.entity_payload(PROJECT_ID, "project"),
    )
    evidence = fixture.entity_payload(EVIDENCE_ID, "evidence")
    evidence["artifact_ref"] = f"artifact://sha256/{ARTIFACT_DIGEST}"
    evidence["observations"] = [fixture.observation_payload()]
    fixture.write_markdown("02_knowledge/evidence", evidence)
    return fixture
