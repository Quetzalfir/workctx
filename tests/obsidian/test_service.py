from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from graph.support import NOW

from workctx.errors import UnavailableDependencyError
from workctx.graph import GraphMap, export_graph
from workctx.obsidian import (
    OBSIDIAN_DOWNLOAD_URL,
    ObsidianInstaller,
    PlatformUriOpener,
    open_obsidian_map,
)
from workctx.services.contexts import initialize_context


@pytest.mark.parametrize(
    ("platform", "manager", "expected"),
    [
        ("win32", "winget", ("winget", "install", "--id", "Obsidian.Obsidian", "-e")),
        ("darwin", "brew", ("brew", "install", "--cask", "obsidian")),
        (
            "linux",
            "flatpak",
            ("flatpak", "install", "-y", "flathub", "md.obsidian.Obsidian"),
        ),
    ],
)
def test_install_preview_and_yes_gate_every_platform(
    platform: str,
    manager: str,
    expected: tuple[str, ...],
) -> None:
    calls: list[tuple[str, ...]] = []

    def find_executable(name: str) -> str | None:
        return f"/fake/{name}" if name == manager else None

    installer = ObsidianInstaller(
        platform=platform,
        executable_finder=find_executable,
        path_exists=lambda _path: False,
        environment={},
        home=Path("C:/fictional-home"),
        process_runner=lambda command: calls.append(tuple(command)) or 0,
    )

    preview = installer.install()

    assert preview.command == expected
    assert preview.command_text == " ".join(expected)
    assert preview.executed is False
    assert preview.installed is False
    assert calls == []

    installed = installer.install(yes=True)

    assert installed.command == expected
    assert installed.executed is True
    assert installed.installed is True
    assert calls == [expected]


def test_windows_agreement_flags_require_explicit_option() -> None:
    calls: list[tuple[str, ...]] = []
    installer = ObsidianInstaller(
        platform="win32",
        executable_finder=lambda name: "C:/fake/winget.exe" if name == "winget" else None,
        path_exists=lambda _path: False,
        environment={},
        home=Path("C:/fictional-home"),
        process_runner=lambda command: calls.append(tuple(command)) or 0,
    )

    without_agreements = installer.install(yes=True)
    with_agreements = installer.install(yes=True, accept_agreements=True)

    flags = {"--accept-package-agreements", "--accept-source-agreements"}
    assert without_agreements.command is not None
    assert flags.isdisjoint(without_agreements.command)
    assert with_agreements.command is not None
    assert flags.issubset(with_agreements.command)
    assert calls == [without_agreements.command, with_agreements.command]


def test_existing_install_and_missing_manager_never_run_a_process() -> None:
    def reject_process(_command: Sequence[str]) -> int:
        raise AssertionError("process execution was not expected")

    installed = ObsidianInstaller(
        platform="linux",
        executable_finder=lambda name: "/usr/bin/obsidian" if name == "obsidian" else None,
        path_exists=lambda _path: False,
        home=Path("/fictional-home"),
        process_runner=reject_process,
    ).install(yes=True)

    assert installed.installed is True
    assert installed.command is None
    assert installed.executed is False

    missing = ObsidianInstaller(
        platform="linux",
        executable_finder=lambda _name: None,
        path_exists=lambda _path: False,
        home=Path("/fictional-home"),
        process_runner=reject_process,
    )
    with pytest.raises(UnavailableDependencyError, match=OBSIDIAN_DOWNLOAD_URL):
        missing.install(yes=True)


@pytest.mark.parametrize(
    ("platform", "expected_program"),
    [("darwin", "open"), ("linux", "xdg-open")],
)
def test_process_uri_openers_are_injectable(
    platform: str,
    expected_program: str,
) -> None:
    calls: list[tuple[str, ...]] = []
    opener = PlatformUriOpener(
        platform=platform,
        process_runner=lambda command: calls.append(tuple(command)) or 0,
    )

    opener.open("obsidian://open?path=fake")

    assert calls == [(expected_program, "obsidian://open?path=fake")]


def test_windows_uri_opener_uses_injected_startfile() -> None:
    calls: list[str] = []
    opener = PlatformUriOpener(
        platform="win32",
        process_runner=lambda _command: (_ for _ in ()).throw(
            AssertionError("Windows must not run a process command")
        ),
        windows_startfile=calls.append,
    )

    opener.open("obsidian://open?path=fake")

    assert calls == ["obsidian://open?path=fake"]


def test_open_exports_missing_vault_and_encodes_unicode_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / "context with space ñ"
    initialize_context(root, name="Unicode Vault", context_id="unicode-vault")
    calls: list[tuple[str, ...]] = []
    opener = PlatformUriOpener(
        platform="linux",
        process_runner=lambda command: calls.append(tuple(command)) or 0,
    )

    opened = open_obsidian_map(
        root,
        GraphMap.REFERENCES,
        exporter=lambda path: export_graph(path, clock=lambda: NOW),
        uri_opener=opener,
    )

    expected_path = str((root / "04_views" / "graph" / "reference-map").resolve())
    assert opened.exported is True
    assert opened.vault_path == expected_path
    assert "%20" in opened.uri
    assert "%C3%B1" in opened.uri
    assert parse_qs(urlparse(opened.uri).query) == {"path": [expected_path]}
    assert calls == [("xdg-open", opened.uri)]

    repeated = open_obsidian_map(
        root,
        "references",
        exporter=lambda _path: (_ for _ in ()).throw(
            AssertionError("an existing vault must not be exported")
        ),
        uri_opener=opener,
    )
    assert repeated.exported is False
