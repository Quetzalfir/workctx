from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workctx.services.contexts import initialize_context
from workctx.validation import Severity, validate_workspace

from .conftest import ARTIFACT_ID, PROJECT_ID, FixtureWorkspace


def _codes(workspace: FixtureWorkspace) -> list[str]:
    return [issue.code for issue in validate_workspace(workspace.root).issues]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_valid_canonical_workspace_is_clean(canonical_workspace: FixtureWorkspace) -> None:
    report = validate_workspace(canonical_workspace.root)

    assert report.ok
    assert report.context_id == canonical_workspace.context_id
    assert report.issues == []


def test_freshly_initialized_workspace_is_clean(tmp_path: Path) -> None:
    root = tmp_path / "fresh-context"
    initialize_context(root, name="Fresh Context", context_id="fresh-context")

    report = validate_workspace(root)

    assert report.ok
    assert report.issues == []


def test_validation_never_writes_workspace(canonical_workspace: FixtureWorkspace) -> None:
    before = _snapshot(canonical_workspace.root)

    validate_workspace(canonical_workspace.root)

    assert _snapshot(canonical_workspace.root) == before


def test_required_directory_names_are_case_sensitive_and_still_inspected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    canonical = canonical_workspace.root / "02_knowledge"
    intermediate = canonical_workspace.root / "02_knowledge_renaming"
    wrong_case = canonical_workspace.root / "02_Knowledge"
    canonical.rename(intermediate)
    intermediate.rename(wrong_case)
    invalid = wrong_case / "projects" / "PRJ-invalid.md"
    invalid.write_text("# Missing frontmatter\n", encoding="utf-8")

    report = validate_workspace(canonical_workspace.root)
    codes = [issue.code for issue in report.issues]

    assert codes.count("CTX-MISSING-DIRECTORY") == 1
    assert codes.count("DOC-PARSE") == 1


