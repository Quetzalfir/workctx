"""Optional Obsidian install and open helpers with injectable side-effect seams."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlencode

from workctx.errors import UnavailableDependencyError
from workctx.graph import GraphExportResult, GraphMap, export_graph, graph_vault_path
from workctx.obsidian.errors import ObsidianError
from workctx.obsidian.models import ObsidianInstallResult, ObsidianOpenResult

OBSIDIAN_DOWNLOAD_URL = "https://obsidian.md/download"


class ProcessRunner(Protocol):
    """Narrow process seam used by package managers and platform launchers."""

    def __call__(self, command: Sequence[str]) -> int: ...


class ObsidianInstaller:
    """Detect or explicitly install Obsidian on one supported platform."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        executable_finder: Callable[[str], str | None] = shutil.which,
        path_exists: Callable[[Path], bool] = Path.exists,
        environment: Mapping[str, str] | None = None,
        home: Path | None = None,
        process_runner: ProcessRunner | None = None,
    ) -> None:
        self._platform = _platform_name(sys.platform if platform is None else platform)
        self._find_executable = executable_finder
        self._path_exists = path_exists
        self._environment = os.environ if environment is None else environment
        self._home = Path.home() if home is None else home
        self._run = process_runner or _run_process

    def install(
        self,
        *,
        yes: bool = False,
        accept_agreements: bool = False,
    ) -> ObsidianInstallResult:
        """Return a plan by default and run the manager only after explicit ``yes``."""

        if self._is_installed():
            return ObsidianInstallResult(
                platform=self._platform,
                installed=True,
                manager=None,
                command=None,
                command_text=None,
                executed=False,
            )

        manager, command = self._install_command(accept_agreements=accept_agreements)
        if self._find_executable(manager) is None:
            raise UnavailableDependencyError(
                f"{manager} is not available; install Obsidian from {OBSIDIAN_DOWNLOAD_URL}."
            )
        command_text = " ".join(command)
        if not yes:
            return ObsidianInstallResult(
                platform=self._platform,
                installed=False,
                manager=manager,
                command=command,
                command_text=command_text,
                executed=False,
            )

        return_code = self._run(command)
        if return_code != 0:
            raise ObsidianError(
                "The Obsidian package-manager command failed; review the manager output or "
                f"download Obsidian from {OBSIDIAN_DOWNLOAD_URL}."
            )
        return ObsidianInstallResult(
            platform=self._platform,
            installed=True,
            manager=manager,
            command=command,
            command_text=command_text,
            executed=True,
        )

    def _is_installed(self) -> bool:
        executable_names = (
            ("Obsidian.exe", "obsidian") if self._platform == "windows" else ("obsidian",)
        )
        if any(self._find_executable(name) is not None for name in executable_names):
            return True
        return any(self._path_exists(path) for path in self._installed_paths())

    def _installed_paths(self) -> tuple[Path, ...]:
        if self._platform == "windows":
            local_app_data = self._environment.get("LOCALAPPDATA")
            return (
                ()
                if local_app_data is None
                else (
                    Path(local_app_data) / "Obsidian" / "Obsidian.exe",
                    Path(local_app_data) / "Programs" / "Obsidian" / "Obsidian.exe",
                )
            )
        if self._platform == "macos":
            return (
                Path("/Applications/Obsidian.app"),
                self._home / "Applications" / "Obsidian.app",
            )
        return (
            self._home / ".local/share/flatpak/app/md.obsidian.Obsidian",
            Path("/var/lib/flatpak/app/md.obsidian.Obsidian"),
        )

    def _install_command(self, *, accept_agreements: bool) -> tuple[str, tuple[str, ...]]:
        if self._platform == "windows":
            command = ["winget", "install", "--id", "Obsidian.Obsidian", "-e"]
            if accept_agreements:
                command.extend(("--accept-package-agreements", "--accept-source-agreements"))
            return "winget", tuple(command)
        if self._platform == "macos":
            return "brew", ("brew", "install", "--cask", "obsidian")
        return "flatpak", (
            "flatpak",
            "install",
            "-y",
            "flathub",
            "md.obsidian.Obsidian",
        )


