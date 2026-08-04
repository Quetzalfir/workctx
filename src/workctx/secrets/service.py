"""Public secret-reference resolver and OS-store operations."""

from __future__ import annotations

import os

from workctx.secrets._backend import (
    backend_available,
    read_password,
    remove_password,
    write_password,
)
from workctx.secrets._index import SecretNamesIndex
from workctx.secrets.errors import InvalidSecretValueError, SecretNotFoundError
from workctx.secrets.models import SecretLayer, SecretPresence, SecretRef, SecretValue

_ENV_PREFIX = "WORKCTX_SECRET_"


def resolve(name: str | SecretRef) -> SecretValue:
    """Resolve environment first, then the machine-global OS credential store."""

    ref = SecretRef.parse(name)
    if ref.env_var in os.environ:
        return SecretValue(os.environ[ref.env_var])
    stored = read_password(ref)
    if stored is None:
        raise SecretNotFoundError(ref.name)
    return SecretValue(stored)


def resolution_layer(name: str | SecretRef) -> SecretLayer:
    """Return the winning resolver layer without returning a value."""

    ref = SecretRef.parse(name)
    if ref.env_var in os.environ:
        return SecretLayer.ENVIRONMENT
    stored = read_password(ref)
    if stored is None:
        raise SecretNotFoundError(ref.name)
    del stored
    return SecretLayer.OS_STORE


def store(name: str | SecretRef, value: str | SecretValue) -> None:
    """Store a value in the OS credential store and index only its name."""

    ref = SecretRef.parse(name)
    raw_value = value.reveal() if isinstance(value, SecretValue) else value
    if not isinstance(raw_value, str):
        raise InvalidSecretValueError
    write_password(ref, raw_value)
    SecretNamesIndex().add(ref)


def delete(name: str | SecretRef) -> bool:
    """Idempotently remove an OS-store entry and its names-only index record."""

    ref = SecretRef.parse(name)
    stored = read_password(ref)
    if stored is None:
        SecretNamesIndex().remove(ref)
        return False
    del stored
    remove_password(ref)
    SecretNamesIndex().remove(ref)
    return True


def exists(name: str | SecretRef) -> bool:
    """Return whether the resolver chain can satisfy a reference."""

    try:
        resolution_layer(name)
    except SecretNotFoundError:
        return False
    return True


def list_names() -> tuple[str, ...]:
    """List known environment and indexed OS-store names in stable order."""

    names = set(SecretNamesIndex().list())
    names.update(_environment_names())
    return tuple(sorted(names))


def inspect_presence(name: str | SecretRef) -> SecretPresence:
    """Return names-and-presence metadata for both resolver layers."""

    ref = SecretRef.parse(name)
    stored = read_password(ref)
    os_store = stored is not None
    del stored
    return SecretPresence(
        name=ref.name,
        environment=ref.env_var in os.environ,
        os_store=os_store,
    )


def environment_contains(name: str | SecretRef) -> bool:
    """Check the environment layer without loading keyring."""

    return SecretRef.parse(name).env_var in os.environ


def os_store_available() -> bool:
    """Check whether the keyring package has a usable local backend."""

    return backend_available()


def env_var_name(name: str | SecretRef) -> str:
    """Return the deterministic environment-variable name for a reference."""

    return SecretRef.parse(name).env_var


def _environment_names() -> set[str]:
    names: set[str] = set()
    for variable_name in os.environ:
        if not variable_name.startswith(_ENV_PREFIX):
            continue
        suffix = variable_name.removeprefix(_ENV_PREFIX)
        candidate = suffix.lower().replace("_", "-")
        try:
            names.add(SecretRef(candidate).name)
        except Exception:
            continue
    return names
