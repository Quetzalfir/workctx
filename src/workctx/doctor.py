from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

_CONTAINER_REDIRECT = re.compile(r"[\\/]Packages[\\/][^\\/]+[\\/]LocalCache[\\/]", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    required: bool


def run_doctor() -> list[DoctorCheck]:
    checks = [
        DoctorCheck(
            name="python",
            status="ok" if sys.version_info >= (3, 12) else "error",
            detail=platform.python_version(),
            required=True,
        ),
        _executable_check("git", required=True),
        _executable_check("uv", required=False),
        _executable_check("codex", required=False),
        _executable_check("claude", required=False),
        _executable_check("gemini", required=False),
        _user_config_virtualization_check(),
    ]
    return checks


def _executable_check(name: str, *, required: bool) -> DoctorCheck:
    path = shutil.which(name)
    if path:
        return DoctorCheck(name=name, status="ok", detail=path, required=required)
    return DoctorCheck(
        name=name,
        status="error" if required else "optional_missing",
        detail="not found on PATH",
        required=required,
    )


def _user_config_virtualization_check() -> DoctorCheck:
    """Detect app-container redirection of the machine-global user-config directory.

    Windows MSIX app containers copy-on-write files under the user profile's
    AppData tree into a per-application shadow. A workctx process running
    inside such a container then reads and writes stale per-app copies of
    machine-global state — the secret names index, the context registry, and
    the trusted install records — that silently diverge from the operator's
    real files, while OS-keyring lookups keep working. Surfacing the redirect
    turns that silent inconsistency into a diagnosed one.
    """

    literal = user_config_path("workctx", appauthor=False)
    if _CONTAINER_REDIRECT.search(str(literal)):
        return DoctorCheck(
            name="user-config-path", status="ok", detail=str(literal), required=False
        )
    # Container copy-on-write redirection is only observable per file: shadowed
    # files resolve into the package layer while the directory itself resolves
    # normally, so each machine-global state file must be probed individually.
    redirected: list[str] = []
    for filename in ("secret-names.json", "contexts.json", "agent-adapter-installs.json"):
        candidate = literal / filename
        resolved = _resolve_existing(candidate)
        if resolved is not None and _CONTAINER_REDIRECT.search(str(resolved)):
            redirected.append(filename)
    directory_resolved = _resolve_existing(literal)
    if directory_resolved is not None and _CONTAINER_REDIRECT.search(str(directory_resolved)):
        redirected.append(".")
    if not redirected:
        return DoctorCheck(
            name="user-config-path", status="ok", detail=str(literal), required=False
        )
    names = ", ".join(sorted(set(redirected)))
    return DoctorCheck(
        name="user-config-path",
        status="warning",
        detail=(
            f"{literal} is app-container virtualized ({names} resolve into a "
            "Packages\\...\\LocalCache shadow); machine-global state read here — the "
            "secret names index, the context registry, and the trusted install "
            "records — can be a stale per-app copy that diverges from the "
            "operator's real files while OS-keyring lookups keep working. Run "
            "workctx from a non-containerized shell, or remove the application's "
            "shadow copies of the workctx user-config directory."
        ),
        required=False,
    )


def _resolve_existing(path: Path) -> Path | None:
    try:
        if not path.exists():
            return None
        return Path(os.path.realpath(path))
    except OSError:
        return None
