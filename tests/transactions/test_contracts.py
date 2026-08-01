from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry, Resource

from workctx.domain.transactions import (
    AuditEvent,
    AuditEventContent,
    TransactionProposal,
)

ROOT = Path(__file__).parents[2]
SCHEMA_ROOT = ROOT / "schemas"
FIXTURE_ROOT = ROOT / "tests" / "workspace" / "fixtures"
SCHEMA_NAMES = ("transaction-proposal.schema.json", "audit-event.schema.json")
CONTENT_HASH = f"sha256:{'a' * 64}"


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return payload


def _load_fixture(group: str, name: str) -> dict[str, object]:
    return _load_json(FIXTURE_ROOT / group / f"{name}.json")


def _validator(schema_name: str) -> Draft202012Validator:
    schemas = {path.name: _load_json(path) for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))}
    registry = Registry().with_resources(
        (str(schema["$id"]), Resource.from_contents(schema)) for schema in schemas.values()
    )
    return Draft202012Validator(
        schemas[schema_name],
        registry=registry,
        format_checker=FormatChecker(),
    )


def _assert_proposal_rejected_by_schema_and_model(payload: dict[str, object]) -> None:
    with pytest.raises(JsonSchemaValidationError):
        _validator("transaction-proposal.schema.json").validate(payload)
    with pytest.raises(PydanticValidationError):
        TransactionProposal.model_validate(payload)


def _assert_audit_rejected_by_schema_and_model(payload: dict[str, object]) -> None:
    with pytest.raises(JsonSchemaValidationError):
        _validator("audit-event.schema.json").validate(payload)
    with pytest.raises(PydanticValidationError):
        AuditEvent.model_validate(payload)


def _reseal_audit_payload(payload: dict[str, object]) -> None:
    unsigned = deepcopy(payload)
    unsigned["event_hash"] = ""
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["event_hash"] = hashlib.sha256(canonical).hexdigest()


def _second_task_payload(payload: dict[str, object]) -> dict[str, object]:
    cloned = deepcopy(payload)
    document = cloned["document"]
    if not isinstance(document, dict):
        raise TypeError("Expected a typed document payload")
    document.update(
        {
            "id": "TASK-2026-002",
            "title": "Validate a second fictional workspace operation",
            "uri": "workctx://fictional-context/task/TASK-2026-002",
            "root_task": "TASK-2026-002",
        }
    )
    return cloned


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_transaction_contract_schemas_are_valid_draft_2020_12(schema_name: str) -> None:
    Draft202012Validator.check_schema(_load_json(SCHEMA_ROOT / schema_name))


def test_positive_transaction_proposal_round_trips_through_schema_and_model() -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    validator = _validator("transaction-proposal.schema.json")
    validator.validate(payload)

    proposal = TransactionProposal.model_validate(payload)
    dumped = proposal.model_dump(mode="json")

    validator.validate(dumped)
    assert TransactionProposal.model_validate_json(proposal.model_dump_json()) == proposal


def test_empty_operations_fixture_is_rejected_by_schema_and_model() -> None:
    payload = _load_fixture("negative", "transaction-proposal-operations")
    _assert_proposal_rejected_by_schema_and_model(payload)


def test_proposal_supports_every_operation_and_condition_variant() -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    operations = payload["operations"]
    if not isinstance(operations, list) or not isinstance(operations[0], dict):
        raise TypeError("Expected a positive transaction operation fixture")
    create_payload = operations[0]["payload"]
    if not isinstance(create_payload, dict):
        raise TypeError("Expected a typed create payload")

    payload["operations"] = [
        operations[0],
        {
            "op": "update",
            "target": "03_work/tasks/TASK-2026-002.md",
            "payload": _second_task_payload(create_payload),
            "expected_hash": CONTENT_HASH,
        },
        {
            "op": "move",
            "source": "00_inbox/raw/fixture.txt",
            "destination": "01_processed/fixture.txt",
            "expected_hash": CONTENT_HASH,
        },
        {
            "op": "delete_generated",
            "target": "04_views/summary.md",
            "expected_hash": CONTENT_HASH,
        },
    ]
    payload["preconditions"] = [
        {"kind": "path_exists", "path": "00_inbox/raw/fixture.txt"},
        {"kind": "path_absent", "path": "01_processed/fixture.txt"},
        {
            "kind": "path_hash",
            "path": "04_views/summary.md",
            "content_hash": CONTENT_HASH,
        },
        {
            "kind": "reference_exists",
            "reference": "workctx://fictional-context/task/TASK-2026-001",
        },
    ]

    _validator("transaction-proposal.schema.json").validate(payload)
    proposal = TransactionProposal.model_validate(payload)

    assert [operation.op for operation in proposal.operations] == [
        "create",
        "update",
        "move",
        "delete_generated",
    ]
    assert [condition.kind for condition in proposal.preconditions] == [
        "path_exists",
        "path_absent",
        "path_hash",
        "reference_exists",
    ]


