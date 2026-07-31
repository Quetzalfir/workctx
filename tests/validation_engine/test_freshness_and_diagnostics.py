from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from workctx.validation import (
    CanonicalEdge,
    FreshnessResult,
    FreshnessState,
    NullFreshnessProbe,
    Severity,
    validate_workspace,
)
from workctx.validation.diagnostics import DIAGNOSTIC_DEFINITIONS

from .conftest import EVIDENCE_ID, OBSERVATION_ID, PROJECT_ID, FixtureWorkspace

ROOT = Path(__file__).parents[2]


@dataclass(slots=True)
class StaticProbe:
    state: FreshnessState
    observed_edges: tuple[CanonicalEdge, ...] = field(default_factory=tuple)

    def probe(
        self,
        root: Path,
        *,
        context_id: str,
        canonical_edges: Sequence[CanonicalEdge],
    ) -> FreshnessResult:
        del root, context_id
        self.observed_edges = tuple(canonical_edges)
        return FreshnessResult(self.state)


class FailingProbe:
    def probe(
        self,
        root: Path,
        *,
        context_id: str,
        canonical_edges: Sequence[CanonicalEdge],
    ) -> FreshnessResult:
        del root, context_id, canonical_edges
        raise RuntimeError("Fictional probe failure")


def test_strict_mode_escalates_warnings_to_errors(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "02_knowledge" / "notes" / "absolute-path.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("/fictional/machine/path\n", encoding="utf-8")

    normal = validate_workspace(canonical_workspace.root)
    strict = validate_workspace(canonical_workspace.root, strict=True)

    normal_issue = next(issue for issue in normal.issues if issue.code == "CTX-ABSOLUTE-PATH")
    strict_issue = next(issue for issue in strict.issues if issue.code == "CTX-ABSOLUTE-PATH")
    assert normal.ok
    assert normal_issue.severity is Severity.WARNING
    assert not strict.ok
    assert strict_issue.severity is Severity.ERROR
    assert strict.warnings == []


def test_non_utf8_file_is_a_warning(canonical_workspace: FixtureWorkspace) -> None:
    path = canonical_workspace.root / "02_knowledge" / "notes" / "non-utf8.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe")

    report = validate_workspace(canonical_workspace.root)
    issue = next(issue for issue in report.issues if issue.code == "CTX-NON-UTF8")

    assert issue.severity is Severity.WARNING
    assert report.ok


def test_null_probe_reports_unknown_advisory(canonical_workspace: FixtureWorkspace) -> None:
    report = validate_workspace(
        canonical_workspace.root,
        freshness_probe=NullFreshnessProbe(),
    )
    issue = next(issue for issue in report.issues if issue.code == "PROJECTION-FRESHNESS-UNKNOWN")

    assert issue.severity is Severity.ADVISORY
    assert report.ok


def test_stale_probe_warning_escalates_under_strict_mode(
    canonical_workspace: FixtureWorkspace,
) -> None:
    probe = StaticProbe(FreshnessState.STALE)

    report = validate_workspace(
        canonical_workspace.root,
        strict=True,
        freshness_probe=probe,
    )
    issue = next(issue for issue in report.issues if issue.code == "PROJECTION-STALE")

    assert issue.severity is Severity.ERROR
    assert not report.ok


def test_backlink_probe_receives_canonical_outbound_edges(
    canonical_workspace: FixtureWorkspace,
) -> None:
    evidence_uri = canonical_workspace.uri("evidence", "EVD-20260730-fictional-note-01")
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[{"relation": "related_to", "target": evidence_uri}],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)
    probe = StaticProbe(FreshnessState.BACKLINK_MISMATCH)

    report = validate_workspace(canonical_workspace.root, freshness_probe=probe)

    assert any(issue.code == "PROJECTION-BACKLINK-MISMATCH" for issue in report.issues)
    assert (
        CanonicalEdge(
            canonical_workspace.uri("project", PROJECT_ID),
            "related_to",
            evidence_uri,
        )
        in probe.observed_edges
    )


