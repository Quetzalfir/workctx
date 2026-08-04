from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workctx.adapters.filesystem import CanonicalStore
from workctx.suggestions import (
    SuggestionApprovalRequiredError,
    SuggestionProposalError,
    SuggestionService,
    SuggestionStateError,
    SuggestionStatus,
    adopt_suggestion,
    create_suggestion,
    get_suggestion,
    list_suggestions,
    reject_suggestion,
)
from workctx.transactions import (
    PostconditionRollbackError,
    TransactionEngine,
    apply,
    read_audit_events,
    verify_ledger,
)
from workctx.validation import Severity, ValidationIssue, ValidationReport

from .support import (
    PROJECT_PATH,
    MutableClock,
    data_fix_proposal,
    initialize_suggestion_context,
    suggestion_payload,
)


def test_create_requires_approval_and_commits_one_canonical_record(tmp_path: Path) -> None:
    root = initialize_suggestion_context(tmp_path / "create")
    clock = MutableClock(datetime(2026, 8, 3, 11, tzinfo=UTC))
    payload = suggestion_payload(root)
    service = SuggestionService(root, clock=clock)

    with pytest.raises(SuggestionApprovalRequiredError):
        service.create(payload, approved=False)

    assert not (root / "03_work" / "suggestions").exists()
    assert verify_ledger(root).event_count == 0

    created = service.create(payload, approved=True)

    assert created.operation == "created"
    assert created.suggestion.record.status is SuggestionStatus.OPEN
    assert created.suggestion.path == ("03_work/suggestions/SUG-20260803-fix-project-title-01.md")
    assert created.receipt.applied_targets == (created.suggestion.path,)
    assert created.receipt.committed is True
    assert verify_ledger(root).event_count == 1
    assert get_suggestion(root, created.suggestion.record.uri) == created.suggestion
    assert list_suggestions(root) == (created.suggestion,)
    stored = (root / created.suggestion.path).read_text(encoding="utf-8")
    assert "proposal:" in stored
    assert "## Proposed outcome" in stored


def test_data_fix_adoption_is_one_apply_and_one_multi_target_ledger_event(
    tmp_path: Path,
) -> None:
    root = initialize_suggestion_context(tmp_path / "adopt")
    create_clock = MutableClock(datetime(2026, 8, 3, 11, tzinfo=UTC))
    created = SuggestionService(root, clock=create_clock).create(
        suggestion_payload(root),
        approved=True,
    )
    original_proposal_id = created.suggestion.record.proposal.id  # type: ignore[union-attr]
    original_project = CanonicalStore(root).read_entity(PROJECT_PATH)

    with pytest.raises(SuggestionApprovalRequiredError):
        adopt_suggestion(root, created.suggestion.record.id, approved=False)

    assert get_suggestion(root, created.suggestion.record.id).record.status is SuggestionStatus.OPEN
    assert CanonicalStore(root).read_entity(PROJECT_PATH) == original_project
    assert verify_ledger(root).event_count == 1

    calls = []

    def counted_apply(
        context_root: Path,
        proposal: object,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ):
        calls.append(proposal)
        return apply(
            context_root,
            proposal,  # type: ignore[arg-type]
            approved=approved,
            session_id=session_id,
        )

    adopt_clock = MutableClock(datetime(2026, 8, 3, 12, tzinfo=UTC))
    adopted = SuggestionService(
        root,
        clock=adopt_clock,
        transaction_apply=counted_apply,  # type: ignore[arg-type]
    ).adopt(created.suggestion.record.id, approved=True)

    assert len(calls) == 1
    assert adopted.suggestion.record.status is SuggestionStatus.ADOPTED
    assert adopted.suggestion.record.proposal is not None
    assert adopted.suggestion.record.proposal.id == original_proposal_id
    assert CanonicalStore(root).read_entity(PROJECT_PATH).frontmatter.title == (
        "Project Orion Review"
    )
    assert set(adopted.receipt.applied_targets) == {PROJECT_PATH, created.suggestion.path}
    verification = verify_ledger(root)
    assert verification.event_count == 2
    event = read_audit_events(root)[-1]
    assert event.result == "committed"
    assert {operation.target for operation in event.operations} == {
        PROJECT_PATH,
        created.suggestion.path,
    }


