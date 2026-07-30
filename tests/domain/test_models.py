import pytest
from pydantic import ValidationError

from workctx.domain.claims import Claim
from workctx.domain.observations import Observation
from workctx.domain.relations import TypedReference


def test_typed_reference_rejects_file_uri() -> None:
    with pytest.raises(ValidationError, match="file://"):
        TypedReference(relation="supports", target="file:///tmp/evidence.txt")


def test_observation_requires_an_artifact_source() -> None:
    with pytest.raises(ValidationError, match="artifact"):
        Observation.model_validate(
            {
                "id": "EVD-20260730-auth-review-01#OBS-001",
                "kind": "fact",
                "statement": "A fictional statement.",
                "confidence": "high",
                "source": {
                    "ref": "repo://fictional-repo@abcdef1/src/example.py#L1-L2",
                    "locator": {"type": "line_range", "start_line": 1, "end_line": 2},
                },
            }
        )


def test_claim_requires_canonical_observation_uris() -> None:
    with pytest.raises(ValidationError, match=r"%23"):
        Claim.model_validate(
            {
                "schema_version": 1,
                "id": "CLM-2026-00421",
                "subject": "workctx://fictional-lab/task/TASK-2026-014",
                "predicate": "status",
                "object": "blocked",
                "observed_at": "2026-07-30T14:42:00Z",
                "status": "current",
                "confidence": "high",
                "source_observations": [
                    "workctx://fictional-lab/observation/EVD-20260730-auth-review-01#OBS-001"
                ],
            }
        )


def test_claim_rejects_non_json_object_values() -> None:
    with pytest.raises(ValidationError):
        Claim.model_validate(
            {
                "schema_version": 1,
                "id": "CLM-2026-00421",
                "subject": "workctx://fictional-lab/task/TASK-2026-014",
                "predicate": "owner",
                "object": object(),
                "observed_at": "2026-07-30T14:42:00Z",
                "status": "current",
                "confidence": "high",
                "source_observations": [
                    "workctx://fictional-lab/observation/EVD-20260730-auth-review-01%23OBS-001"
                ],
            }
        )
