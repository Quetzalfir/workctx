from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from workctx.validation.secret_acknowledgments import (
    SecretScanAcknowledgment,
    SecretScanAcknowledgments,
    dump_secret_scan_acknowledgments,
    load_secret_scan_acknowledgments,
)

ROOT = Path(__file__).parents[2]
SCHEMA_PATH = ROOT / "schemas" / "secret-scan-acknowledgments.schema.json"


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "acknowledgments": [
            {
                "path": "02_knowledge/PRJ-fictional.md",
                "content_hash": f"sha256:{'a' * 64}",
                "acknowledged_at": "2026-09-23T18:00:00Z",
                "note": "Confirmed fictional fixture text.",
            }
        ],
    }


def test_acknowledgment_schema_and_typed_yaml_round_trip() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(_payload())

    record = SecretScanAcknowledgments.model_validate(_payload())
    rendered = dump_secret_scan_acknowledgments(record)

    assert load_secret_scan_acknowledgments(rendered.decode("utf-8")) == record
    assert rendered.endswith(b"\n")


def test_acknowledgment_file_cannot_be_empty_or_acknowledge_itself() -> None:
    with pytest.raises(ValidationError):
        SecretScanAcknowledgments(acknowledgments=())
    with pytest.raises(ValidationError):
        SecretScanAcknowledgment(
            path="99_meta/secret-scan-acknowledgments.yaml",
            content_hash=f"sha256:{'b' * 64}",
            acknowledged_at=datetime(2026, 9, 23, 18, tzinfo=UTC),
        )
