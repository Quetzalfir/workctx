"""Lead integration test (D-035): public secret predicate for ingestion guards."""

from workctx.validation import contains_possible_secret


def test_secret_like_values_are_detected() -> None:
    assert contains_possible_secret('api_key = "sk-fictional-1234567890abcdef"')
    assert contains_possible_secret("-----BEGIN RSA PRIVATE KEY-----")


def test_ordinary_prose_is_not_flagged() -> None:
    assert not contains_possible_secret("The portal delegates authentication upstream.")
