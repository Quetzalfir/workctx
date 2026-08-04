from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from suggestions.support import MutableClock, initialize_suggestion_context, suggestion_payload

from workctx.suggestions import SuggestionService
from workctx.views import ViewName, ViewService


def test_suggestions_view_lists_only_open_records_with_deterministic_ages(
    tmp_path: Path,
) -> None:
    root = initialize_suggestion_context(tmp_path / "records-view")
    clock = MutableClock(datetime(2026, 7, 30, 12, tzinfo=UTC))
    suggestions = SuggestionService(root, clock=clock)
    old_id = "SUG-20260730-old-engine-proposal-01"
    suggestions.create(
        suggestion_payload(
            root,
            suggestion_id=old_id,
            suggestion_type="engine_proposal",
            rationale="Clarify the old fictional engine proposal",
            signal="A deterministic interface review found ambiguous terminology.",
        ),
        approved=True,
    )

    rejected_id = "SUG-20260801-rejected-skill-change-01"
    clock.value = datetime(2026, 8, 1, 12, tzinfo=UTC)
    suggestions.create(
        suggestion_payload(
            root,
            suggestion_id=rejected_id,
            suggestion_type="skill_override",
            rationale="Change the fictional skill output",
            signal="A review suggested a field that the operator declined.",
        ),
        approved=True,
    )
    suggestions.reject(rejected_id, approved=True)

    recent_id = "SUG-20260802-recent-engine-proposal-01"
    clock.value = datetime(2026, 8, 2, 12, tzinfo=UTC)
    suggestions.create(
        suggestion_payload(
            root,
            suggestion_id=recent_id,
            suggestion_type="engine_proposal",
            rationale="Narrow the recent fictional engine proposal",
            signal="New deterministic evidence reduced the affected surface.",
        ),
        approved=True,
    )

    generated_at = datetime(2026, 8, 3, 12, tzinfo=UTC)
    service = ViewService(root, clock=lambda: generated_at)
    first = service.rebuild_view(ViewName.SUGGESTIONS)
    first_bytes = (root / ViewName.SUGGESTIONS.relative_path).read_bytes()
    second = service.rebuild_view(ViewName.SUGGESTIONS)
    second_bytes = (root / ViewName.SUGGESTIONS.relative_path).read_bytes()

    assert first == second
    assert first_bytes == second_bytes
    rendered = first_bytes.decode()
    records = _section(rendered, "## Records", "## Stale claims")
    assert records.index(old_id) < records.index(recent_id)
    assert rejected_id not in records
    assert f"workctx://fictional-suggestions/investigation/{old_id}" in records
    assert "| engine_proposal | 4 | Clarify the old fictional engine proposal |" in records
    assert "| engine_proposal | 1 | Narrow the recent fictional engine proposal |" in records
    detection_sections = rendered[rendered.index("## Stale claims") :]
    assert old_id not in detection_sections
    assert recent_id not in detection_sections
    assert rendered.count("## Records") == 1
    assert all(
        heading in rendered
        for heading in (
            "## Stale claims",
            "## Broken evidence links",
            "## Inactive tasks",
            "## Orphaned knowledge",
            "## Old waiting-on entries",
        )
    )


def test_suggestions_view_renders_an_explicit_empty_records_row(tmp_path: Path) -> None:
    root = initialize_suggestion_context(tmp_path / "empty-records-view")

    ViewService(
        root,
        clock=lambda: datetime(2026, 8, 3, 12, tzinfo=UTC),
    ).rebuild_view(ViewName.SUGGESTIONS)

    rendered = (root / ViewName.SUGGESTIONS.relative_path).read_text(encoding="utf-8")
    records = _section(rendered, "## Records", "## Stale claims")
    assert "_No open suggestion records._" in records


def _section(content: str, heading: str, next_heading: str) -> str:
    start = content.index(heading)
    return content[start : content.index(next_heading, start + len(heading))]
