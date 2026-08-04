from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from workctx.connectors import (
    ConnectorManifest,
    ConnectorManifestError,
    DuplicateConnectorNameError,
    load_manifests,
)
from workctx.services.contexts import initialize_context
from workctx.validation import validate_workspace

ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "manifest"
POSITIVE_FIXTURES = sorted((FIXTURE_ROOT / "positive").glob("*.yaml"))
NEGATIVE_FIXTURES = sorted((FIXTURE_ROOT / "negative").glob("*.yaml"))
SCHEMA = json.loads(
    (ROOT / "schemas" / "connector-manifest.schema.json").read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a YAML mapping in {path}")
    return payload


def _context(tmp_path: Path) -> Path:
    root = tmp_path / "context"
    initialize_context(root, name="Fictional Connector Lab", context_id="connector-test")
    return root


def _write_manifest(root: Path, filename: str, content: str) -> Path:
    directory = root / "07_connectors"
    directory.mkdir(exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def test_connector_fixture_sets_are_complete() -> None:
    assert {path.stem for path in POSITIVE_FIXTURES} == {"full", "minimal"}
    assert {path.stem for path in NEGATIVE_FIXTURES} == {
        "absolute-snapshot-url",
        "auth-without-secret",
        "credentialed-base-url",
        "duplicate-snapshot",
        "extra-property",
        "forbidden-header",
        "insecure-without-opt-in",
        "invalid-auth-style",
        "invalid-secret-ref",
        "oversized-limits",
        "secret-without-auth",
        "wrong-schema-version",
    }


def test_connector_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", POSITIVE_FIXTURES, ids=lambda path: path.stem)
def test_positive_manifest_fixtures_validate_and_round_trip(path: Path) -> None:
    payload = _load_yaml(path)
    VALIDATOR.validate(payload)

    manifest = ConnectorManifest.model_validate(payload)
    dumped = manifest.model_dump(mode="json", exclude_none=True)

    VALIDATOR.validate(dumped)
    assert ConnectorManifest.model_validate(dumped) == manifest
    assert ConnectorManifest.model_validate_json(manifest.model_dump_json()) == manifest


@pytest.mark.parametrize("path", NEGATIVE_FIXTURES, ids=lambda path: path.stem)
def test_negative_manifest_fixtures_are_rejected_by_schema_and_model(path: Path) -> None:
    payload = _load_yaml(path)

    assert list(VALIDATOR.iter_errors(payload))
    with pytest.raises(ValidationError):
        ConnectorManifest.model_validate(payload)


def test_manifest_defaults_are_aligned() -> None:
    manifest = ConnectorManifest.model_validate(_load_yaml(FIXTURE_ROOT / "positive/minimal.yaml"))

    assert manifest.timeout_seconds == 30
    assert manifest.max_bytes == 10 * 1024 * 1024
    assert manifest.allow_insecure_http is False
    assert manifest.snapshots[0].accept == "application/json"
    assert manifest.snapshots[0].query == {}
    assert manifest.snapshots[0].schedule is None


def test_query_auth_parameter_cannot_collide_with_snapshot_query() -> None:
    payload = _load_yaml(FIXTURE_ROOT / "positive/minimal.yaml")
    payload["secret_ref"] = "fictional-service-token"
    payload["auth_style"] = "query:api_key"
    payload["snapshots"][0]["query"] = {"api_key": "operator-value"}

    VALIDATOR.validate(payload)
    with pytest.raises(ValidationError, match="auth query parameter"):
        ConnectorManifest.model_validate(payload)


def test_load_manifests_returns_stable_validated_models(connector_tmp_path: Path) -> None:
    root = _context(connector_tmp_path)
    _write_manifest(
        root,
        "rally-interno.yaml",
        (FIXTURE_ROOT / "positive/full.yaml").read_text(encoding="utf-8"),
    )
    _write_manifest(
        root,
        "fictional-service.yaml",
        (FIXTURE_ROOT / "positive/minimal.yaml").read_text(encoding="utf-8"),
    )

    manifests = load_manifests(root)

    assert [manifest.name for manifest in manifests] == ["fictional-service", "rally-interno"]
    assert manifests[1].allow_insecure_http is True


def test_load_manifests_returns_empty_when_directory_is_absent(
    connector_tmp_path: Path,
) -> None:
    assert load_manifests(_context(connector_tmp_path)) == ()


def test_load_manifests_refuses_duplicate_names(connector_tmp_path: Path) -> None:
    root = _context(connector_tmp_path)
    content = (FIXTURE_ROOT / "positive/minimal.yaml").read_text(encoding="utf-8")
    _write_manifest(root, "fictional-service.yaml", content)
    _write_manifest(root, "second-file.yaml", content)

    with pytest.raises(DuplicateConnectorNameError, match="fictional-service"):
        load_manifests(root)


def test_load_manifests_refuses_filename_name_mismatch(connector_tmp_path: Path) -> None:
    root = _context(connector_tmp_path)
    _write_manifest(
        root,
        "wrong-name.yaml",
        (FIXTURE_ROOT / "positive/minimal.yaml").read_text(encoding="utf-8"),
    )

    with pytest.raises(ConnectorManifestError) as caught:
        load_manifests(root)

    assert caught.value.path == "07_connectors/wrong-name.yaml"


def test_load_manifests_refuses_duplicate_yaml_keys_without_echoing_values(
    connector_tmp_path: Path,
) -> None:
    root = _context(connector_tmp_path)
    secret_like_value = "fictional-value-that-must-not-escape"
    _write_manifest(
        root,
        "fictional-service.yaml",
        "\n".join(
            (
                "schema_version: 1",
                "name: fictional-service",
                f"base_url: https://api.example.test/{secret_like_value}",
                "base_url: https://other.example.test/",
                "snapshots:",
                "  - id: current-items",
                "    path: /items",
                "",
            )
        ),
    )

    with pytest.raises(ConnectorManifestError) as caught:
        load_manifests(root)

    assert secret_like_value not in str(caught.value)
    assert secret_like_value not in repr(caught.value)


def test_workspace_validation_accepts_operator_owned_connector_directory(
    connector_tmp_path: Path,
) -> None:
    root = _context(connector_tmp_path)
    path = _write_manifest(
        root,
        "fictional-service.yaml",
        (FIXTURE_ROOT / "positive/minimal.yaml").read_text(encoding="utf-8"),
    )

    report = validate_workspace(root)

    assert path.is_file()
    assert not [issue for issue in report.issues if (issue.path or "").startswith("07_connectors")]