def test_unreadable_canonical_subtree_is_not_skipped(
    canonical_workspace: FixtureWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = canonical_workspace.root / "02_knowledge" / "blocked"
    blocked.mkdir()
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == blocked:
            raise PermissionError("fictional denied path")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    report = validate_workspace(canonical_workspace.root)
    issue = next(issue for issue in report.issues if issue.code == "CTX-UNREADABLE-PATH")

    assert issue.path == "02_knowledge/blocked"
    assert "fictional denied path" not in issue.message


def test_canonical_non_utf8_document_is_an_error(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "03_work" / "tasks" / "TASK-2026-999.md"
    path.write_bytes(b"\xff\xfe")

    report = validate_workspace(canonical_workspace.root)
    codes = [issue.code for issue in report.issues]

    assert codes.count("CTX-NON-UTF8") == 1
    assert codes.count("DOC-PARSE") == 1
    assert not report.ok


def test_invalid_typed_document_has_stable_code(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    del payload["title"]
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("DOC-MODEL") == 1


def test_entity_id_family_is_validated(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.entity_payload("not-a-person-id", "person")
    canonical_workspace.write_markdown("02_knowledge/people", payload)

    assert _codes(canonical_workspace).count("DOC-MODEL") == 1


def test_invalid_task_uses_task_model(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.task_payload("TASK-2026-920")
    payload["priority"] = "urgent"
    canonical_workspace.write_markdown("03_work/tasks", payload)

    assert _codes(canonical_workspace).count("DOC-MODEL") == 1


def test_invalid_claim_uses_claim_model(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.claim_payload("CLM-2026-00920")
    payload["predicate"] = ""
    canonical_workspace.write_markdown("02_knowledge/claims", payload)

    assert _codes(canonical_workspace).count("DOC-MODEL") == 1


def test_invalid_artifact_uses_manifest_model(canonical_workspace: FixtureWorkspace) -> None:
    manifest_path = canonical_workspace.root / "00_inbox" / "manifests" / f"{ARTIFACT_ID}.json"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["content_hash"] = "sha256:invalid"
    canonical_workspace.write_json("00_inbox/manifests", payload)

    assert _codes(canonical_workspace).count("DOC-MODEL") == 1


def test_invalid_context_uses_context_model(canonical_workspace: FixtureWorkspace) -> None:
    config_path = canonical_workspace.root / "context.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    del payload["security_boundary"]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    assert _codes(canonical_workspace).count("CTX-CONFIG") == 1


def test_malformed_frontmatter_has_stable_code(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "02_knowledge" / "claims" / "CLM-2026-00001.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nid: CLM-2026-00001\n", encoding="utf-8")

    assert _codes(canonical_workspace).count("DOC-PARSE") == 1


def test_recursive_yaml_anchor_is_reported_without_crashing(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "02_knowledge" / "projects" / "PRJ-recursive.md"
    path.write_text(
        """---
schema_version: 1
id: PRJ-recursive
entity_type: project
title: Recursive fictional fixture
uri: workctx://validation-lab/project/PRJ-recursive
aliases: []
status: active
confidence: high
tags: []
references: []
created_at: 2026-07-30T12:00:00Z
updated_at: 2026-07-30T12:00:00Z
recursive: &recursive
  self: *recursive
---
""",
        encoding="utf-8",
    )

    assert _codes(canonical_workspace).count("DOC-MODEL") == 1


def test_deep_json_is_reported_without_crashing(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "02_knowledge" / "claims" / "CLM-2026-00001.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[" * 1500 + "]" * 1500, encoding="utf-8")

    assert _codes(canonical_workspace).count("DOC-PARSE") == 1


def test_oversized_json_integer_is_reported_without_crashing(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "02_knowledge" / "claims" / "CLM-2026-00001.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"id":' + "9" * 5000 + "}", encoding="utf-8")

    assert _codes(canonical_workspace).count("DOC-PARSE") == 1


def test_canonical_markdown_without_frontmatter_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "02_knowledge" / "projects" / "PRJ-roadmap.md"
    path.write_text("# Fictional roadmap\n", encoding="utf-8")

    assert _codes(canonical_workspace).count("DOC-PARSE") == 1


def test_canonical_structured_candidates_cannot_disappear(
    canonical_workspace: FixtureWorkspace,
) -> None:
    claim = canonical_workspace.root / "02_knowledge" / "claims" / "CLM-2026-00001.yaml"
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text("{}\n", encoding="utf-8")
    task = canonical_workspace.root / "03_work" / "tasks" / "TASK-2026-999.json"
    task.write_text("[]\n", encoding="utf-8")
    project = canonical_workspace.root / "02_knowledge" / "projects" / "has-id.yaml"
    project.write_text("id: PRJ-incomplete\n", encoding="utf-8")

    codes = _codes(canonical_workspace)
    assert codes.count("DOC-MODEL") == 2
    assert codes.count("DOC-PARSE") == 1


def test_invalid_model_still_has_reference_diagnostics(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "related_to",
                "target": "workctx://validation-lab/widget/WID-001",
            }
        ],
    )
    del payload["title"]
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    codes = _codes(canonical_workspace)
    assert codes.count("DOC-MODEL") == 1
    assert codes.count("REF-UNKNOWN-ENTITY-TYPE") == 1


def test_entity_type_cannot_bypass_claim_model(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload("CLM-2026-00921", "claim")
    canonical_workspace.write_markdown("02_knowledge/claims", payload)

    codes = _codes(canonical_workspace)
    assert codes.count("DOC-MODEL") == 1
    assert "REF-UNRESOLVED" not in codes


@pytest.mark.parametrize(
    "extra_fields",
    [
        {"task_type": "migration"},
        {"content_hash": "descriptive hash"},
        {"subject": "topic", "predicate": "tracks"},
    ],
)
def test_explicit_entity_type_precedes_shape_heuristics(
    canonical_workspace: FixtureWorkspace,
    extra_fields: dict[str, str],
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    payload.update(extra_fields)
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert validate_workspace(canonical_workspace.root).ok


def test_manifest_location_always_uses_artifact_model(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload("ART-20260730-bypass-01", "artifact")
    canonical_workspace.write_json("00_inbox/manifests", payload)

    assert _codes(canonical_workspace).count("DOC-MODEL") == 1


def test_filename_must_match_frontmatter_id(
    canonical_workspace: FixtureWorkspace,
) -> None:
    source = canonical_workspace.root / "02_knowledge" / "projects" / f"{PROJECT_ID}.md"
    target = source.with_name("PRJ-wrong-name.md")
    source.rename(target)

    assert _codes(canonical_workspace).count("DOC-FILENAME-ID") == 1


def test_duplicate_canonical_identity_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    canonical_workspace.write_markdown(
        "02_knowledge/duplicate-projects",
        canonical_workspace.entity_payload(PROJECT_ID, "project"),
    )

    assert _codes(canonical_workspace).count("DOC-DUPLICATE-ID") == 1


def test_broken_workctx_uri_is_rejected(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[{"relation": "related_to", "target": "workctx://validation-lab/task"}],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_noncanonical_observation_uri_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "evidenced_by",
                "target": (
                    "workctx://validation-lab/observation/EVD-20260730-fictional-note-01#OBS-001"
                ),
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_foreign_context_uri_is_rejected(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "related_to",
                "target": "workctx://other-context/task/TASK-2026-999",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-CONTEXT-MISMATCH") == 1


def test_context_boundary_precedes_vocabulary_check(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "related_to",
                "target": "workctx://other-context/widget/WID-001",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    codes = _codes(canonical_workspace)
    assert codes.count("REF-CONTEXT-MISMATCH") == 1
    assert "REF-UNKNOWN-ENTITY-TYPE" not in codes


def test_unknown_entity_type_is_rejected(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "related_to",
                "target": "workctx://validation-lab/widget/WID-001",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-UNKNOWN-ENTITY-TYPE") == 1


def test_embedded_uri_in_frontmatter_scalar_is_checked(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    payload["title"] = "See workctx://validation-lab/widget/WID-001"
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-UNKNOWN-ENTITY-TYPE") == 1


def test_leading_uri_in_frontmatter_prose_is_tokenized(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    payload["title"] = "workctx://validation-lab/widget/WID-001 is referenced"
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    codes = _codes(canonical_workspace)
    assert codes.count("REF-UNKNOWN-ENTITY-TYPE") == 1
    assert "REF-INVALID-URI" not in codes


def test_reference_like_identifier_suffixes_in_prose_are_ignored(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    canonical_workspace.write_markdown(
        "02_knowledge/projects",
        payload,
        body="pre-workctx:value foo.workctx:value foo+repo:value myartifact:count\n",
    )

    assert validate_workspace(canonical_workspace.root).issues == []


def test_reference_scheme_labels_in_prose_are_ignored(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    canonical_workspace.write_markdown(
        "02_knowledge/projects",
        payload,
        body="Artifact: pending. Repo: pending. Workctx: pending.\n",
    )

    assert validate_workspace(canonical_workspace.root).issues == []


def test_unresolved_internal_reference_is_an_error(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "related_to",
                "target": "workctx://validation-lab/task/TASK-2026-999",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-UNRESOLVED") == 1


def test_missing_artifact_is_an_advisory(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "evidenced_by",
                "target": f"artifact://sha256/{'b' * 64}",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    report = validate_workspace(canonical_workspace.root)
    issues = [issue for issue in report.issues if issue.code == "REF-ARTIFACT-UNAVAILABLE"]

    assert report.ok
    assert len(issues) == 1
    assert issues[0].severity is Severity.ADVISORY
    assert issues[0] not in report.warnings


def test_evidence_artifact_ref_must_be_a_durable_uri(
    canonical_workspace: FixtureWorkspace,
) -> None:
    evidence_id = "EVD-20260730-fictional-note-01"
    evidence = canonical_workspace.entity_payload(evidence_id, "evidence")
    evidence["artifact_ref"] = "missing-artifact-reference"
    evidence["observations"] = [canonical_workspace.observation_payload()]
    canonical_workspace.write_markdown("02_knowledge/evidence", evidence)

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_observation_derived_from_must_be_a_durable_uri(
    canonical_workspace: FixtureWorkspace,
) -> None:
    evidence_id = "EVD-20260730-fictional-note-01"
    evidence = canonical_workspace.entity_payload(evidence_id, "evidence")
    evidence["artifact_ref"] = f"artifact://sha256/{'a' * 64}"
    observation = canonical_workspace.observation_payload()
    observation["derived_from"] = ["missing-observation-reference"]
    evidence["observations"] = [observation]
    canonical_workspace.write_markdown("02_knowledge/evidence", evidence)

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_external_reference_is_an_advisory(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "related_to",
                "target": "jira://fictional-connection/DEMO-42",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    report = validate_workspace(canonical_workspace.root)
    issues = [issue for issue in report.issues if issue.code == "REF-EXTERNAL-UNAVAILABLE"]

    assert report.ok
    assert len(issues) == 1
    assert issues[0].severity is Severity.ADVISORY


def test_external_reference_in_markdown_body_is_checked(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    canonical_workspace.write_markdown(
        "02_knowledge/projects",
        payload,
        body="See jira://fictional-connection/DEMO-42.\n",
    )

    assert _codes(canonical_workspace).count("REF-EXTERNAL-UNAVAILABLE") == 1


def test_uppercase_scheme_in_markdown_body_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    canonical_workspace.write_markdown(
        "02_knowledge/projects",
        payload,
        body="See JIRA://fictional-connection/DEMO-42.\n",
    )

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_malformed_known_scheme_in_markdown_body_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    canonical_workspace.write_markdown(
        "02_knowledge/projects",
        payload,
        body="Broken reference: workctx:missing-slashes.\n",
    )

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_repo_reference_requires_immutable_sha(canonical_workspace: FixtureWorkspace) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "evidenced_by",
                "target": "repo://fictional-repo@main/src/example.py#L1-L2",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-REPO-SHA") == 1


def test_repo_reference_path_must_be_portable(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "evidenced_by",
                "target": "repo://fictional@abcdef0/C%3Arelative#L1-L1",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_malformed_repo_netloc_does_not_crash(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(
        PROJECT_ID,
        "project",
        references=[
            {
                "relation": "evidenced_by",
                "target": "repo://[/path#L1-L1",
            }
        ],
    )
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("REF-INVALID-URI") == 1


def test_invalid_observation_locator_is_rejected(
    canonical_workspace: FixtureWorkspace,
) -> None:
    evidence_id = "EVD-20260730-fictional-note-01"
    evidence = canonical_workspace.entity_payload(evidence_id, "evidence")
    evidence["artifact_ref"] = f"artifact://sha256/{'a' * 64}"
    evidence["observations"] = [
        canonical_workspace.observation_payload(
            locator={"type": "line_range", "start_line": 9, "end_line": 2}
        )
    ]
    canonical_workspace.write_markdown("02_knowledge/evidence", evidence)

    assert _codes(canonical_workspace).count("OBS-INVALID") == 1


def test_json_pointer_locator_is_not_a_machine_path(
    canonical_workspace: FixtureWorkspace,
) -> None:
    evidence_id = "EVD-20260730-fictional-note-01"
    evidence = canonical_workspace.entity_payload(evidence_id, "evidence")
    evidence["artifact_ref"] = "artifact://sha256/" + "a" * 64
    evidence["observations"] = [
        canonical_workspace.observation_payload(
            locator={"type": "json_pointer", "pointer": "/items/0"}
        )
    ]
    canonical_workspace.write_markdown("02_knowledge/evidence", evidence)

    report = validate_workspace(canonical_workspace.root, strict=True)

    assert report.ok
    assert "CTX-ABSOLUTE-PATH" not in [issue.code for issue in report.issues]


def test_json_pointer_block_scalar_is_not_a_machine_path(
    canonical_workspace: FixtureWorkspace,
) -> None:
    observation_id = "EVD-20260730-fictional-note-01#OBS-002"
    path = (
        canonical_workspace.root
        / "02_knowledge"
        / "observations"
        / "EVD-20260730-fictional-note-01%23OBS-002.yaml"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""id: {observation_id}
kind: fact
statement: Fictional JSON pointer fixture.
confidence: high
source:
  ref: artifact://sha256/{"a" * 64}
  locator:
    type: json_pointer
    pointer: |
      /items/0
derived_from: []
related: []
""",
        encoding="utf-8",
    )

    report = validate_workspace(canonical_workspace.root, strict=True)

    assert report.ok
    assert "CTX-ABSOLUTE-PATH" not in [issue.code for issue in report.issues]


def test_observation_id_must_belong_to_evidence(
    canonical_workspace: FixtureWorkspace,
) -> None:
    evidence_id = "EVD-20260730-fictional-note-01"
    evidence = canonical_workspace.entity_payload(evidence_id, "evidence")
    evidence["artifact_ref"] = f"artifact://sha256/{'a' * 64}"
    evidence["observations"] = [
        canonical_workspace.observation_payload(identifier="EVD-20260730-other-note-01#OBS-001")
    ]
    canonical_workspace.write_markdown("02_knowledge/evidence", evidence)

    assert _codes(canonical_workspace).count("OBS-EVIDENCE-ID") == 1


def test_artifact_paths_cannot_escape_context(canonical_workspace: FixtureWorkspace) -> None:
    manifest_path = canonical_workspace.root / "00_inbox" / "manifests" / f"{ARTIFACT_ID}.json"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["preserved_path"] = "../outside.txt"
    canonical_workspace.write_json("00_inbox/manifests", payload)

    assert _codes(canonical_workspace).count("CTX-PATH-ESCAPE") == 1


@pytest.mark.parametrize(
    "preserved_path",
    ["C:relative", "01_processed/file.txt:stream", "01_processed/NUL", "01_processed/a\\b"],
)
def test_artifact_paths_must_be_portable(
    canonical_workspace: FixtureWorkspace,
    preserved_path: str,
) -> None:
    manifest_path = canonical_workspace.root / "00_inbox" / "manifests" / f"{ARTIFACT_ID}.json"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["preserved_path"] = preserved_path
    canonical_workspace.write_json("00_inbox/manifests", payload)

    assert _codes(canonical_workspace).count("CTX-PATH-ESCAPE") == 1


def test_artifact_path_with_null_byte_does_not_crash(
    canonical_workspace: FixtureWorkspace,
) -> None:
    manifest_path = canonical_workspace.root / "00_inbox" / "manifests" / f"{ARTIFACT_ID}.json"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["preserved_path"] = "01_processed/a\x00b"
    canonical_workspace.write_json("00_inbox/manifests", payload)

    assert _codes(canonical_workspace).count("CTX-PATH-ESCAPE") == 1


def test_secret_diagnostic_never_echoes_value(canonical_workspace: FixtureWorkspace) -> None:
    secret = "FICTIONAL_VALIDATION_SECRET_123456"
    path = canonical_workspace.root / "02_knowledge" / "notes" / "unsafe.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"api_key = {secret}\n", encoding="utf-8")

    report = validate_workspace(canonical_workspace.root)
    issue = next(issue for issue in report.issues if issue.code == "CTX-POSSIBLE-SECRET")

    assert secret not in issue.message
    assert secret not in (issue.repair_action or "")
    assert secret not in (issue.path or "")


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        (".env", "API_KEY=FICTIONAL_VALIDATION_SECRET_123456\n"),
        ("fixture.pem", "-----BEGIN PRIVATE KEY-----\nfictional\n"),
    ],
)
def test_secret_scan_includes_common_secret_files(
    canonical_workspace: FixtureWorkspace,
    filename: str,
    content: str,
) -> None:
    path = canonical_workspace.root / "90_integrations" / filename
    path.write_text(content, encoding="utf-8")

    assert _codes(canonical_workspace).count("CTX-POSSIBLE-SECRET") == 1


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("leak.ps1", "$env:API_KEY='FICTIONAL_SECRET_VALUE_123456'\n"),
        (".env", "AWS_SECRET_ACCESS_KEY=FICTIONAL_SECRET_VALUE_123456\n"),
        ("compact.env", "APIKEY=FICTIONAL_SECRET_VALUE_123456\n"),
        ("camel.env", "apiKey=FICTIONAL_SECRET_VALUE_123456\n"),
        ("client.env", "CLIENTSECRET=FICTIONAL_SECRET_VALUE_123456\n"),
    ],
)
def test_secret_scan_covers_code_and_prefixed_environment_keys(
    canonical_workspace: FixtureWorkspace,
    filename: str,
    content: str,
) -> None:
    path = canonical_workspace.root / "90_integrations" / filename
    path.write_text(content, encoding="utf-8")

    report = validate_workspace(canonical_workspace.root)
    issue = next(issue for issue in report.issues if issue.code == "CTX-POSSIBLE-SECRET")

    assert "FICTIONAL_SECRET_VALUE_123456" not in issue.message


def test_unc_path_is_reported_but_source_comment_is_not(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    payload["machine_path"] = "\\\\fictional-server\\share"
    canonical_workspace.write_markdown("02_knowledge/projects", payload)
    comment = canonical_workspace.root / "01_processed" / "comment.txt"
    comment.write_text("// ordinary source-code comment\n", encoding="utf-8")

    assert _codes(canonical_workspace).count("CTX-ABSOLUTE-PATH") == 1


def test_absolute_path_in_non_path_frontmatter_field_is_reported(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    payload["source_origin"] = "C:\\Users\\fictional\\source.txt"
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("CTX-ABSOLUTE-PATH") == 1


def test_absolute_path_in_multiline_frontmatter_scalar_is_reported(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    payload["notes"] = "Details follow:\n/etc/fictional-source\n"
    canonical_workspace.write_markdown("02_knowledge/projects", payload)

    assert _codes(canonical_workspace).count("CTX-ABSOLUTE-PATH") == 1


def test_absolute_path_in_canonical_markdown_body_is_reported(
    canonical_workspace: FixtureWorkspace,
) -> None:
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    canonical_workspace.write_markdown(
        "02_knowledge/projects",
        payload,
        body="/etc/fictional-source\n",
    )

    assert _codes(canonical_workspace).count("CTX-ABSOLUTE-PATH") == 1


def test_triple_slash_posix_path_is_reported(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "90_integrations" / "path-note.txt"
    path.write_text("///etc/passwd\n", encoding="utf-8")

    assert _codes(canonical_workspace).count("CTX-ABSOLUTE-PATH") == 1


def test_triple_slash_source_comment_is_not_a_machine_path(
    canonical_workspace: FixtureWorkspace,
) -> None:
    path = canonical_workspace.root / "01_processed" / "fixture.cs"
    path.write_text("/// <summary>Fictional source docs.</summary>\n", encoding="utf-8")

    assert "CTX-ABSOLUTE-PATH" not in _codes(canonical_workspace)


def test_top_level_state_symlink_is_not_followed(
    canonical_workspace: FixtureWorkspace,
    tmp_path: Path,
) -> None:
    state = canonical_workspace.root / "98_state"
    for child in state.iterdir():
        child.unlink()
    state.rmdir()
    outside = tmp_path / "outside-state"
    outside.mkdir()
    (outside / "private.txt").write_text(
        "API_KEY=FICTIONAL_OUTSIDE_SECRET_123456\n", encoding="utf-8"
    )
    try:
        state.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this platform")

    report = validate_workspace(canonical_workspace.root)
    codes = [issue.code for issue in report.issues]

    assert codes.count("CTX-PATH-ESCAPE") == 1
    assert "CTX-POSSIBLE-SECRET" not in codes


def test_federated_search_has_specific_code_only(
    canonical_workspace: FixtureWorkspace,
) -> None:
    config_path = canonical_workspace.root / "context.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    policies = payload["policies"]
    assert isinstance(policies, dict)
    policies["federated_search"] = True
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    codes = _codes(canonical_workspace)

    assert codes.count("CTX-FEDERATED-SEARCH") == 1
    assert "CTX-CONFIG" not in codes
