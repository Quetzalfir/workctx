"""Credential-blind discovery of supported project-local agent clients."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from ._safe_fs import SafeFilesystemError, SafeRoot
from .models import (
    AgentClient,
    ClientAvailability,
    ClientCapability,
    SemanticVersion,
    SupportedVersionRange,
)

ExecutableFinder = Callable[[str], str | None]
VersionProbe = Callable[[str, Path], str]

SUPPORTED_VERSION_RANGES: dict[AgentClient, SupportedVersionRange] = {
    AgentClient.CODEX: SupportedVersionRange(SemanticVersion(0, 1, 0), SemanticVersion(1, 0, 0)),
    AgentClient.CLAUDE: SupportedVersionRange(SemanticVersion(1, 0, 0), SemanticVersion(3, 0, 0)),
    AgentClient.GEMINI: SupportedVersionRange(SemanticVersion(0, 1, 0), SemanticVersion(1, 0, 0)),
}

PROJECT_MARKERS: dict[AgentClient, tuple[str, ...]] = {
    AgentClient.CODEX: (
        "AGENTS.md",
        ".agents/skills",
        ".codex/config.toml",
        ".codex/config.local.toml",
    ),
    AgentClient.CLAUDE: ("CLAUDE.md", ".claude/skills", ".mcp.json"),
    AgentClient.GEMINI: (
        "GEMINI.md",
        ".gemini/skills",
        ".gemini/commands",
        ".gemini/settings.json",
    ),
}

_VERSION_RE = re.compile(r"(?<![0-9])[vV]?([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})(?![0-9])")


def parse_client_version(output: str) -> SemanticVersion | None:
    """Parse the first complete three-part version without interpreting other text."""

    match = _VERSION_RE.search(output)
    if match is None:
        return None
    try:
        return SemanticVersion(*(int(value) for value in match.groups()))
    except ValueError:
        return None


def _default_version_probe(executable: str, root: Path) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        cwd=root,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            [executable, "--version"],
        )
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part)


def _marker_state(root: Path, relative: str) -> tuple[bool, bool]:
    """Return ``(present, unsafe)`` using metadata only, never marker contents."""

    try:
        return SafeRoot(root).inspect_entry(relative) is not None, False
    except (OSError, SafeFilesystemError):
        return False, True


class ClientDetector:
    """Discover clients through injected executable and version probes."""

    def __init__(
        self,
        *,
        executable_finder: ExecutableFinder = shutil.which,
        version_probe: VersionProbe = _default_version_probe,
    ) -> None:
        self._find = executable_finder
        self._probe = version_probe

    def detect(
        self,
        project_root: Path,
        clients: Sequence[AgentClient] | None = None,
    ) -> tuple[ClientCapability, ...]:
        """Report clients independently and avoid all user-global configuration reads."""

        root = project_root.resolve(strict=True)
        selected = tuple(clients) if clients is not None else tuple(AgentClient)
        return tuple(self.detect_one(root, client) for client in selected)

    def detect_one(self, project_root: Path, client: AgentClient) -> ClientCapability:
        root = project_root.resolve(strict=True)
        markers: list[str] = []
        unsafe_markers: list[str] = []
        for marker in PROJECT_MARKERS[client]:
            present, unsafe = _marker_state(root, marker)
            if present:
                markers.append(marker)
            if unsafe:
                unsafe_markers.append(marker)

        supported_range = SUPPORTED_VERSION_RANGES[client]
        if unsafe_markers:
            return ClientCapability(
                client=client,
                availability=ClientAvailability.INVALID,
                executable=None,
                version=None,
                supported_range=supported_range,
                project_markers=tuple(markers),
                detail="Unsafe project marker path(s): " + ", ".join(unsafe_markers),
            )

        executable = self._find(client.value)
        if executable is None:
            availability = (
                ClientAvailability.CONFIGURED_ONLY if markers else ClientAvailability.MISSING
            )
            return ClientCapability(
                client=client,
                availability=availability,
                executable=None,
                version=None,
                supported_range=supported_range,
                project_markers=tuple(markers),
            )

        try:
            output = self._probe(executable, root)
        except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as error:
            return ClientCapability(
                client=client,
                availability=ClientAvailability.UNSUPPORTED,
                executable=executable,
                version=None,
                supported_range=supported_range,
                project_markers=tuple(markers),
                detail=f"Version probe failed safely: {type(error).__name__}",
            )
        version = parse_client_version(output)
        if version is None:
            return ClientCapability(
                client=client,
                availability=ClientAvailability.UNSUPPORTED,
                executable=executable,
                version=None,
                supported_range=supported_range,
                project_markers=tuple(markers),
                detail="Client version output did not contain a supported semantic version.",
            )
        availability = (
            ClientAvailability.AVAILABLE
            if supported_range.contains(version)
            else ClientAvailability.UNSUPPORTED
        )
        detail = None
        if availability is ClientAvailability.UNSUPPORTED:
            detail = f"Detected {version}; supported range is {supported_range}."
        return ClientCapability(
            client=client,
            availability=availability,
            executable=executable,
            version=version,
            supported_range=supported_range,
            project_markers=tuple(markers),
            detail=detail,
        )


def detect_clients(
    project_root: Path,
    *,
    executable_finder: ExecutableFinder = shutil.which,
    version_probe: VersionProbe = _default_version_probe,
) -> tuple[ClientCapability, ...]:
    """Convenience typed API for independent client detection."""

    return ClientDetector(executable_finder=executable_finder, version_probe=version_probe).detect(
        project_root
    )


def detect_client(
    project_root: Path,
    client: AgentClient,
    *,
    executable_finder: ExecutableFinder = shutil.which,
    version_probe: VersionProbe = _default_version_probe,
) -> ClientCapability:
    """Convenience typed API for one client."""

    return ClientDetector(
        executable_finder=executable_finder, version_probe=version_probe
    ).detect_one(project_root, client)
