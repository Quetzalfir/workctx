from __future__ import annotations

import ast
from pathlib import Path

import pytest

import workctx.drafting as drafting
from workctx.drafting import (
    DraftSecretError,
    get_draft,
    list_drafts,
    save_draft,
)
from workctx.transactions import audit_summary, verify_ledger

from .support import DRAFT_ID, UNCERTAINTY, draft_payload, initialize_drafting_context


def test_save_draft_uses_real_transaction_and_preserves_uncertainty(tmp_path: Path) -> None:
    root = initialize_drafting_context(tmp_path / "save-draft")
    payload = draft_payload()

    saved = save_draft(root, payload, approved=True)

    assert saved.operation == "created"
    assert saved.receipt.committed is True
    assert saved.receipt.applied_targets == (f"05_outbox/{DRAFT_ID}.md",)
    verification = verify_ledger(root)
    assert verification.valid is True
    assert verification.event_count == 1
    assert verification.last_event_id == saved.receipt.ledger_event_id
    assert audit_summary(root).last_proposal_id == saved.receipt.proposal_id

    stored = get_draft(root, DRAFT_ID)
    assert stored == saved.draft
    assert stored.delivery_state == "unsent"
    assert stored.body == payload.body
    assert UNCERTAINTY in stored.body
    assert list_drafts(root) == (stored,)


def test_save_draft_updates_in_place_and_listing_remains_stable(tmp_path: Path) -> None:
    root = initialize_drafting_context(tmp_path / "update-draft")
    created = save_draft(root, draft_payload(), approved=True)
    revised_body = "Hello Alex,\n\nThis revision changes only local draft text.\n"

    updated = save_draft(root, draft_payload(body=revised_body), approved=True)

    assert created.operation == "created"
    assert updated.operation == "updated"
    assert updated.draft.id == created.draft.id
    assert updated.draft.created_at == created.draft.created_at
    assert updated.draft.body == revised_body
    assert list_drafts(root) == (updated.draft,)
    assert verify_ledger(root).event_count == 2


def test_possible_secret_is_refused_without_outbox_or_ledger_mutation(tmp_path: Path) -> None:
    root = initialize_drafting_context(tmp_path / "secret-draft")
    secret = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    payload = draft_payload(body=f"api_key={secret}\n")

    with pytest.raises(DraftSecretError) as captured:
        save_draft(root, payload, approved=True)

    assert secret not in str(captured.value)
    assert list_drafts(root) == ()
    assert verify_ledger(root).event_count == 0


def test_only_delivery_module_imports_network_and_no_batch_send_api_exists() -> None:
    package = Path(drafting.__file__).parent
    forbidden = {
        "aiohttp",
        "http",
        "httpx",
        "requests",
        "socket",
        "smtplib",
        "urllib",
        "webbrowser",
    }
    network_importers: set[str] = set()
    for module_path in package.glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module.partition(".")[0])
        if not imported.isdisjoint(forbidden):
            network_importers.add(module_path.name)

    assert network_importers == {"delivery.py"}
    assert {"preview_send", "send"}.issubset(drafting.__all__)
    assert not any(name in drafting.__all__ for name in ("batch_send", "send_all", "schedule"))
