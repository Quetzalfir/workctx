from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import quote

import pytest

from workctx.adapters.sqlite import SQLiteProjection
from workctx.domain.transactions import TransactionProposal
from workctx.transactions import (
    DryRunResult,
    ProposalValidationResult,
    TransactionEngine,
)

from .support import (
    TIMESTAMP,
    content_hash,
    create_operation,
    entity_document,
    initialize_transaction_context,
    proposal,
)

CONTEXT_ID = "transaction-lab"
OTHER_CONTEXT_ID = "other-context"
ARTIFACT_ID = "ART-20260801-fictional-artifact-01"
ARTIFACT_BYTES = b"Fictional transaction traversal source.\n"
ARTIFACT_DIGEST = content_hash(ARTIFACT_BYTES).removeprefix("sha256:")
REPLACEMENT_ARTIFACT_DIGEST = content_hash(
    b"Fictional replacement transaction traversal source.\n"
).removeprefix("sha256:")
ARTIFACT_REF = f"artifact://sha256/{ARTIFACT_DIGEST}"
MISSING_ARTIFACT_REF = f"artifact://sha256/{'f' * 64}"
EVIDENCE_A = "EVD-20260801-fictional-evidence-01"
EVIDENCE_B = "EVD-20260801-fictional-evidence-02"
OBSERVATION_A = f"{EVIDENCE_A}#OBS-001"
OBSERVATION_B = f"{EVIDENCE_B}#OBS-001"

PreflightApi = Literal["validate", "dry_run"]
PreflightResult = ProposalValidationResult | DryRunResult


def _context(tmp_path: Path, *, with_artifact: bool = False) -> Path:
    root = initialize_transaction_context(
        tmp_path / "context",
        build_projection=not with_artifact,
    )
    if with_artifact:
        (root / "00_inbox" / "fictional-artifact.txt").write_bytes(ARTIFACT_BYTES)
        SQLiteProjection(root).rebuild()
    return root


def _preflight(
    root: Path,
    transaction: TransactionProposal,
    api: PreflightApi,
) -> PreflightResult:
    engine = TransactionEngine(root)
    if api == "validate":
        return engine.validate_proposal(transaction)
    return engine.dry_run(transaction)


def _assert_invalid_at(result: PreflightResult, path_fragment: str) -> None:
    assert result.valid is False
    paths = tuple(diagnostic.path or "" for diagnostic in result.diagnostics)
    assert any(path_fragment in path for path in paths), paths


def _slug(value: str) -> str:
    return value.replace("_", "-")


def _manifest_operation() -> dict[str, object]:
    return {
        "op": "create",
        "target": f"00_inbox/manifests/{ARTIFACT_ID}.json",
        "payload": {
            "kind": "artifact_manifest",
            "document": {
                "schema_version": 1,
                "id": ARTIFACT_ID,
                "content_hash": f"sha256:{ARTIFACT_DIGEST}",
                "original_name": "fictional-artifact.txt",
                "media_type": "text/plain",
                "source_type": "note",
                "source_origin": None,
                "event_at": None,
                "event_at_inferred": False,
                "ingested_at": TIMESTAMP,
                "language": "en",
                "participants": [],
                "classification": "internal",
                "status": "pending",
                "preserved_path": "00_inbox/fictional-artifact.txt",
                "sidecars": [],
                "duplicate_of": None,
                "notes": None,
            },
        },
    }


def _observation(
    identifier: str,
    *,
    source_ref: str = ARTIFACT_REF,
    derived_from: list[str] | None = None,
    related: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": identifier,
        "kind": "fact",
        "statement": f"Fictional observation {identifier}.",
        "confidence": "high",
        "source": {
            "ref": source_ref,
            "locator": {
                "type": "line_range",
                "start_line": 1,
                "end_line": 1,
            },
        },
        "derived_from": derived_from or [],
        "related": related or [],
    }


