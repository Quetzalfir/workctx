from __future__ import annotations

from pathlib import Path

import pytest

from workctx.drafting import DraftService, get_draft
from workctx.evidence import (
    begin_processing,
    build_evidence_proposal,
    complete_processing,
    stage_observations,
)
from workctx.tasks import TaskEvidenceRequiredError, TaskService
from workctx.transactions import apply, verify_ledger
from workctx.views import ViewService

from .support import (
    CONTEXT_ID,
    DRAFT_ID,
    DRAFT_TIME,
    OBSERVATION_ID,
    OBSERVATION_URI,
    PERSON_URI,
    RAW_NOTE,
    TASK_ID,
    TASK_TIME,
    TASK_URI,
    UNCERTAIN_MARKER,
    VIEW_TIME,
    acceptance_draft_payload,
    acceptance_task,
    evidence_payload,
    human_actor,
    initialize_acceptance_context,
    invoke_envelope,
    write_raw_note,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.integration]


def test_decisive_alpha_cycle_is_traceable_rebuildable_and_audited(tmp_path: Path) -> None:
    root = initialize_acceptance_context(tmp_path / "alpha-cycle")
    raw = write_raw_note(root)

    added = invoke_envelope(
        [
            "inbox",
            "add",
            str(raw),
            "--source",
            "note",
            "--event-date",
            "2026-08-02",
            "--context",
            str(root),
            "--json",
        ],
        command="inbox.add",
    )
    outcome = added["result"]["outcomes"][0]
    assert outcome["outcome"] == "registered"
    artifact_id = outcome["artifact_id"]
    artifact_ref = outcome["reference"]

    packet = begin_processing(root, artifact_id)
    assert packet.artifact_ref == artifact_ref
    assert packet.content.content_hash == packet.manifest.content_hash
    assert RAW_NOTE.strip() not in packet.model_dump_json()

    staging = stage_observations(root, artifact_id, evidence_payload(artifact_ref))
    assert staging.observations[0].id == OBSERVATION_ID
    proposal = build_evidence_proposal(staging)
    assert proposal.approval == "required"
    assert proposal.source_refs == [artifact_ref]
    receipt = apply(root, proposal, approved=True, session_id="e2e-full-cycle-evidence")
    assert receipt.committed is True

    archived = complete_processing(root, artifact_id, receipt)
    assert archived.destination_path.startswith(f"01_processed/{artifact_id}")
    assert (root / archived.destination_path).read_text(encoding="utf-8") == RAW_NOTE
    assert not (root / archived.source_path).exists()
    assert archived.artifact.manifest.status == "processed"

    task_service = TaskService(root, actor=human_actor(), clock=lambda: TASK_TIME)
    unsupported = acceptance_task(source_observations=())
    events_before_refusal = verify_ledger(root).event_count
    with pytest.raises(TaskEvidenceRequiredError):
        task_service.create_task(unsupported, approved=True)
    assert verify_ledger(root).event_count == events_before_refusal
    assert not (root / "03_work" / "tasks" / f"{TASK_ID}.md").exists()

    created = task_service.create_task(
        acceptance_task(),
        body="Evidence-backed fictional readiness task.\n",
        approved=True,
        session_id="e2e-full-cycle-task",
    )
    assert created.receipt.committed is True
    assert created.task_id == TASK_ID

    rebuilt = ViewService(root, clock=lambda: VIEW_TIME).rebuild_views()
    assert rebuilt.context_id == CONTEXT_ID
    brief = (root / "04_views" / "brief.md").read_text(encoding="utf-8")
    waiting_on = (root / "04_views" / "waiting-on.md").read_text(encoding="utf-8")
    assert TASK_ID in brief
    assert "Ask Alex Rivera to confirm the fictional launch date." in brief
    assert TASK_ID in waiting_on
    assert "Alex Rivera" in waiting_on
    assert PERSON_URI in waiting_on

    saved = DraftService(root, clock=lambda: DRAFT_TIME).save_draft(
        acceptance_draft_payload(),
        approved=True,
    )
    assert saved.operation == "created"
    assert saved.receipt.committed is True
    assert saved.receipt.applied_targets == (f"05_outbox/{DRAFT_ID}.md",)
    stored_draft = get_draft(root, DRAFT_ID)
    assert stored_draft.delivery_state == "unsent"
    assert stored_draft.source_refs == (OBSERVATION_URI,)
    assert UNCERTAIN_MARKER in stored_draft.body

    ledger = verify_ledger(root)
    assert ledger.valid is True
    assert ledger.context_id == CONTEXT_ID
    assert ledger.event_count == 5
    assert ledger.first_event_id is not None
    assert ledger.last_event_id == saved.receipt.ledger_event_id
    assert ledger.head_hash == saved.receipt.committed_revision

    shown = invoke_envelope(
        ["ref", "show", TASK_URI, "--context", str(root), "--json"],
        command="ref.show",
    )
    resolution = shown["result"]["resolution"]
    assert resolution["status"] == "resolved"
    assert resolution["record"] == {"id": TASK_ID, "uri": TASK_URI, "kind": "task"}

    traced = invoke_envelope(
        ["ref", "trace", TASK_URI, "--context", str(root), "--json"],
        command="ref.trace",
    )["result"]
    assert traced["missing_observations"] == []
    assert {item["id"] for item in traced["observations"]} == {OBSERVATION_ID}
    assert {item["source_ref"] for item in traced["observations"]} == {artifact_ref}
    assert {item["locator_type"] for item in traced["observations"]} == {"line_range"}
