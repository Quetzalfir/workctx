from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from workctx.models.context import ContextConfig
from workctx.services.contexts import initialize_context
from workctx.validation import Severity, validate_workspace

from .conftest import PROJECT_ID, FixtureWorkspace

ROOT = Path(__file__).parents[2]


def _context_payload(root: Path) -> dict[str, object]:
    payload = yaml.safe_load((root / "context.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("context fixture must be a mapping")
    return payload


def test_working_dirs_default_and_schema_contract(tmp_path: Path) -> None:
    root = tmp_path / "working-default"
    config = initialize_context(root, name="Working Default", context_id="working-default")
    schema = json.loads((ROOT / "schemas" / "context.schema.json").read_text(encoding="utf-8"))

    assert config.working_dirs == ["outputs"]
    assert _context_payload(root)["working_dirs"] == ["outputs"]
    Draft202012Validator(schema).validate(config.model_dump(mode="json"))


@pytest.mark.parametrize(
    "working_dirs",
    [
        [".hidden"],
        ["nested/path"],
        [r"nested\path"],
        ["02_knowledge"],
        ["06_overrides"],
        ["Outputs", "outputs"],
    ],
)
def test_working_dirs_reject_hidden_nested_reserved_or_duplicate_names(
    tmp_path: Path,
    working_dirs: list[str],
) -> None:
    root = tmp_path / "working-invalid"
    initialize_context(root, name="Working Invalid", context_id="working-invalid")
    payload = _context_payload(root)
    payload["working_dirs"] = working_dirs

    with pytest.raises(ValidationError):
        ContextConfig.model_validate(payload)


@pytest.mark.parametrize(
    "working_dirs",
    [
        [".hidden"],
        ["nested/path"],
        [r"nested\path"],
        ["02_knowledge"],
        ["outputs", "outputs"],
    ],
)
def test_working_dirs_schema_rejects_invalid_names(
    tmp_path: Path,
    working_dirs: list[str],
) -> None:
    root = tmp_path / "working-schema-invalid"
    initialize_context(
        root,
        name="Working Schema Invalid",
        context_id="working-schema-invalid",
    )
    payload = _context_payload(root)
    payload["working_dirs"] = working_dirs
    schema = json.loads((ROOT / "schemas" / "context.schema.json").read_text(encoding="utf-8"))

    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(schema).validate(payload)


def test_working_dir_downgrades_content_findings_without_silencing_them(
    tmp_path: Path,
) -> None:
    root = tmp_path / "working-findings"
    initialize_context(root, name="Working Findings", context_id="working-findings")
    secret = "FICTIONAL_WORKING_SECRET_123456"
    path = root / "outputs" / "PRJ-not-canonical.md"
    path.write_text(
        "---\n"
        "malformed: [\n"
        "---\n"
        "C:\\fictional\\machine\\report.txt\n"
        f"API_KEY={secret}\n"
        "workctx://working-findings/project/PRJ-missing\n",
        encoding="utf-8",
    )

    report = validate_workspace(root)
    issues = [issue for issue in report.issues if issue.path == "outputs/PRJ-not-canonical.md"]
    absolute = next(issue for issue in issues if issue.code == "CTX-ABSOLUTE-PATH")
    possible_secret = next(issue for issue in issues if issue.code == "CTX-POSSIBLE-SECRET")

    assert absolute.severity is Severity.ADVISORY
    assert "line 4" in absolute.message
    assert "C:\\fictional" not in absolute.message
    assert possible_secret.severity is Severity.WARNING
    assert "line 5" in possible_secret.message
    assert "assignment to key 'API_KEY'" in possible_secret.message
    assert secret not in possible_secret.message
    assert not {"DOC-PARSE", "DOC-MODEL", "REF-UNRESOLVED"}.intersection(
        issue.code for issue in issues
    )
    assert report.ok


def test_working_dir_is_exempt_from_entity_location_diagnostic(
    canonical_workspace: FixtureWorkspace,
) -> None:
    identifier = "INT-working-copy"
    canonical_workspace.write_markdown(
        "outputs",
        canonical_workspace.entity_payload(identifier, "integration"),
    )

    report = validate_workspace(canonical_workspace.root)

    assert "CTX-MISPLACED-ENTITY" not in {issue.code for issue in report.issues}
    assert report.ok


def test_configured_working_dir_replaces_the_default(tmp_path: Path) -> None:
    root = tmp_path / "working-custom"
    initialize_context(root, name="Working Custom", context_id="working-custom")
    payload = _context_payload(root)
    payload["working_dirs"] = ["scratch"]
    (root / "context.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    (root / "scratch").mkdir()
    (root / "scratch" / "probe.txt").write_text(
        "API_KEY=FICTIONAL_CUSTOM_SECRET_123456\n",
        encoding="utf-8",
    )
    (root / "outputs" / "probe.txt").write_text(
        "API_KEY=FICTIONAL_DEFAULT_SECRET_123456\n",
        encoding="utf-8",
    )

    report = validate_workspace(root)
    custom = next(
        issue
        for issue in report.issues
        if issue.code == "CTX-POSSIBLE-SECRET" and issue.path == "scratch/probe.txt"
    )
    former_default = next(
        issue
        for issue in report.issues
        if issue.code == "CTX-POSSIBLE-SECRET" and issue.path == "outputs/probe.txt"
    )

    assert custom.severity is Severity.WARNING
    assert former_default.severity is Severity.ERROR


def test_path_escape_stays_an_error_in_working_dir(tmp_path: Path) -> None:
    root = tmp_path / "working-link"
    initialize_context(root, name="Working Link", context_id="working-link")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "outputs" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this platform")

    report = validate_workspace(root)
    issue = next(
        issue
        for issue in report.issues
        if issue.code == "CTX-PATH-ESCAPE" and issue.path == "outputs/linked"
    )

    assert issue.severity is Severity.ERROR
    assert not report.ok


def test_structured_absolute_path_reports_exact_value_free_line(
    canonical_workspace: FixtureWorkspace,
) -> None:
    machine_path = r"C:\Users\fictional\source.txt"
    payload = canonical_workspace.entity_payload(PROJECT_ID, "project")
    payload["source_origin"] = machine_path
    path = canonical_workspace.write_markdown("02_knowledge/projects", payload)
    expected_line = next(
        index
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "source_origin:" in line
    )

    report = validate_workspace(canonical_workspace.root)
    issue = next(issue for issue in report.issues if issue.code == "CTX-ABSOLUTE-PATH")

    assert f"line {expected_line}" in issue.message
    assert machine_path not in issue.message
