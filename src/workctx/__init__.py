"""Work Context OS package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("workctx")
except PackageNotFoundError:  # pragma: no cover - source tree fallback
    __version__ = "0.2.0a1"

__all__ = ["__version__"]
