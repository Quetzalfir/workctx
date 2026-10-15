"""Optional Obsidian installation and generated-vault helpers."""

from workctx.graph import GraphMap
from workctx.obsidian.errors import ObsidianError
from workctx.obsidian.models import ObsidianInstallResult, ObsidianOpenResult
from workctx.obsidian.service import (
    OBSIDIAN_DOWNLOAD_URL,
    ObsidianInstaller,
    PlatformUriOpener,
    ProcessRunner,
    install_obsidian,
    open_obsidian_map,
)

__all__ = [
    "OBSIDIAN_DOWNLOAD_URL",
    "GraphMap",
    "ObsidianError",
    "ObsidianInstallResult",
    "ObsidianInstaller",
    "ObsidianOpenResult",
    "PlatformUriOpener",
    "ProcessRunner",
    "install_obsidian",
    "open_obsidian_map",
]
