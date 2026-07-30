from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from workctx.models.context import ContextConfig


def test_context_config_rejects_non_slug_id() -> None:
    with pytest.raises(ValidationError):
        ContextConfig(
            id="Company A",
            name="Company A",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def test_context_config_defaults_are_isolated() -> None:
    config = ContextConfig(
        id="company-a",
        name="Company A",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert config.security_boundary == "isolated"
    assert config.policies.federated_search is False
    assert config.languages.repository == "en"