def test_data_fix_postcondition_rollback_restores_target_and_open_record(
    tmp_path: Path,
) -> None:
    root = initialize_suggestion_context(tmp_path / "rollback")
    created = SuggestionService(
        root,
        clock=lambda: datetime(2026, 8, 3, 11, tzinfo=UTC),
    ).create(suggestion_payload(root), approved=True)
    original_project = (root / PROJECT_PATH).read_bytes()

    def failing_workspace_validation(
        context_root: Path,
        *,
        strict: bool,
        freshness_probe: object,
    ) -> ValidationReport:
        return ValidationReport(
            context_root=context_root,
            context_id="fictional-suggestions",
            issues=[
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="CTX-POSTCONDITION-FAILURE",
                    message="Injected deterministic postcondition failure.",
                    path=PROJECT_PATH,
                )
            ],
        )

    def failing_apply(
        context_root: Path,
        proposal: object,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ):
        return TransactionEngine(
            context_root,
            workspace_validator=failing_workspace_validation,
        ).apply(
            proposal,  # type: ignore[arg-type]
            approved=approved,
            session_id=session_id,
        )

    service = SuggestionService(
        root,
        clock=lambda: datetime(2026, 8, 3, 12, tzinfo=UTC),
        transaction_apply=failing_apply,  # type: ignore[arg-type]
    )
    with pytest.raises(PostconditionRollbackError):
        service.adopt(created.suggestion.record.id, approved=True)

    assert (root / PROJECT_PATH).read_bytes() == original_project
    assert get_suggestion(root, created.suggestion.record.id).record.status is SuggestionStatus.OPEN
    events = read_audit_events(root)
    assert len(events) == 2
    assert events[-1].result == "rolled_back"
    assert {operation.target for operation in events[-1].operations} == {
        PROJECT_PATH,
        created.suggestion.path,
    }


def test_reject_requires_approval_preserves_record_and_blocks_later_adoption(
    tmp_path: Path,
) -> None:
    root = initialize_suggestion_context(tmp_path / "reject")
    suggestion_id = "SUG-20260803-review-skill-contract-01"
    created = SuggestionService(
        root,
        clock=lambda: datetime(2026, 8, 3, 11, tzinfo=UTC),
    ).create(
        suggestion_payload(
            root,
            suggestion_id=suggestion_id,
            suggestion_type="skill_override",
            rationale="Review the fictional skill contract",
            signal="A deterministic review found an ambiguous output field.",
            body="The override machinery is intentionally deferred.\n",
        ),
        approved=True,
    )

    with pytest.raises(SuggestionApprovalRequiredError):
        reject_suggestion(root, suggestion_id, approved=False)
    assert verify_ledger(root).event_count == 1

    rejected = reject_suggestion(root, suggestion_id, approved=True)

    assert rejected.suggestion.record.status is SuggestionStatus.REJECTED
    assert (root / created.suggestion.path).is_file()
    assert verify_ledger(root).event_count == 2
    with pytest.raises(SuggestionStateError):
        adopt_suggestion(root, suggestion_id, approved=True)


def test_supersession_is_atomic_reciprocal_and_preserves_both_bodies(tmp_path: Path) -> None:
    root = initialize_suggestion_context(tmp_path / "supersede")
    clock = MutableClock(datetime(2026, 8, 3, 11, tzinfo=UTC))
    service = SuggestionService(root, clock=clock)
    old_id = "SUG-20260803-retire-engine-hook-01"
    old_body = "Preserve this fictional first proposal.\n"
    old = service.create(
        suggestion_payload(
            root,
            suggestion_id=old_id,
            suggestion_type="engine_proposal",
            rationale="Retire the fictional engine hook",
            signal="The hook is not used by the fictional workflow.",
            body=old_body,
        ),
        approved=True,
    )

    clock.value += timedelta(hours=1)
    replacement_id = "SUG-20260803-revise-engine-hook-01"
    replacement = service.create(
        suggestion_payload(
            root,
            suggestion_id=replacement_id,
            suggestion_type="engine_proposal",
            rationale="Revise the fictional engine hook proposal",
            signal="New deterministic evidence narrows the affected interface.",
            supersedes=old_id,
            body="A narrower fictional proposal replaces the first one.\n",
        ),
        approved=True,
    )

    old_after = get_suggestion(root, old_id)
    new_after = get_suggestion(root, replacement_id)
    assert replacement.superseded_id == old_id
    assert old_after.record.status is SuggestionStatus.SUPERSEDED
    assert old_after.record.superseded_by == replacement_id
    assert old_after.body == old_body
    assert new_after.record.status is SuggestionStatus.OPEN
    assert new_after.record.supersedes == old_id
    assert tuple(item.record.id for item in list_suggestions(root)) == (
        old_id,
        replacement_id,
    )
    assert set(replacement.receipt.applied_targets) == {
        old.suggestion.path,
        replacement.suggestion.path,
    }
    assert verify_ledger(root).event_count == 2
    with pytest.raises(SuggestionStateError):
        reject_suggestion(root, old_id, approved=True)


def test_create_refuses_an_engine_invalid_embedded_proposal(tmp_path: Path) -> None:
    root = initialize_suggestion_context(tmp_path / "invalid-proposal")
    invalid = data_fix_proposal(root, expected_hash=f"sha256:{'0' * 64}")

    with pytest.raises(SuggestionProposalError):
        create_suggestion(
            root,
            suggestion_payload(root, proposal=invalid),
            approved=True,
        )

    assert list_suggestions(root) == ()
    assert verify_ledger(root).event_count == 0