@pytest.mark.parametrize(
    "actor",
    [
        {
            "type": "agent",
            "id": "fictional-agent-run",
            "agent": "workctx-worker",
            "model": "fictional-model",
        },
        {
            "type": "system",
            "id": "fictional-recovery",
            "agent": None,
            "model": None,
        },
    ],
)
def test_discriminated_actor_variants_are_schema_and_model_valid(
    actor: dict[str, object],
) -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    payload["actor"] = actor

    _validator("transaction-proposal.schema.json").validate(payload)
    assert TransactionProposal.model_validate(payload).actor.type == actor["type"]


def test_actor_metadata_is_structurally_tightened() -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    actor = payload["actor"]
    if not isinstance(actor, dict):
        raise TypeError("Expected an actor object")
    actor["agent"] = "human-must-not-name-an-agent"

    _assert_proposal_rejected_by_schema_and_model(payload)


@pytest.mark.parametrize(
    "reference",
    (
        "file://fictional-host/private.txt",
        "artifact://sha256/not-a-digest",
    ),
)
def test_proposal_durable_references_are_rejected_by_schema_and_model(
    reference: str,
) -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    payload["source_refs"] = [reference]

    _assert_proposal_rejected_by_schema_and_model(payload)


@pytest.mark.parametrize(
    "path",
    (
        "02_knowledge/CON.md",
        "02_knowledge/control\tcharacter.md",
    ),
)
def test_proposal_unsafe_context_paths_are_rejected_by_schema_and_model(
    path: str,
) -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    payload["preconditions"] = [{"kind": "path_exists", "path": path}]

    _assert_proposal_rejected_by_schema_and_model(payload)


@pytest.mark.parametrize(
    "expected_views",
    [None, ["sqlite", "sqlite"]],
)
def test_expected_views_is_required_and_unique(expected_views: object) -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    if expected_views is None:
        del payload["expected_views"]
    else:
        payload["expected_views"] = expected_views

    _assert_proposal_rejected_by_schema_and_model(payload)


@pytest.mark.parametrize(
    ("field", "value", "error_match"),
    [
        ("created_at", "2026-07-30T12:00:01Z", "timestamp"),
        ("context_id", "different-context", "context"),
    ],
)
def test_proposal_producer_invariants_are_model_only(
    field: str,
    value: object,
    error_match: str,
) -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    payload[field] = value

    _validator("transaction-proposal.schema.json").validate(payload)
    with pytest.raises(PydanticValidationError, match=error_match):
        TransactionProposal.model_validate(payload)


def test_move_path_equality_is_a_model_only_producer_invariant() -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    payload["operations"] = [
        {
            "op": "move",
            "source": "00_inbox/raw/fixture.txt",
            "destination": "00_inbox/raw/fixture.txt",
            "expected_hash": CONTENT_HASH,
        }
    ]

    _validator("transaction-proposal.schema.json").validate(payload)
    with pytest.raises(PydanticValidationError, match="distinct"):
        TransactionProposal.model_validate(payload)


def test_narrative_document_suffix_case_is_rejected_by_schema_and_model() -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    operations = payload["operations"]
    if not isinstance(operations, list) or not isinstance(operations[0], dict):
        raise TypeError("Expected a typed proposal operation")
    operations[0]["target"] = "03_work/tasks/TASK-2026-001.MD"

    _assert_proposal_rejected_by_schema_and_model(payload)


def test_manifest_document_suffix_case_is_rejected_by_schema_and_model() -> None:
    payload = _load_fixture("positive", "transaction-proposal")
    manifest = _load_fixture("positive", "artifact-manifest")
    payload["operations"] = [
        {
            "op": "create",
            "target": "00_inbox/manifests/ART-20260730-fictional-note-01.JSON",
            "payload": {
                "kind": "artifact_manifest",
                "document": manifest,
            },
        }
    ]

    _assert_proposal_rejected_by_schema_and_model(payload)


def test_positive_audit_event_round_trips_and_has_one_canonical_line() -> None:
    payload = _load_fixture("positive", "audit-event")
    validator = _validator("audit-event.schema.json")
    validator.validate(payload)

    event = AuditEvent.model_validate(payload)
    dumped = event.model_dump(mode="json")

    assert event.event_hash == event.expected_event_hash()
    assert event.canonical_line_bytes().endswith(b"\n")
    assert event.canonical_line_bytes().count(b"\n") == 1
    validator.validate(dumped)
    assert AuditEvent.model_validate_json(event.model_dump_json()) == event


