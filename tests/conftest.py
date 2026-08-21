from __future__ import annotations

import hashlib
import io
import stat
from pathlib import Path

import pytest
from platformdirs import user_config_path as platform_user_config_path

import workctx.adapters.filesystem.registry as registry_module
import workctx.secrets._index as secret_index_module

_REAL_REGISTRY_PATH = (
    platform_user_config_path("workctx", appauthor=False) / registry_module.REGISTRY_FILENAME
)
_PATH_LSTAT = Path.lstat
_IO_OPEN = io.open


def _registry_canary(path: Path) -> tuple[object, ...]:
    """Capture content-free identity sufficient to detect a real-registry write."""

    try:
        metadata = _PATH_LSTAT(path)
    except FileNotFoundError:
        return (False,)
    except OSError as error:
        return ("unavailable", type(error).__name__)
    digest: str | None = None
    if stat.S_ISREG(metadata.st_mode):
        try:
            with _IO_OPEN(path, "rb") as stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
        except OSError as error:
            digest = type(error).__name__
    return (
        True,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        digest,
    )


@pytest.fixture(autouse=True)
def isolated_user_config_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Fence every test's default context registry inside its own temp tree."""

    real_before = _registry_canary(_REAL_REGISTRY_PATH)
    test_home = tmp_path / "user-home"
    user_config = test_home / "workctx"
    monkeypatch.setenv("APPDATA", str(test_home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(test_home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(test_home / ".config"))
    monkeypatch.setenv(
        "WORKCTX_CONTEXT_REGISTRY",
        str(user_config / registry_module.REGISTRY_FILENAME),
    )
    monkeypatch.setenv(
        secret_index_module.SECRET_INDEX_ENV,
        str(user_config / secret_index_module.INDEX_FILENAME),
    )
    monkeypatch.setattr(
        registry_module,
        "user_config_path",
        lambda *_args, **_kwargs: user_config,
    )
    monkeypatch.setattr(
        secret_index_module,
        "user_config_path",
        lambda *_args, **_kwargs: user_config,
    )

    yield user_config

    assert _registry_canary(_REAL_REGISTRY_PATH) == real_before, (
        "A test modified the operator's real context registry; all default registry "
        "access must use isolated_user_config_dir."
    )
