import pytest

from workctx.domain.references import (
    ArtifactReference,
    RepoReference,
    WorkctxUri,
    normalize_workctx_uri,
    parse_durable_reference,
    parse_source_reference,
    validate_durable_reference,
)
from workctx.models.reference import WorkctxUri as CompatibilityWorkctxUri


def test_compatibility_shim_exports_the_domain_class() -> None:
    assert CompatibilityWorkctxUri is WorkctxUri


def test_observation_authoring_uri_normalizes_and_is_idempotent() -> None:
    authored = "workctx://fictional-lab/observation/EVD-20260730-auth-review-01#OBS-004"
    canonical = "workctx://fictional-lab/observation/EVD-20260730-auth-review-01%23OBS-004"

    assert normalize_workctx_uri(authored) == canonical
    assert normalize_workctx_uri(canonical) == canonical


def test_literal_observation_fragment_is_rejected_with_actionable_guidance() -> None:
    with pytest.raises(ValueError, match=r"%23"):
        WorkctxUri.parse("workctx://fictional-lab/observation/EVD-20260730-auth-review-01#OBS-004")


@pytest.mark.parametrize(
    "value",
    (
        "workctx://fictional-lab/task/TASK-2026-014#",
        "workctx://fictional-lab/task/TASK-2026-014?",
    ),
)
def test_workctx_uri_rejects_empty_query_or_fragment_delimiters(value: str) -> None:
    with pytest.raises(ValueError):
        WorkctxUri.parse(value)


@pytest.mark.parametrize(
    "value",
    (
        "workctx://fictional-lab/task/%2E%2E",
        "workctx://fictional-lab/task/TASK%GG",
        "workctx://fictional-lab/task/%2Ftmp",
        "workctx://fictional-lab/task//TASK-2026-014",
        "workctx://fictional-lab/task/TASK-2026-014/",
    ),
)
def test_workctx_uri_rejects_encoded_traversal_and_empty_segments(value: str) -> None:
    with pytest.raises(ValueError):
        WorkctxUri.parse(value)


@pytest.mark.parametrize(
    "value",
    (
        "workctx://fictional-lab/task/TASK-2026-014#section",
        "workctx://fictional-lab/task/TASK-2026-014#OBS-001",
        "workctx://fictional-lab/observation/not-an-observation#OBS-001",
    ),
)
def test_normalizer_rejects_non_observation_fragments(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_workctx_uri(value)


def test_artifact_reference_round_trips() -> None:
    value = "artifact://sha256/" + "a" * 64
    reference = ArtifactReference.parse(value)

    assert reference.digest == "a" * 64
    assert str(reference) == value
    assert parse_source_reference(value) == reference


@pytest.mark.parametrize(
    "value",
    (
        "artifact://sha256/abc",
        "artifact://sha256/" + "A" * 64,
        "artifact://md5/" + "a" * 64,
        "artifact://sha256/" + "a" * 64 + "?download=true",
    ),
)
def test_artifact_reference_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError):
        ArtifactReference.parse(value)


def test_repository_reference_round_trips_with_encoded_path() -> None:
    reference = RepoReference(
        repo_id="fictional-repo",
        commit="abcdef1",
        path="docs/design notes.md",
        start_line=7,
        end_line=12,
    )
    rendered = "repo://fictional-repo@abcdef1/docs/design%20notes.md#L7-L12"

    assert str(reference) == rendered
    assert RepoReference.parse(rendered) == reference
    assert parse_source_reference(rendered) == reference


@pytest.mark.parametrize(
    "value",
    (
        "repo://fictional-repo/src/example.py#L1-L2",
        "repo://fictional-repo@branch/src/example.py#L1-L2",
        "repo://fictional-repo@abcdef1/src/example.py#L0-L2",
        "repo://fictional-repo@abcdef1/src/example.py#L3-L2",
        "repo://fictional-repo@abcdef1/src/%2E%2E/secret.txt#L1-L2",
        "repo://fictional-repo@abcdef1/src/%GG.py#L1-L2",
        "repo://fictional-repo@abcdef1/C%3A/windows.txt#L1-L2",
        "repo://fictional-repo@abcdef1/src/example.py?raw=true#L1-L2",
    ),
)
def test_repository_reference_rejects_mutable_or_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        RepoReference.parse(value)


@pytest.mark.parametrize(
    "value",
    (
        "/var/tmp/evidence.txt",
        "C:\\temp\\evidence.txt",
        "C:/temp/evidence.txt",
        "\\\\server\\share\\evidence.txt",
        "file:///var/tmp/evidence.txt",
        "file://server/share/evidence.txt",
    ),
)
def test_durable_references_reject_machine_specific_paths(value: str) -> None:
    with pytest.raises(ValueError):
        validate_durable_reference(value)


def test_durable_reference_accepts_external_grammar_placeholder() -> None:
    value = "jira://fictional-connection/DEMO-42"

    assert parse_durable_reference(value) == value
    assert validate_durable_reference(value) == value