def _evidence_operation(
    identifier: str,
    *,
    artifact_ref: str = ARTIFACT_REF,
    observations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload = entity_document(identifier, "evidence")
    payload["document"]["artifact_ref"] = artifact_ref
    payload["document"]["observations"] = observations or []
    return {
        "op": "create",
        "target": f"02_knowledge/{identifier}.md",
        "payload": payload,
    }


def _standalone_observation_operation(
    observation: dict[str, object],
) -> dict[str, object]:
    identifier = observation["id"]
    assert isinstance(identifier, str)
    encoded = quote(identifier, safe="-._~")
    return {
        "op": "create",
        "target": f"02_knowledge/{encoded}.md",
        "payload": {
            "kind": "observation",
            "document": observation,
            "body": "Fictional standalone observation.\n",
        },
    }


def _observation_uri(identifier: str, *, context_id: str = CONTEXT_ID) -> str:
    return f"workctx://{context_id}/observation/{quote(identifier, safe='-._~')}"


def _task_uri(identifier: str, *, context_id: str = CONTEXT_ID) -> str:
    return f"workctx://{context_id}/task/{identifier}"


def _task_operation(
    identifier: str,
    *,
    dependencies: list[str] | None = None,
    blockers: list[str] | None = None,
    source_observations: list[str] | None = None,
) -> dict[str, object]:
    return {
        "op": "create",
        "target": f"03_work/tasks/{identifier}.md",
        "payload": {
            "kind": "task",
            "document": {
                "schema_version": 1,
                "id": identifier,
                "entity_type": "task",
                "title": f"Fictional {identifier}",
                "uri": _task_uri(identifier),
                "aliases": [],
                "status": "active",
                "confidence": "high",
                "tags": ["fictional"],
                "references": [],
                "created_at": TIMESTAMP,
                "updated_at": TIMESTAMP,
                "task_type": "parent",
                "parent_task": None,
                "root_task": identifier,
                "priority": "P1",
                "owner": None,
                "requester": None,
                "waiting_on": [],
                "due_at": None,
                "next_action": "Exercise transaction preflight traversal.",
                "dependencies": dependencies or [],
                "blockers": blockers or [],
                "source_observations": source_observations or [],
            },
            "body": "Fictional task traversal fixture.\n",
        },
    }


@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_evidence_artifact_ref_resolves_from_staged_manifest(
    tmp_path: Path,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path, with_artifact=True)
    transaction = proposal(
        _slug(f"evidence-artifact-staged-{api}"),
        [
            _evidence_operation(EVIDENCE_A),
            _manifest_operation(),
        ],
    )

    result = _preflight(root, transaction, api)

    assert result.valid is True
    assert not any(
        diagnostic.path and ".document.artifact_ref" in diagnostic.path
        for diagnostic in result.diagnostics
    )


@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_missing_evidence_artifact_ref_is_rejected_before_apply(
    tmp_path: Path,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path)
    transaction = proposal(
        _slug(f"evidence-artifact-missing-{api}"),
        [
            _evidence_operation(
                EVIDENCE_A,
                artifact_ref=MISSING_ARTIFACT_REF,
            )
        ],
    )

    result = _preflight(root, transaction, api)

    _assert_invalid_at(result, ".document.artifact_ref")


def test_evidence_artifact_ref_resolves_from_canonical_manifest(tmp_path: Path) -> None:
    root = _context(tmp_path, with_artifact=True)
    receipt = TransactionEngine(root).apply(
        proposal("canonical-artifact-seed", [_manifest_operation()]),
        approved=True,
    )
    transaction = proposal(
        "canonical-artifact-reference",
        [_evidence_operation(EVIDENCE_A)],
        base_revision=receipt.committed_revision,
    )

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is True


def test_updated_manifest_removes_old_digest_from_final_overlay(tmp_path: Path) -> None:
    root = _context(tmp_path, with_artifact=True)
    receipt = TransactionEngine(root).apply(
        proposal("artifact-overlay-seed", [_manifest_operation()]),
        approved=True,
    )
    manifest_path = root / "00_inbox" / "manifests" / f"{ARTIFACT_ID}.json"
    update_manifest = _manifest_operation()
    update_manifest["op"] = "update"
    update_manifest["expected_hash"] = content_hash(manifest_path.read_bytes())
    document = update_manifest["payload"]
    assert isinstance(document, dict)
    manifest_payload = document["document"]
    assert isinstance(manifest_payload, dict)
    manifest_payload["content_hash"] = f"sha256:{REPLACEMENT_ARTIFACT_DIGEST}"
    transaction = proposal(
        "artifact-final-overlay-removal",
        [
            _evidence_operation(EVIDENCE_A),
            update_manifest,
        ],
        base_revision=receipt.committed_revision,
    )

    result = TransactionEngine(root).dry_run(transaction)

    _assert_invalid_at(result, ".document.artifact_ref")


@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_missing_embedded_observation_source_artifact_is_rejected(
    tmp_path: Path,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path, with_artifact=True)
    transaction = proposal(
        _slug(f"observation-source-missing-{api}"),
        [
            _manifest_operation(),
            _evidence_operation(
                EVIDENCE_A,
                observations=[
                    _observation(
                        OBSERVATION_A,
                        source_ref=MISSING_ARTIFACT_REF,
                    )
                ],
            ),
        ],
    )

    result = _preflight(root, transaction, api)

    _assert_invalid_at(result, ".document.observations[0].source.ref")


def test_embedded_observation_references_resolve_staged_identities(tmp_path: Path) -> None:
    root = _context(tmp_path, with_artifact=True)
    observation_a_uri = _observation_uri(OBSERVATION_A)
    transaction = proposal(
        "embedded-observation-staged-references",
        [
            _manifest_operation(),
            _evidence_operation(
                EVIDENCE_A,
                observations=[_observation(OBSERVATION_A)],
            ),
            _evidence_operation(
                EVIDENCE_B,
                observations=[
                    _observation(
                        OBSERVATION_B,
                        derived_from=[observation_a_uri],
                        related=[
                            {
                                "relation": "derived_from",
                                "target": observation_a_uri,
                                "source_observations": [observation_a_uri],
                            }
                        ],
                    )
                ],
            ),
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is True


def test_embedded_observation_references_resolve_canonical_identity(tmp_path: Path) -> None:
    root = _context(tmp_path, with_artifact=True)
    receipt = TransactionEngine(root).apply(
        proposal(
            "embedded-observation-canonical-seed",
            [
                _manifest_operation(),
                _evidence_operation(
                    EVIDENCE_A,
                    observations=[_observation(OBSERVATION_A)],
                ),
            ],
        ),
        approved=True,
    )
    observation_a_uri = _observation_uri(OBSERVATION_A)
    transaction = proposal(
        "embedded-observation-canonical-reference",
        [
            _evidence_operation(
                EVIDENCE_B,
                observations=[
                    _observation(
                        OBSERVATION_B,
                        derived_from=[observation_a_uri],
                        related=[
                            {
                                "relation": "related_to",
                                "target": observation_a_uri,
                                "source_observations": [observation_a_uri],
                            }
                        ],
                    )
                ],
            )
        ],
        base_revision=receipt.committed_revision,
    )

    result = TransactionEngine(root).validate_proposal(transaction)

    assert result.valid is True


@pytest.mark.parametrize(
    ("carrier", "path_fragment"),
    [
        ("derived_from", ".document.observations[0].derived_from[0]"),
        ("related_target", ".document.observations[0].related[0].target"),
        (
            "related_source",
            ".document.observations[0].related[0].source_observations[0]",
        ),
    ],
)
@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_missing_embedded_observation_reference_is_rejected(
    tmp_path: Path,
    carrier: str,
    path_fragment: str,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path, with_artifact=True)
    staged_uri = _observation_uri(OBSERVATION_A)
    missing_uri = _observation_uri(f"{EVIDENCE_A}#OBS-999")
    derived_from = [missing_uri] if carrier == "derived_from" else []
    related: list[dict[str, object]] = []
    if carrier == "related_target":
        related = [{"relation": "related_to", "target": missing_uri}]
    elif carrier == "related_source":
        related = [
            {
                "relation": "related_to",
                "target": staged_uri,
                "source_observations": [missing_uri],
            }
        ]
    transaction = proposal(
        _slug(f"embedded-missing-{carrier}-{api}"),
        [
            _manifest_operation(),
            _evidence_operation(
                EVIDENCE_A,
                observations=[_observation(OBSERVATION_A)],
            ),
            _evidence_operation(
                EVIDENCE_B,
                observations=[
                    _observation(
                        OBSERVATION_B,
                        derived_from=derived_from,
                        related=related,
                    )
                ],
            ),
        ],
    )

    result = _preflight(root, transaction, api)

    _assert_invalid_at(result, path_fragment)


@pytest.mark.parametrize(
    ("carrier", "path_fragment"),
    [
        ("derived_from", ".document.observations[0].derived_from[0]"),
        ("related_target", ".document.observations[0].related[0].target"),
        (
            "related_source",
            ".document.observations[0].related[0].source_observations[0]",
        ),
    ],
)
def test_cross_context_embedded_observation_reference_is_rejected(
    tmp_path: Path,
    carrier: str,
    path_fragment: str,
) -> None:
    root = _context(tmp_path, with_artifact=True)
    local_uri = _observation_uri(OBSERVATION_A)
    foreign_uri = _observation_uri(OBSERVATION_A, context_id=OTHER_CONTEXT_ID)
    derived_from = [foreign_uri] if carrier == "derived_from" else []
    related: list[dict[str, object]] = []
    if carrier == "related_target":
        related = [{"relation": "related_to", "target": foreign_uri}]
    elif carrier == "related_source":
        related = [
            {
                "relation": "related_to",
                "target": local_uri,
                "source_observations": [foreign_uri],
            }
        ]
    transaction = proposal(
        _slug(f"embedded-cross-context-{carrier}"),
        [
            _manifest_operation(),
            _evidence_operation(
                EVIDENCE_A,
                observations=[_observation(OBSERVATION_A)],
            ),
            _evidence_operation(
                EVIDENCE_B,
                observations=[
                    _observation(
                        OBSERVATION_B,
                        derived_from=derived_from,
                        related=related,
                    )
                ],
            ),
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    _assert_invalid_at(result, path_fragment)


@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_duplicate_embedded_observation_identity_is_rejected_before_apply(
    tmp_path: Path,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path, with_artifact=True)
    observation = _observation(OBSERVATION_A)
    transaction = proposal(
        _slug(f"duplicate-embedded-observation-{api}"),
        [
            _manifest_operation(),
            _evidence_operation(
                EVIDENCE_A,
                observations=[observation, observation.copy()],
            ),
        ],
    )

    result = _preflight(root, transaction, api)

    _assert_invalid_at(result, ".document.observations[1].id")


def test_embedded_and_standalone_observation_identity_collision_is_rejected(
    tmp_path: Path,
) -> None:
    root = _context(tmp_path, with_artifact=True)
    observation = _observation(OBSERVATION_A)
    transaction = proposal(
        "embedded-standalone-observation-duplicate",
        [
            _manifest_operation(),
            _evidence_operation(EVIDENCE_A, observations=[observation]),
            _standalone_observation_operation(observation.copy()),
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    _assert_invalid_at(result, ".document.id")


def test_embedded_observation_identity_can_transfer_in_one_final_overlay(
    tmp_path: Path,
) -> None:
    root = _context(tmp_path, with_artifact=True)
    observation = _observation(OBSERVATION_A)
    seed = TransactionEngine(root).apply(
        proposal(
            "embedded-observation-transfer-seed",
            [
                _manifest_operation(),
                _evidence_operation(EVIDENCE_A, observations=[observation]),
            ],
        ),
        approved=True,
    )
    evidence_path = root / "02_knowledge" / f"{EVIDENCE_A}.md"
    update_evidence = _evidence_operation(EVIDENCE_A, observations=[])
    update_evidence["op"] = "update"
    update_evidence["expected_hash"] = content_hash(evidence_path.read_bytes())
    transaction = proposal(
        "embedded-observation-transfer",
        [
            _standalone_observation_operation(observation.copy()),
            update_evidence,
        ],
        base_revision=seed.committed_revision,
    )

    dry_run = TransactionEngine(root).dry_run(transaction)
    receipt = TransactionEngine(root).apply(transaction, approved=True)

    assert dry_run.valid is True
    assert receipt.committed is True
    assert evidence_path.is_file()
    assert (root / "02_knowledge" / f"{quote(OBSERVATION_A, safe='-._~')}.md").is_file()


def test_embedded_observation_must_belong_to_its_evidence_identity(tmp_path: Path) -> None:
    root = _context(tmp_path, with_artifact=True)
    transaction = proposal(
        "embedded-observation-owner-mismatch",
        [
            _manifest_operation(),
            _evidence_operation(
                EVIDENCE_B,
                observations=[_observation(OBSERVATION_A)],
            ),
        ],
    )

    result = TransactionEngine(root).validate_proposal(transaction)

    _assert_invalid_at(result, ".document.observations[0].id")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("dependencies", "TASK-2026-999"),
        ("blockers", "TASK-2026-999"),
        ("blockers", "workctx://transaction-lab/task/TASK-2026-999"),
    ],
)
@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_missing_task_relation_is_rejected_for_raw_ids_and_uris(
    tmp_path: Path,
    field_name: str,
    value: str,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path)
    dependencies = [value] if field_name == "dependencies" else []
    blockers = [value] if field_name == "blockers" else []
    transaction = proposal(
        _slug(f"missing-task-{field_name}-{api}"),
        [
            _task_operation(
                "TASK-2026-001",
                dependencies=dependencies,
                blockers=blockers,
            )
        ],
    )

    result = _preflight(root, transaction, api)

    _assert_invalid_at(result, f".document.{field_name}[0]")


def test_task_relations_resolve_raw_ids_and_uri_blocker_from_staged_tasks(
    tmp_path: Path,
) -> None:
    root = _context(tmp_path)
    transaction = proposal(
        "staged-task-relations",
        [
            _task_operation(
                "TASK-2026-001",
                dependencies=["TASK-2026-002"],
                blockers=[
                    "TASK-2026-003",
                    _task_uri("TASK-2026-004"),
                ],
            ),
            _task_operation("TASK-2026-002"),
            _task_operation("TASK-2026-003"),
            _task_operation("TASK-2026-004"),
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is True


def test_task_relations_resolve_raw_ids_and_uri_blocker_from_canonical_tasks(
    tmp_path: Path,
) -> None:
    root = _context(tmp_path)
    receipt = TransactionEngine(root).apply(
        proposal(
            "canonical-task-seed",
            [
                _task_operation("TASK-2026-002"),
                _task_operation("TASK-2026-003"),
                _task_operation("TASK-2026-004"),
            ],
        ),
        approved=True,
    )
    transaction = proposal(
        "canonical-task-relations",
        [
            _task_operation(
                "TASK-2026-001",
                dependencies=["TASK-2026-002"],
                blockers=[
                    "TASK-2026-003",
                    _task_uri("TASK-2026-004"),
                ],
            )
        ],
        base_revision=receipt.committed_revision,
    )

    result = TransactionEngine(root).validate_proposal(transaction)

    assert result.valid is True


def test_task_uri_blocker_must_resolve_to_a_task_identity(tmp_path: Path) -> None:
    root = _context(tmp_path)
    project_id = "PRJ-not-a-task"
    transaction = proposal(
        "task-blocker-wrong-type",
        [
            create_operation(project_id),
            _task_operation(
                "TASK-2026-001",
                blockers=[f"workctx://{CONTEXT_ID}/project/{project_id}"],
            ),
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    _assert_invalid_at(result, ".document.blockers[0]")


def test_cross_context_task_uri_blocker_is_rejected(tmp_path: Path) -> None:
    root = _context(tmp_path)
    transaction = proposal(
        "task-blocker-cross-context",
        [
            _task_operation(
                "TASK-2026-001",
                blockers=[_task_uri("TASK-2026-002", context_id=OTHER_CONTEXT_ID)],
            )
        ],
    )

    result = TransactionEngine(root).validate_proposal(transaction)

    _assert_invalid_at(result, ".document.blockers[0]")


@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_malformed_task_source_observation_is_rejected(
    tmp_path: Path,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path)
    transaction = proposal(
        _slug(f"task-source-observation-invalid-{api}"),
        [
            _task_operation(
                "TASK-2026-001",
                source_observations=["not-a-durable-uri"],
            )
        ],
    )

    result = _preflight(root, transaction, api)

    _assert_invalid_at(result, ".document.source_observations[0]")


@pytest.mark.parametrize("api", ("validate", "dry_run"))
def test_missing_durable_reference_in_markdown_body_is_rejected(
    tmp_path: Path,
    api: PreflightApi,
) -> None:
    root = _context(tmp_path)
    missing_uri = f"workctx://{CONTEXT_ID}/project/PRJ-missing-body-target"
    transaction = proposal(
        _slug(f"body-reference-missing-{api}"),
        [
            create_operation(
                "PRJ-body-source",
                body=f"See {missing_uri}.\n",
            )
        ],
    )

    result = _preflight(root, transaction, api)

    _assert_invalid_at(result, ".body")


def test_markdown_body_reference_resolves_staged_identity(tmp_path: Path) -> None:
    root = _context(tmp_path)
    target_uri = f"workctx://{CONTEXT_ID}/project/PRJ-body-target"
    transaction = proposal(
        "body-reference-staged",
        [
            create_operation(
                "PRJ-body-source",
                body=f"See {target_uri}.\n",
            ),
            create_operation("PRJ-body-target"),
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    assert result.valid is True


def test_markdown_body_reference_resolves_canonical_identity(tmp_path: Path) -> None:
    root = _context(tmp_path)
    receipt = TransactionEngine(root).apply(
        proposal("body-reference-seed", [create_operation("PRJ-body-target")]),
        approved=True,
    )
    target_uri = f"workctx://{CONTEXT_ID}/project/PRJ-body-target"
    transaction = proposal(
        "body-reference-canonical",
        [
            create_operation(
                "PRJ-body-source",
                body=f"See {target_uri}.\n",
            )
        ],
        base_revision=receipt.committed_revision,
    )

    result = TransactionEngine(root).validate_proposal(transaction)

    assert result.valid is True


@pytest.mark.parametrize(
    "body_reference",
    (
        "workctx:missing-slashes",
        "WORKCTX://transaction-lab/project/PRJ-body-target",
    ),
)
def test_malformed_durable_reference_in_markdown_body_is_rejected(
    tmp_path: Path,
    body_reference: str,
) -> None:
    root = _context(tmp_path)
    transaction = proposal(
        "body-reference-malformed",
        [
            create_operation(
                "PRJ-body-source",
                body=f"See {body_reference}.\n",
            )
        ],
    )

    result = TransactionEngine(root).dry_run(transaction)

    _assert_invalid_at(result, ".body")


def test_external_markdown_body_reference_remains_advisory_for_preflight(
    tmp_path: Path,
) -> None:
    """WP-220 cannot resolve external placeholders, but that advisory is not an apply veto."""

    root = _context(tmp_path)
    transaction = proposal(
        "body-reference-external-advisory",
        [
            create_operation(
                "PRJ-body-source",
                body="See jira://fictional-connection/DEMO-42.\n",
            )
        ],
    )

    result = TransactionEngine(root).validate_proposal(transaction)

    assert result.valid is True
    assert not any(
        diagnostic.severity == "error" and diagnostic.path and ".body" in diagnostic.path
        for diagnostic in result.diagnostics
    )
