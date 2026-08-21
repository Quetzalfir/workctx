"""The secret names index default path must honor its environment override."""

from __future__ import annotations

from pathlib import Path

import pytest

import workctx.secrets._index as index_module
from workctx.secrets._index import SECRET_INDEX_ENV, SecretNamesIndex
from workctx.secrets.errors import SecretIndexError


def test_default_index_path_honors_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_file = tmp_path / "subprocess-fence" / "secret-names.json"
    monkeypatch.setenv(SECRET_INDEX_ENV, str(index_file))

    def forbidden_fallback(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("environment override fell through to platformdirs")

    monkeypatch.setattr(index_module, "user_config_path", forbidden_fallback)

    assert SecretNamesIndex().path == index_file.absolute()


@pytest.mark.parametrize("override", ["", "   ", "relative/secret-names.json"])
def test_empty_or_relative_index_override_is_rejected(
    override: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SECRET_INDEX_ENV, override)

    with pytest.raises(SecretIndexError):
        SecretNamesIndex()


def test_explicit_constructor_path_beats_the_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SECRET_INDEX_ENV, str(tmp_path / "environment" / "names.json"))
    explicit = tmp_path / "explicit" / "names.json"

    assert SecretNamesIndex(explicit).path == explicit
