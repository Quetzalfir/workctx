from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass


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
