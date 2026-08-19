from __future__ import annotations

from pathlib import Path

import pytest

from workctx.transactions import PostconditionRollbackError, TransactionEngine

from .support import create_operation, initialize_transaction_context, proposal


def test_unrelated_legacy_strict_violation_does_not_poison_apply(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    legacy = root / "90_integrations" / "legacy-notes.md"
    legacy.write_text("C:\\legacy\\notes.txt\n", encoding="utf-8")
    transaction = proposal("legacy-unrelated", [create_operation("PRJ-clean")])

    receipt = TransactionEngine(root).apply(transaction, approved=True)

    assert receipt.committed is True
    assert (root / "02_knowledge" / "PRJ-clean.md").is_file()
    assert legacy.read_text(encoding="utf-8") == "C:\\legacy\\notes.txt\n"


def test_transaction_written_strict_violation_still_rolls_back(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    transaction = proposal(
        "touched-violation",
        [
            create_operation(
                "PRJ-violating",
                body="C:\\transaction\\written.txt\n",
            )
        ],
    )

    with pytest.raises(PostconditionRollbackError) as captured:
        TransactionEngine(root).apply(transaction, approved=True)

    diagnostics = [
        diagnostic
        for diagnostic in captured.value.result.diagnostics
        if diagnostic.code == "CTX-ABSOLUTE-PATH"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].severity == "error"
    assert diagnostics[0].path == "02_knowledge/PRJ-violating.md"
    assert not (root / "02_knowledge" / "PRJ-violating.md").exists()
