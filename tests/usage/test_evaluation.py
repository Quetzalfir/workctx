from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from workctx.domain.transactions import SystemActor
from workctx.suggestions import SuggestionPayload, SuggestionService, SuggestionType
from workctx.usage import UsageCandidateKind, evaluate_usage, record

from .support import (
    REPO_URI,
    initialize_usage_context,
    write_claim,
    write_task,
)


def test_promotion_triggers_at_exact_threshold_and_excludes_tier_one_uri(
    tmp_path: Path,
) -> None:
    root = initialize_usage_context(tmp_path / "promotion", enabled=True)
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    below = "repo://fictional.example@abcdef2/src/below.md#L1-L2"
    tier_one = "workctx://fictional-usage/system/SYS-existing"
    for index in range(5):
        record(root, "resolve", REPO_URI, now=now - timedelta(days=index))
        record(root, "resolve", tier_one, now=now - timedelta(days=index))
    for index in range(4):
        record(root, "resolve", below, now=now - timedelta(days=index))

    candidates = evaluate_usage(root, now=now)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.kind is UsageCandidateKind.PROMOTION
    assert candidate.target_uri == REPO_URI
    assert candidate.uses_30d == candidate.threshold_uses == 5


def test_decay_uses_exact_boundary_and_latest_usage_or_ledger_activity(tmp_path: Path) -> None:
    root = initialize_usage_context(tmp_path / "decay", enabled=True)
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    exact_task = write_task(root, "TASK-2026-010", updated_at=now - timedelta(days=60))
    recent_task = write_task(
        root,
        "TASK-2026-011",
        updated_at=now - timedelta(days=60) + timedelta(seconds=1),
    )
    used_task = write_task(root, "TASK-2026-012", updated_at=now - timedelta(days=100))
    ledger_task = write_task(root, "TASK-2026-013", updated_at=now - timedelta(days=100))
    record(root, "resolve", used_task, now=now - timedelta(days=59))

    service = SuggestionService(root, clock=lambda: now - timedelta(days=59))
    service.create(
        SuggestionPayload(
            type=SuggestionType.ENGINE_PROPOSAL,
            rationale="Review a fictional ledger touch",
            signal="A fictional transaction references the task.",
            source_refs=(ledger_task,),
            actor=SystemActor(
                type="system",
                id="fictional-ledger-touch",
                agent=None,
                model=None,
            ),
        ),
        approved=True,
    )

    exact_claim = write_claim(
        root,
        "CLM-2026-00001",
        observed_at=now - timedelta(days=60),
    )
    recent_claim = write_claim(
        root,
        "CLM-2026-00002",
        observed_at=now - timedelta(days=60) + timedelta(seconds=1),
    )

    decays = {
        candidate.target_uri: candidate
        for candidate in evaluate_usage(root, now=now)
        if candidate.kind is UsageCandidateKind.DECAY
    }

    assert set(decays) == {exact_task, exact_claim}
    assert decays[exact_task].inactive_days == decays[exact_task].threshold_days == 60
    assert decays[exact_claim].inactive_days == decays[exact_claim].threshold_days == 60
    assert recent_task not in decays
    assert used_task not in decays
    assert ledger_task not in decays
    assert recent_claim not in decays
