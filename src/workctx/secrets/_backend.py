"""Lazy, sanitized boundary around the optional-at-runtime keyring backend."""

from __future__ import annotations

from importlib import import_module
from typing import Protocol, cast

from workctx.secrets.errors import SecretBackendUnavailableError
from workctx.secrets.models import SecretRef

SERVICE_NAMESPACE = "workctx"


class _CredentialBackend(Protocol):
    @property
    def priority(self) -> float: ...


class KeyringModule(Protocol):
    def get_keyring(self) -> _CredentialBackend: ...

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


def backend_available() -> bool:
    """Return whether keyring exposes a usable OS credential-store backend."""

    try:
        _require_keyring()
    except SecretBackendUnavailableError:
        return False
    return True


def read_password(ref: SecretRef) -> str | None:
    """Read one OS-store value while replacing all backend failures."""

    keyring = _require_keyring()
    failed = False
    value: object = None
    try:
        value = keyring.get_password(SERVICE_NAMESPACE, ref.name)
    except Exception:
        failed = True
    if failed or (value is not None and not isinstance(value, str)):
        raise SecretBackendUnavailableError
    return value


def write_password(ref: SecretRef, value: str) -> None:
    """Write one value without allowing a backend exception to retain it in text."""

    keyring = _require_keyring()
    failed = False
    try:
        keyring.set_password(SERVICE_NAMESPACE, ref.name, value)
    except Exception:
        failed = True
    if failed:
        raise SecretBackendUnavailableError


def remove_password(ref: SecretRef) -> None:
    """Delete one existing OS-store entry through the sanitized boundary."""

    keyring = _require_keyring()
    failed = False
    try:
        keyring.delete_password(SERVICE_NAMESPACE, ref.name)
    except Exception:
        failed = True
    if failed:
        raise SecretBackendUnavailableError


def _require_keyring() -> KeyringModule:
    failed = False
    loaded: object | None = None
    try:
        loaded = import_module("keyring")
    except ImportError:
        failed = True
    if failed or loaded is None:
        raise SecretBackendUnavailableError

    module = cast(KeyringModule, loaded)
    usable = False
    try:
        priority = module.get_keyring().priority
        usable = not isinstance(priority, bool) and float(priority) > 0
    except Exception:
        usable = False
    if not usable:
        raise SecretBackendUnavailableError
    return module
