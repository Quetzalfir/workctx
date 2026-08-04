from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from workctx.secrets import SecretRef, _backend
from workctx.secrets import service as secret_service


class MemoryKeyring:
    """Value-holding test double whose representation is always content-free."""

    priority = 1.0

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.failure_message: str | None = None

    def __repr__(self) -> str:
        return "MemoryKeyring([REDACTED])"

    def get_keyring(self) -> MemoryKeyring:
        return self

    def get_password(self, service_name: str, username: str) -> str | None:
        self.calls.append(("get", service_name, username))
        if self.failure_message is not None:
            raise RuntimeError(self.failure_message)
        return self._values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.calls.append(("set", service_name, username))
        if self.failure_message is not None:
            raise RuntimeError(self.failure_message)
        self._values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.calls.append(("delete", service_name, username))
        if self.failure_message is not None:
            raise RuntimeError(self.failure_message)
        self._values.pop((service_name, username), None)

    def contains(self, name: str) -> bool:
        return ("workctx", name) in self._values

    def reveal_for_test(self, name: str) -> str:
        return self._values[("workctx", name)]


class MemoryNamesIndex:
    """Names-only test double shared across service calls in one test."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def list(self) -> tuple[str, ...]:
        return tuple(sorted(self.names))

    def add(self, ref: SecretRef) -> None:
        self.names.add(ref.name)

    def remove(self, ref: SecretRef) -> bool:
        if ref.name not in self.names:
            return False
        self.names.remove(ref.name)
        return True


@pytest.fixture(autouse=True)
def isolated_secret_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MemoryKeyring]:
    for variable_name in tuple(os.environ):
        if variable_name.startswith("WORKCTX_SECRET_"):
            monkeypatch.delenv(variable_name, raising=False)

    memory = MemoryKeyring()
    names_index = MemoryNamesIndex()
    monkeypatch.setattr(_backend, "import_module", lambda _name: memory)
    monkeypatch.setattr(secret_service, "SecretNamesIndex", lambda: names_index)
    yield memory


@pytest.fixture
def memory_keyring(isolated_secret_backends: MemoryKeyring) -> MemoryKeyring:
    return isolated_secret_backends
