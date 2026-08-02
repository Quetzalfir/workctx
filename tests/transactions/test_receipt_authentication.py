"""Lead integration tests (D-035): receipt authentication for WP-310."""

from pathlib import Path

import pytest

from workctx.transactions import (
    LedgerIntegrityError,
    ReceiptAuthenticationError,
    authenticate_apply_result,
)
from workctx.transactions.engine import apply
from workctx.transactions.models import ApplyResult

from .support import (
    create_operation,
    initialize_transaction_context,
    proposal,
)


def _committed_result(root_dir: Path) -> tuple[Path, ApplyResult]:
    root = initialize_transaction_context(root_dir)
    transaction = proposal("receipt-auth", [create_operation("PRJ-receipt-auth")])
    result = apply(root, transaction, approved=True)
    assert isinstance(result, ApplyResult)
    return root, result


def test_genuine_receipt_authenticates_and_returns_the_event(tmp_path: Path) -> None:
    root, result = _committed_result(tmp_path / "ctx")
    event = authenticate_apply_result(root, result)
    assert event.id == result.ledger_event_id
    assert event.event_hash == result.ledger_event_hash
    assert event.proposal_id == result.proposal_id
    assert event.result == "committed"


@pytest.mark.parametrize(
    "mutation",
    [
        {"ledger_event_hash": "sha256:" + "0" * 64},
        {"proposal_id": "TXP-20990101T000000Z-forged"},
        {"ledger_source_refs": ("artifact://sha256/" + "0" * 64,)},
    ],
)
def test_forged_receipt_fields_are_rejected(tmp_path: Path, mutation: dict[str, object]) -> None:
    root, result = _committed_result(tmp_path / "ctx")
    forged = result.model_copy(update=mutation)
    with pytest.raises(ReceiptAuthenticationError):
        authenticate_apply_result(root, forged)


def test_unknown_event_id_is_rejected(tmp_path: Path) -> None:
    root, result = _committed_result(tmp_path / "ctx")
    forged = result.model_copy(update={"ledger_event_id": result.ledger_event_id[:-2] + "zz"})
    with pytest.raises(ReceiptAuthenticationError):
        authenticate_apply_result(root, forged)


def test_receipt_against_a_foreign_context_is_rejected(tmp_path: Path) -> None:
    _root, result = _committed_result(tmp_path / "ctx")
    other, _ = _committed_result(tmp_path / "other")
    with pytest.raises(ReceiptAuthenticationError):
        authenticate_apply_result(other, result)


def test_tampered_ledger_refuses_authentication(tmp_path: Path) -> None:
    root, result = _committed_result(tmp_path / "ctx")
    ledger = root / "99_meta" / "audit" / "ledger.jsonl"
    tampered = ledger.read_text(encoding="utf-8").replace('"committed"', '"rolled_back"', 1)
    ledger.write_text(tampered, encoding="utf-8", newline="\n")
    with pytest.raises((ReceiptAuthenticationError, LedgerIntegrityError, ValueError)):
        authenticate_apply_result(root, result)
