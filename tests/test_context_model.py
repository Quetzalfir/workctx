from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from workctx.models.context import ContextConfig


def _valid_context_data() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "schema_version": 1,
        "id": "company-a",
        "name": "Company A",
        "kind": "company",
        "profile": "hybrid",
        "languages": {
            "repository": "en",
            "user_interaction": "en",
        },
        "timezone": "UTC",
        "classification": "confidential",
        "security_boundary": "isolated",
        "policies": {
            "local_mutations": "review_required",
            "external_writes": "approval_required",
            "raw_evidence_retention": "preserve",
            "federated_search": False,
        },
        "created_at": now,
        "updated_at": now,
    }


def test_context_config_rejects_non_slug_id() -> None:
    data = _valid_context_data()
    data["id"] = "Company A"

    with pytest.raises(ValidationError):
        ContextConfig.model_validate(data)


def test_context_config_enforces_isolated_invariants() -> None:
    config = ContextConfig.model_validate(_valid_context_data())

    assert config.security_boundary == "isolated"
    assert config.policies.federated_search is False
    assert config.languages.repository == "en"


@pytest.mark.parametrize("schema_version", [0, 2])
def test_context_config_rejects_unsupported_schema_versions_with_migration_hint(
    schema_version: int,
) -> None:
    data = _valid_context_data()
    data["schema_version"] = schema_version

    with pytest.raises(ValidationError, match="migration"):
        ContextConfig.model_validate(data)
