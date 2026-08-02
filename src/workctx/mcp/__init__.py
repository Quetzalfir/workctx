"""Versioned, context-bound MCP boundary for Work Context OS.

Importing this package never imports the optional official MCP SDK.  Only the
``serve_stdio`` call crosses that dependency boundary.
"""

from workctx.mcp.application import McpToolService
from workctx.mcp.contracts import SCHEMA_VERSION, TOOL_CONTRACTS
from workctx.mcp.resources import McpResourceService
from workctx.mcp.runner import serve_stdio

__all__ = [
    "SCHEMA_VERSION",
    "TOOL_CONTRACTS",
    "McpResourceService",
    "McpToolService",
    "serve_stdio",
]
