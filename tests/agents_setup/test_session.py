from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from workctx.adapters.agents.errors import UnsupportedClientVersionError
from workctx.adapters.agents.models import AgentClient
from workctx.adapters.agents.session import open_context
from workctx.errors import UnavailableDependencyError


@dataclass(frozen=True)
class _FakeProcess:
    pid: int


def _finder(executables: dict[str, str]) -> Callable[[str], str | None]:
    return executables.get


def test_open_context_spawns_selected_detected_client_in_project(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(arguments: list[str], **kwargs: Any) -> _FakeProcess:
        calls.append((arguments, kwargs))
        return _FakeProcess(pid=4312)

    opened = open_context(
        tmp_path,
        AgentClient.GEMINI,
        executable_finder=_finder({"gemini": "fake-gemini"}),
        version_probe=lambda _executable, _root: "gemini 0.9.2",
        spawner=spawn,
    )

    assert opened.client is AgentClient.GEMINI
    assert opened.root == tmp_path.resolve()
    assert opened.executable == "fake-gemini"
    assert opened.pid == 4312
    assert calls == [
        (
            ["fake-gemini"],
            {"cwd": tmp_path.resolve(), "shell": False},
        )
    ]
    assert "env" not in calls[0][1]


def test_open_context_fails_clearly_when_client_is_missing(tmp_path: Path) -> None:
    spawn_called = False

    def spawn(_arguments: list[str], **_kwargs: Any) -> _FakeProcess:
        nonlocal spawn_called
        spawn_called = True
        return _FakeProcess(pid=1)

    with pytest.raises(UnavailableDependencyError, match="not available on PATH"):
        open_context(
            tmp_path,
            AgentClient.CLAUDE,
            executable_finder=lambda _name: None,
            version_probe=lambda _executable, _root: "2.0.0",
            spawner=spawn,
        )

    assert not spawn_called


def test_open_context_rejects_unsupported_client_before_spawn(tmp_path: Path) -> None:
    spawn_called = False

    def spawn(_arguments: list[str], **_kwargs: Any) -> _FakeProcess:
        nonlocal spawn_called
        spawn_called = True
        return _FakeProcess(pid=1)

    with pytest.raises(UnsupportedClientVersionError, match="supported range"):
        open_context(
            tmp_path,
            AgentClient.CODEX,
            executable_finder=_finder({"codex": "fake-codex"}),
            version_probe=lambda _executable, _root: "codex 9.0.0",
            spawner=spawn,
        )

    assert not spawn_called


def test_open_context_maps_executable_disappearance_to_dependency_error(tmp_path: Path) -> None:
    def disappear(_arguments: list[str], **_kwargs: Any) -> _FakeProcess:
        raise FileNotFoundError("fake executable disappeared")

    with pytest.raises(UnavailableDependencyError, match="became unavailable"):
        open_context(
            tmp_path,
            AgentClient.CLAUDE,
            executable_finder=_finder({"claude": "fake-claude"}),
            version_probe=lambda _executable, _root: "claude 2.0.0",
            spawner=disappear,
        )
