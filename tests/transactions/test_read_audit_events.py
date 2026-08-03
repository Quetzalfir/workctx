"""Coverage for the public verified ledger-event range reader."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workctx.transactions import apply, read_audit_events, verify_ledger

from .support import create_operation, initialize_transaction_context, proposal


def _apply_entity(root: Path, index: int) -> None:
    apply(
        root,
        proposal(
            f"events-{index:02}",
            [create_operation(f"SYS-events-{index:02}", "system")],
            base_revision=verify_ledger(root).head_hash,
        ),
        approved=True,
    )


def test_reader_returns_verified_chronological_events_with_inclusive_bounds(
    tmp_path: Path,
) -> None:
    root = initialize_transaction_context(tmp_path / "context")
    _apply_entity(root, 1)
    _apply_entity(root, 2)

    events = read_audit_events(root)
    assert [event.id for event in events] == [
        event.id for event in sorted(events, key=lambda item: item.timestamp)
    ]
    assert len(events) == 2
    assert events[-1].event_hash == verify_ledger(root).head_hash

    bounded = read_audit_events(
        root,
        since=events[0].timestamp,
        through=events[-1].timestamp,
    )
    assert bounded == events

    future = datetime(2099, 1, 1, tzinfo=UTC)
    assert read_audit_events(root, since=future) == ()
    assert read_audit_events(root, through=future - timedelta(days=36500)) == ()


def test_reader_rejects_naive_and_inverted_bounds(tmp_path: Path) -> None:
    root = initialize_transaction_context(tmp_path / "context")

    with pytest.raises(ValueError, match="timezone-aware"):
        read_audit_events(root, since=datetime(2026, 8, 3))
    with pytest.raises(ValueError, match="later than"):
        read_audit_events(
            root,
            since=datetime(2026, 8, 4, tzinfo=UTC),
            through=datetime(2026, 8, 3, tzinfo=UTC),
        )