def test_apply_audit_event_variant_is_schema_and_model_valid() -> None:
    payload = _load_fixture("positive", "audit-event")
    payload.update(
        {
            "actor": {
                "type": "human",
                "id": "fictional-operator",
                "agent": None,
                "model": None,
            },
            "action": "apply",
            "result": "committed",
            "source_refs": [
                "workctx://fictional-context/task/TASK-2026-001",
            ],
        }
    )
    _reseal_audit_payload(payload)

    _validator("audit-event.schema.json").validate(payload)
    assert AuditEvent.model_validate(payload).action == "apply"


@pytest.mark.parametrize(
    "reference",
    (
        "file://fictional-host/private.txt",
        "artifact://sha256/not-a-digest",
    ),
)
def test_audit_durable_references_are_rejected_by_schema_and_model(
    reference: str,
) -> None:
    payload = _load_fixture("positive", "audit-event")
    payload.update(
        {
            "actor": {
                "type": "human",
                "id": "fictional-operator",
                "agent": None,
                "model": None,
            },
            "action": "apply",
            "result": "committed",
            "source_refs": [reference],
        }
    )
    _reseal_audit_payload(payload)

    _assert_audit_rejected_by_schema_and_model(payload)


@pytest.mark.parametrize(
    "path",
    (
        "02_knowledge/CON.md",
        "02_knowledge/control\tcharacter.md",
    ),
)
def test_audit_unsafe_context_paths_are_rejected_by_schema_and_model(path: str) -> None:
    payload = _load_fixture("positive", "audit-event")
    operations = payload["operations"]
    if not isinstance(operations, list) or not isinstance(operations[0], dict):
        raise TypeError("Expected a typed audit operation")
    operations[0]["target"] = path
    _reseal_audit_payload(payload)

    _assert_audit_rejected_by_schema_and_model(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("result", "committed"),
        (
            "actor",
            {
                "type": "human",
                "id": "fictional-operator",
                "agent": None,
                "model": None,
            },
        ),
        (
            "actor",
            {
                "type": "system",
                "id": "different-recovery-producer",
                "agent": None,
                "model": None,
            },
        ),
        (
            "source_refs",
            ["workctx://fictional-context/task/TASK-2026-001"],
        ),
    ],
)
def test_recovery_audit_provenance_is_structurally_tightened(
    field: str,
    value: object,
) -> None:
    payload = _load_fixture("positive", "audit-event")
    payload[field] = value
    _reseal_audit_payload(payload)

    _assert_audit_rejected_by_schema_and_model(payload)


def test_invalid_audit_hash_fixture_is_rejected_by_schema_and_model() -> None:
    payload = _load_fixture("negative", "audit-event-hash")
    _assert_audit_rejected_by_schema_and_model(payload)


def test_audit_event_supports_every_operation_variant() -> None:
    payload = _load_fixture("positive", "audit-event")
    payload.pop("event_hash")
    payload["operations"] = [
        {
            "op": "create",
            "target": "03_work/tasks/TASK-2026-001.md",
            "postimage_hash": CONTENT_HASH,
        },
        {
            "op": "update",
            "target": "03_work/tasks/TASK-2026-002.md",
            "preimage_hash": CONTENT_HASH,
            "postimage_hash": f"sha256:{'b' * 64}",
        },
        {
            "op": "move",
            "source": "00_inbox/raw/fixture.txt",
            "destination": "01_processed/fixture.txt",
            "content_hash": CONTENT_HASH,
        },
        {
            "op": "delete_generated",
            "target": "04_views/summary.md",
            "preimage_hash": CONTENT_HASH,
        },
    ]

    event = AuditEvent.seal(AuditEventContent.model_validate(payload))
    dumped = event.model_dump(mode="json")

    _validator("audit-event.schema.json").validate(dumped)
    assert [operation.op for operation in event.operations] == [
        "create",
        "update",
        "move",
        "delete_generated",
    ]


@pytest.mark.parametrize(
    ("field", "value", "error_match"),
    [
        ("id", "AUD-20260730T120000Z-different", "derive"),
        ("base_revision", "b" * 64, "prev_hash"),
        ("event_hash", "f" * 64, "event_hash"),
    ],
)
def test_audit_producer_invariants_are_model_only(
    field: str,
    value: object,
    error_match: str,
) -> None:
    payload = _load_fixture("positive", "audit-event")
    payload[field] = value

    _validator("audit-event.schema.json").validate(payload)
    with pytest.raises(PydanticValidationError, match=error_match):
        AuditEvent.model_validate(payload)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_schema_descriptions_disclose_producer_invariants(schema_name: str) -> None:
    schema = _load_json(SCHEMA_ROOT / schema_name)
    description = schema["description"]

    assert isinstance(description, str)
    assert "Producer invariants" in description
    assert "necessary but not sufficient" in description