def test_embedded_observation_does_not_inherit_evidence_edges(
    canonical_workspace: FixtureWorkspace,
) -> None:
    project_uri = canonical_workspace.uri("project", PROJECT_ID)
    evidence = canonical_workspace.entity_payload(
        EVIDENCE_ID,
        "evidence",
        references=[{"relation": "related_to", "target": project_uri}],
    )
    evidence["artifact_ref"] = "artifact://sha256/" + "a" * 64
    evidence["observations"] = [canonical_workspace.observation_payload()]
    canonical_workspace.write_markdown("02_knowledge/evidence", evidence)
    probe = StaticProbe(FreshnessState.FRESH)

    validate_workspace(canonical_workspace.root, freshness_probe=probe)

    evidence_edge = CanonicalEdge(
        canonical_workspace.uri("evidence", EVIDENCE_ID),
        "related_to",
        project_uri,
    )
    fabricated_edge = CanonicalEdge(
        canonical_workspace.uri("observation", OBSERVATION_ID),
        "related_to",
        project_uri,
    )
    assert evidence_edge in probe.observed_edges
    assert fabricated_edge not in probe.observed_edges


def test_superseded_by_only_claim_produces_normalized_freshness_edge(
    canonical_workspace: FixtureWorkspace,
) -> None:
    old_id = "CLM-2026-00801"
    new_id = "CLM-2026-00802"
    old = canonical_workspace.claim_payload(
        old_id,
        status="superseded",
        superseded_by=new_id,
    )
    new = canonical_workspace.claim_payload(new_id)
    canonical_workspace.write_markdown("02_knowledge/claims", old)
    canonical_workspace.write_markdown("02_knowledge/claims", new)
    probe = StaticProbe(FreshnessState.FRESH)

    validate_workspace(canonical_workspace.root, freshness_probe=probe)

    assert (
        CanonicalEdge(
            canonical_workspace.uri("claim", new_id),
            "supersedes",
            canonical_workspace.uri("claim", old_id),
        )
        in probe.observed_edges
    )


def test_probe_failure_is_sanitized_warning(canonical_workspace: FixtureWorkspace) -> None:
    report = validate_workspace(canonical_workspace.root, freshness_probe=FailingProbe())
    issue = next(issue for issue in report.issues if issue.code == "PROJECTION-PROBE-FAILED")

    assert issue.severity is Severity.WARNING
    assert "Fictional probe failure" not in issue.message


def test_every_emitted_code_has_documented_cause_and_repair() -> None:
    text = (ROOT / "docs" / "reference" / "validation-diagnostics.md").read_text(encoding="utf-8")
    documented: dict[str, tuple[str, str, str]] = {}
    for line in text.splitlines():
        match = re.match(r"^\| `([A-Z][A-Z0-9-]+)` \|", line)
        if match is None:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        assert len(cells) >= 6
        documented[match.group(1)] = (cells[2], cells[3], cells[4])

    assert set(documented) == set(DIAGNOSTIC_DEFINITIONS)
    for code, definition in DIAGNOSTIC_DEFINITIONS.items():
        severity, cause, repair_action = documented[code]
        assert severity == definition.severity.value
        assert cause
        assert repair_action
        assert definition.cause
        assert definition.repair_action


def test_issue_repair_actions_are_reported_not_executed(
    canonical_workspace: FixtureWorkspace,
) -> None:
    missing = canonical_workspace.root / "03_work"
    renamed = canonical_workspace.root / "03_work_missing"
    missing.rename(renamed)

    report = validate_workspace(canonical_workspace.root)
    issue = next(issue for issue in report.issues if issue.code == "CTX-MISSING-DIRECTORY")

    assert issue.repair_action
    assert not missing.exists()
    assert renamed.exists()
