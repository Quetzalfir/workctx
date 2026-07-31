from __future__ import annotations

from workctx.retrieval.security import (
    REDACTED,
    sanitize_json,
    sanitize_named_value,
    sanitize_text,
)


def test_sanitize_text_matches_existing_secret_pattern_union() -> None:
    original = (
        "keep this; api_key=fictional-secret-value-12345; "
        "Authorization: Bearer opaque-fictional-token; keep that"
    )

    sanitized = sanitize_text(original)

    assert "keep this" in sanitized
    assert "keep that" in sanitized
    assert "fictional-secret-value-12345" not in sanitized
    assert "opaque-fictional-token" not in sanitized
    assert sanitized.count(REDACTED) == 2


def test_sanitize_json_redacts_secret_fields_and_sorts_nested_keys() -> None:
    sanitized = sanitize_json(
        {
            "z": [{"password": "fictional phrase"}, "Bearer fictional-bearer-value"],
            "api_key": "fictional-secret-value-12345",
            "a": "safe",
        }
    )

    assert sanitized == {
        "a": "safe",
        "api_key": REDACTED,
        "z": [{"password": REDACTED}, f"Bearer {REDACTED}"],
    }


def test_private_key_material_is_replaced_wholesale() -> None:
    material = "prefix\n-----BEGIN PRIVATE KEY-----\nfictional\n-----END PRIVATE KEY-----"

    assert sanitize_text(material) == REDACTED


def test_separate_secret_predicate_redacts_its_value() -> None:
    assert sanitize_named_value("api_key", "fictional-secret-value-12345") == REDACTED
