from __future__ import annotations

import pytest

from workctx.adapters.agents import _install_records


@pytest.fixture(autouse=True)
def isolate_user_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every adapter test isolated from operator-global configuration."""

    fake_home = tmp_path_factory.mktemp("agent-home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOMEDRIVE", fake_home.drive or "C:")
    monkeypatch.setenv("HOMEPATH", str(fake_home))
    monkeypatch.setenv("APPDATA", str(fake_home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))
    monkeypatch.setattr(
        _install_records,
        "user_config_path",
        lambda *_args, **_kwargs: fake_home / "AppData" / "Local" / "workctx",
    )
