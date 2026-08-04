from __future__ import annotations

import json
import pickle

import pytest
from pydantic import BaseModel, TypeAdapter

from workctx.secrets import InvalidSecretRefError, SecretRef, SecretValue

FICTIONAL_VALUE = "fictional-opaque-material-for-tests"


class _SecretPayload(BaseModel):
    secret: SecretValue


@pytest.mark.parametrize(
    "name",
    [
        "a",
        "service-token",
        "service-2-token",
        "0",
        "a" * 64,
    ],
)
def test_secret_ref_accepts_canonical_lowercase_kebab_names(name: str) -> None:
    ref = SecretRef(name)

    assert ref.name == name
    assert SecretRef.parse(ref) is ref


@pytest.mark.parametrize(
    "name",
    [
        "",
        "UPPER",
        "under_score",
        "-leading",
        "trailing-",
        "two--hyphens",
        "has space",
        "a" * 65,
    ],
)
def test_secret_ref_rejects_noncanonical_names_without_echoing_input(name: str) -> None:
    with pytest.raises(InvalidSecretRefError) as caught:
        SecretRef(name)

    assert name not in str(caught.value) or not name


def test_secret_ref_maps_to_documented_environment_name() -> None:
    assert SecretRef("fictional-service-token").env_var == (
        "WORKCTX_SECRET_FICTIONAL_SERVICE_TOKEN"
    )


def test_secret_value_only_reveals_through_explicit_accessor() -> None:
    value = SecretValue(FICTIONAL_VALUE)

    assert value.reveal() == FICTIONAL_VALUE
    assert FICTIONAL_VALUE not in repr(value)
    assert FICTIONAL_VALUE not in str(value)
    assert FICTIONAL_VALUE not in f"{value}"
    assert FICTIONAL_VALUE not in repr([value])


def test_secret_value_json_and_pydantic_serialization_are_redacted() -> None:
    value = SecretValue(FICTIONAL_VALUE)
    payload = _SecretPayload(secret=value)

    with pytest.raises(TypeError) as unsupported:
        json.dumps({"secret": value})

    serialized = (
        json.dumps({"secret": value}, default=str),
        TypeAdapter(SecretValue).dump_json(value).decode("utf-8"),
        payload.model_dump_json(),
        json.dumps(payload.model_dump(mode="json")),
    )

    assert all(FICTIONAL_VALUE not in item for item in serialized)
    assert all("REDACTED" in item for item in serialized)
    assert FICTIONAL_VALUE not in str(unsupported.value)


def test_secret_value_blocks_pickle_without_leaking() -> None:
    value = SecretValue(FICTIONAL_VALUE)

    with pytest.raises(TypeError) as caught:
        pickle.dumps(value)

    assert FICTIONAL_VALUE not in str(caught.value)
