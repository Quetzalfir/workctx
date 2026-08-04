from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from workctx.secrets import SecretRef, _backend
from workctx.secrets import service as secret_service


class MemoryKeyring:
    """In-memory credential backend whose representation never exposes values."""

    priority = 1.0

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []

    def __repr__(self) -> str:
        return "MemoryKeyring([REDACTED])"

    def get_keyring(self) -> MemoryKeyring:
        return self

    def get_password(self, service_name: str, username: str) -> str | None:
        self.calls.append(("get", service_name, username))
        return self._values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.calls.append(("set", service_name, username))
        self._values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.calls.append(("delete", service_name, username))
        self._values.pop((service_name, username), None)


class MemoryNamesIndex:
    def __init__(self) -> None:
        self.names: set[str] = set()

    def list(self) -> tuple[str, ...]:
        return tuple(sorted(self.names))

    def add(self, ref: SecretRef) -> None:
        self.names.add(ref.name)

    def remove(self, ref: SecretRef) -> bool:
        existed = ref.name in self.names
        self.names.discard(ref.name)
        return existed


@pytest.fixture(autouse=True)
def isolated_connector_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MemoryKeyring]:
    for variable_name in tuple(os.environ):
        if variable_name.startswith("WORKCTX_SECRET_"):
            monkeypatch.delenv(variable_name, raising=False)

    memory = MemoryKeyring()
    names_index = MemoryNamesIndex()
    monkeypatch.setattr(_backend, "import_module", lambda _name: memory)
    monkeypatch.setattr(secret_service, "SecretNamesIndex", lambda: names_index)

    def refuse_real_network(
        _transport: httpx.HTTPTransport,
        _request: httpx.Request,
    ) -> httpx.Response:
        raise AssertionError("connector tests must inject a mocked httpx transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_real_network)
    yield memory


@pytest.fixture
def memory_keyring(isolated_connector_boundaries: MemoryKeyring) -> MemoryKeyring:
    return isolated_connector_boundaries


@pytest.fixture
def connector_tmp_path() -> Iterator[Path]:
    """Use ordinary directory permissions instead of pytest's sandbox-hostile 0700 temp root."""

    parent = Path(tempfile.gettempdir()) / "workctx-connector-tests"
    parent.mkdir(mode=0o755, exist_ok=True)
    root = parent / f"case-{uuid4().hex}"
    root.mkdir(mode=0o755)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
