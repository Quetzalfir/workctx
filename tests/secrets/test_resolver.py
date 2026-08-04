from __future__ import annotations

import json
from pathlib import Path

import pytest

from workctx.secrets import (
    SecretBackendUnavailableError,
    SecretIndexError,
    SecretLayer,
    SecretNotFoundError,
    SecretRef,
    _backend,
    delete,
    env_var_name,
    exists,
    inspect_presence,
    list_names,
    resolution_layer,
    resolve,
    store,
)
from workctx.secrets._index import SecretNamesIndex

from .conftest import MemoryKeyring

OS_VALUE = "fictional-os-store-material"
ENV_VALUE = "fictional-environment-material"


def test_resolver_prefers_environment_over_os_store(
    memory_keyring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store("service-token", OS_VALUE)
    monkeypatch.setenv(env_var_name("service-token"), ENV_VALUE)

    resolved = resolve("service-token")

    assert resolved.reveal() == ENV_VALUE
    assert resolution_layer("service-token") is SecretLayer.ENVIRONMENT
    assert not any(call[0] == "get" for call in memory_keyring.calls)


def test_resolver_uses_os_store_after_environment_miss(
    memory_keyring: MemoryKeyring,
) -> None:
    store("service-token", OS_VALUE)

    assert resolve("service-token").reveal() == OS_VALUE
    assert resolution_layer("service-token") is SecretLayer.OS_STORE
    assert ("get", "workctx", "service-token") in memory_keyring.calls


def test_environment_fallback_works_when_keyring_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(env_var_name("ci-token"), ENV_VALUE)

    def missing_keyring(_name: str) -> object:
        raise ImportError("fictional missing dependency")

    monkeypatch.setattr(_backend, "import_module", missing_keyring)

    assert resolve("ci-token").reveal() == ENV_VALUE
    assert resolution_layer("ci-token") is SecretLayer.ENVIRONMENT


def test_missing_keyring_has_content_free_diagnostic_after_environment_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_keyring(_name: str) -> object:
        raise ImportError("fictional backend detail")

    monkeypatch.setattr(_backend, "import_module", missing_keyring)

    with pytest.raises(SecretBackendUnavailableError) as caught:
        resolve("ci-token")

    assert "fictional backend detail" not in str(caught.value)
    assert caught.value.__context__ is None


def test_missing_secret_error_names_only_the_reference() -> None:
    with pytest.raises(SecretNotFoundError) as caught:
        resolve("missing-token")

    assert caught.value.ref_name == "missing-token"
    assert str(caught.value) == "Secret reference 'missing-token' was not found."
    assert not exists("missing-token")


def test_store_list_presence_and_delete_use_names_only_index(
    memory_keyring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store("stored-token", OS_VALUE)
    monkeypatch.setenv(env_var_name("environment-token"), ENV_VALUE)

    assert list_names() == ("environment-token", "stored-token")
    assert inspect_presence("stored-token").os_store is True
    assert inspect_presence("environment-token").environment is True
    assert memory_keyring.contains("stored-token")

    assert delete("stored-token") is True
    assert delete("stored-token") is False
    assert not memory_keyring.contains("stored-token")
    assert list_names() == ("environment-token",)


def test_backend_exception_text_cannot_escape_store(
    memory_keyring: MemoryKeyring,
) -> None:
    backend_detail = "fictional-backend-detail-containing-material"
    memory_keyring.failure_message = backend_detail

    with pytest.raises(SecretBackendUnavailableError) as caught:
        store("service-token", OS_VALUE)

    assert backend_detail not in str(caught.value)
    assert OS_VALUE not in str(caught.value)
    assert caught.value.__context__ is None


def test_names_index_persists_names_without_values(tmp_path: Path) -> None:
    index_path = tmp_path / "isolated" / "secret-names.json"
    index = SecretNamesIndex(index_path)

    index.add(SecretRef("stored-token"))

    index_text = index_path.read_text(encoding="utf-8")
    index_payload = json.loads(index_text)
    assert index_payload["names"] == ["stored-token"]
    assert OS_VALUE not in index_text


def test_names_index_rejects_value_bearing_or_malformed_content(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "isolated" / "secret-names.json"
    index_path.parent.mkdir()
    index_path.write_text(
        json.dumps({"schema_version": 1, "names": ["stored-token"], "value": OS_VALUE}),
        encoding="utf-8",
    )
    with pytest.raises(SecretIndexError) as caught:
        SecretNamesIndex(index_path).list()

    assert OS_VALUE not in str(caught.value)
