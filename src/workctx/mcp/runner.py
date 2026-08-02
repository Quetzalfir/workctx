"""Lazy optional-dependency loader for ``workctx mcp serve``."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from workctx.errors import UnavailableDependencyError

_UNAVAILABLE_MESSAGE = (
    "MCP support is unavailable. Install the optional dependency with `pip install workctx[mcp]` "
    "or `uv sync --extra mcp`."
)


class _ServerRunner(Protocol):
    def __call__(self, context_root: Path) -> None: ...


def serve_stdio(context_root: Path) -> None:
    """Load the official SDK only for the MCP serve command, then run stdio."""

    _load_server_runner()(context_root)


def _load_server_runner() -> _ServerRunner:
    try:
        import_module("mcp")
    except ModuleNotFoundError as exc:
        raise UnavailableDependencyError(_UNAVAILABLE_MESSAGE) from exc

    try:
        module = import_module("workctx.mcp.server")
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing != "anyio" and missing != "mcp" and not missing.startswith("mcp."):
            raise
        raise UnavailableDependencyError(_UNAVAILABLE_MESSAGE) from exc
    runner = getattr(module, "serve_stdio_with_sdk", None)
    if not callable(runner):
        raise UnavailableDependencyError(_UNAVAILABLE_MESSAGE)
    return cast(_ServerRunner, runner)


__all__ = ["serve_stdio"]
