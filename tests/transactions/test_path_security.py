from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from workspace.schema_support import validator_for

from .support import create_operation, entity_document, proposal

ROOT = Path(__file__).resolve().parents[2]
LEDGER_ALIASES = (
    "99_meta/AUDIT/LEDGER.JSONL",
    "99_meta/audit/ledger.jsonl.",
    "99_meta/audit/ledger.jsonl ",
    "99_meta/audit/ledger.jsonl::$DATA",
)


@pytest.mark.parametrize("ledger_alias", LEDGER_ALIASES)
@pytest.mark.parametrize("operation_kind", ("create", "update", "move_source", "move_destination"))
def test_every_operation_path_rejects_windows_ledger_aliases(
    ledger_alias: str,
    operation_kind: str,
) -> None:
    if operation_kind == "create":
        operation = {
            "op": "create",
            "target": ledger_alias,
            "payload": entity_document("PRJ-ledger-alias"),
        }
    elif operation_kind == "update":
        operation = {
            "op": "update",
            "target": ledger_alias,
            "payload": entity_document("PRJ-ledger-alias"),
            "expected_hash": f"sha256:{'a' * 64}",
        }
    elif operation_kind == "move_source":
        operation = {
            "op": "move",
            "source": ledger_alias,
            "destination": "99_meta/audit/ledger-backup.jsonl",
            "expected_hash": f"sha256:{'a' * 64}",
        }
    else:
        operation = {
            "op": "move",
            "source": "99_meta/audit/ledger-backup.jsonl",
            "destination": ledger_alias,
            "expected_hash": f"sha256:{'a' * 64}",
        }

    with pytest.raises(ValidationError):
        proposal("ledger-alias", [operation])


@pytest.mark.parametrize("ledger_alias", LEDGER_ALIASES)
def test_condition_paths_reject_windows_ledger_aliases(ledger_alias: str) -> None:
    with pytest.raises(ValidationError):
        proposal(
            "ledger-condition-alias",
            [create_operation("PRJ-condition")],
            preconditions=[{"kind": "path_exists", "path": ledger_alias}],
        )


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "02_knowledge/CON.md",
        "02_knowledge/NUL",
        "03_work/COM1.txt",
        "04_views/LPT9.json",
    ),
)
def test_windows_device_names_are_not_portable_context_paths(unsafe_path: str) -> None:
    with pytest.raises(ValidationError):
        proposal(
            "windows-device",
            [
                {
                    "op": "move",
                    "source": unsafe_path,
                    "destination": "01_processed/safe.txt",
                    "expected_hash": f"sha256:{'a' * 64}",
                }
            ],
        )


@pytest.mark.parametrize("ledger_alias", LEDGER_ALIASES)
def test_transaction_schema_rejects_ledger_aliases(ledger_alias: str) -> None:
    fixture = json.loads(
        (
            ROOT / "tests" / "workspace" / "fixtures" / "positive" / "transaction-proposal.json"
        ).read_text()
    )
    payload = deepcopy(fixture)
    payload["operations"][0]["target"] = ledger_alias

    assert not validator_for("transaction-proposal.schema.json").is_valid(payload)
