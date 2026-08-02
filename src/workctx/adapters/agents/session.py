"""Session bootstrap for opening an isolated project in a selected client."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from workctx.errors import UnavailableDependencyError

from .detection import ClientDetector, ExecutableFinder, VersionProbe, _default_version_probe
from .errors import UnsupportedClientVersionError
from .models import AgentClient, ClientAvailability, OpenedContext


class SpawnedProcess(Protocol):
    """Small process surface needed by the bootstrap API."""

    pid: int


ProcessSpawner = Callable[..., SpawnedProcess]


def _default_spawner(arguments: list[str], *, cwd: Path, shell: bool) -> SpawnedProcess:
    return subprocess.Popen(arguments, cwd=cwd, shell=shell)


def open_context(
    root: Path,
    client: AgentClient,
    *,
    executable_finder: ExecutableFinder = shutil.which,
    version_probe: VersionProbe = _default_version_probe,
    spawner: ProcessSpawner = _default_spawner,
) -> OpenedContext:
    """Spawn the discovered executable with the selected project as its working directory.

    The subprocess inherits the caller's streams and environment. This function deliberately
    neither supplies an environment mapping nor reads client configuration or credentials.
    """

    physical_root = root.resolve(strict=True)
    capability = ClientDetector(
        executable_finder=executable_finder,
        version_probe=version_probe,
    ).detect_one(physical_root, client)
    if capability.availability is ClientAvailability.UNSUPPORTED:
        raise UnsupportedClientVersionError(
            capability.detail or f"Unsupported {client.value} client version"
        )
    if not capability.can_open or capability.executable is None:
        raise UnavailableDependencyError(
            f"The {client.value} executable is not available on PATH for this project."
        )
    try:
        process = spawner([capability.executable], cwd=physical_root, shell=False)
    except FileNotFoundError as error:
        raise UnavailableDependencyError(
            f"The {client.value} executable became unavailable before launch."
        ) from error
    return OpenedContext(
        client=client,
        root=physical_root,
        executable=capability.executable,
        pid=process.pid,
    )
