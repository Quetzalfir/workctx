from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

import workctx.drafting.delivery as delivery_runtime
from workctx.secrets import SecretRef, _backend
from workctx.secrets import service as secret_service


class MemoryKeyring:
    """In-memory credential backend whose representation never exposes values."""

    priority = 1.0

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def __repr__(self) -> str:
        return "MemoryKeyring([REDACTED])"

    def get_keyring(self) -> MemoryKeyring:
        return self

    def get_password(self, service_name: str, username: str) -> str | None:
        return self._values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self._values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
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
def isolated_send_boundaries(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryKeyring]:
    for variable_name in tuple(os.environ):
        if variable_name.startswith("WORKCTX_SECRET_") or variable_name == "GITHUB_TOKEN":
            monkeypatch.delenv(variable_name, raising=False)

    memory = MemoryKeyring()
    names_index = MemoryNamesIndex()
    monkeypatch.setattr(_backend, "import_module", lambda _name: memory)
    monkeypatch.setattr(secret_service, "SecretNamesIndex", lambda: names_index)

    def refuse_real_network(
        _transport: httpx.HTTPTransport,
        _request: httpx.Request,
    ) -> httpx.Response:
        raise AssertionError("drafting tests must inject a mocked httpx transport")

    def refuse_real_gh(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("drafting tests must mock the gh auth token fallback")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_real_network)
    monkeypatch.setattr(delivery_runtime.subprocess, "run", refuse_real_gh)
    yield memory


@pytest.fixture
def memory_keyring(isolated_send_boundaries: MemoryKeyring) -> MemoryKeyring:
    return isolated_send_boundaries


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Avoid pytest's sandbox-hostile 0700 temporary directories."""

    parent = Path(tempfile.gettempdir()) / "workctx-drafting-tests"
    parent.mkdir(mode=0o755, exist_ok=True)
    root = parent / f"case-{uuid4().hex}"
    root.mkdir(mode=0o755)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