class PlatformUriOpener:
    """Open a URI with the platform facility behind testable seams."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        process_runner: ProcessRunner | None = None,
        windows_startfile: Callable[[str], None] | None = None,
    ) -> None:
        self._platform = _platform_name(sys.platform if platform is None else platform)
        self._run = process_runner or _run_process
        self._startfile = windows_startfile or _windows_startfile

    def open(self, uri: str) -> None:
        """Open one URI without shell interpolation."""

        if self._platform == "windows":
            try:
                self._startfile(uri)
            except OSError as exc:
                raise ObsidianError("Windows could not open the Obsidian URI.") from exc
            return
        command = ("open", uri) if self._platform == "macos" else ("xdg-open", uri)
        if self._run(command) != 0:
            raise ObsidianError("The platform opener could not open the Obsidian URI.")


def install_obsidian(
    *,
    yes: bool = False,
    accept_agreements: bool = False,
    installer: ObsidianInstaller | None = None,
) -> ObsidianInstallResult:
    """Preview or execute the platform installation command."""

    return (installer or ObsidianInstaller()).install(
        yes=yes,
        accept_agreements=accept_agreements,
    )


def open_obsidian_map(
    root: Path,
    map_name: GraphMap | str,
    *,
    exporter: Callable[[Path], GraphExportResult] = export_graph,
    uri_opener: PlatformUriOpener | None = None,
) -> ObsidianOpenResult:
    """Ensure one generated vault exists, then open its encoded absolute path."""

    try:
        selected_map = map_name if isinstance(map_name, GraphMap) else GraphMap(map_name)
    except ValueError as exc:
        raise ObsidianError("Obsidian map must be 'topics' or 'references'.") from exc

    vaults = {map_value: graph_vault_path(root, map_value) for map_value in GraphMap}
    vault = vaults[selected_map]
    exported = False
    if not all(_vault_exists(path) for path in vaults.values()):
        exporter(root)
        exported = True
    if not _vault_exists(vault):
        raise ObsidianError("Graph export did not create the requested Obsidian vault.")
    try:
        absolute_vault = vault.resolve(strict=True)
    except OSError as exc:
        raise ObsidianError("The generated Obsidian vault path could not be resolved.") from exc
    uri = "obsidian://open?" + urlencode(
        {"path": str(absolute_vault)},
        quote_via=quote,
    )
    (uri_opener or PlatformUriOpener()).open(uri)
    return ObsidianOpenResult(
        map=selected_map,
        vault_path=str(absolute_vault),
        uri=uri,
        exported=exported,
    )


def _vault_exists(vault: Path) -> bool:
    obsidian_directory = vault / ".obsidian"
    app_config = obsidian_directory / "app.json"
    graph_config = obsidian_directory / "graph.json"
    if any(
        path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
        for path in (vault, obsidian_directory, app_config, graph_config)
    ):
        return False
    return vault.is_dir() and app_config.is_file() and graph_config.is_file()


def _platform_name(value: str) -> Literal["windows", "macos", "linux"]:
    normalized = value.casefold()
    if normalized.startswith("win"):
        return "windows"
    if normalized == "darwin":
        return "macos"
    if normalized.startswith("linux"):
        return "linux"
    raise UnavailableDependencyError(
        f"Obsidian helpers do not support platform {value!r}; use {OBSIDIAN_DOWNLOAD_URL}."
    )


def _run_process(command: Sequence[str]) -> int:
    try:
        completed = subprocess.run(
            tuple(command),
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise UnavailableDependencyError(
            f"The required platform command is unavailable; use {OBSIDIAN_DOWNLOAD_URL}."
        ) from exc
    return completed.returncode


def _windows_startfile(uri: str) -> None:
    startfile = getattr(os, "startfile", None)
    if startfile is None:  # pragma: no cover - guarded by runtime platform
        raise OSError("os.startfile is unavailable")
    startfile(uri)


__all__ = [
    "OBSIDIAN_DOWNLOAD_URL",
    "ObsidianInstaller",
    "PlatformUriOpener",
    "ProcessRunner",
    "install_obsidian",
    "open_obsidian_map",
]
