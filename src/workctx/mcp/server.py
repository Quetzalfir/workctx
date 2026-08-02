"""Official MCP SDK adapter for the context-bound application services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from workctx import __version__
from workctx.mcp.application import McpToolService
from workctx.mcp.contracts import TOOL_CONTRACTS
from workctx.mcp.models import ResourceAccessError
from workctx.mcp.resources import McpResourceService


@asynccontextmanager
async def _lifespan(_server: Server[None]) -> AsyncIterator[None]:
    yield None


def create_server(context_root: Path) -> Server[None]:
    """Create the low-level official-SDK server bound to one context root."""

    tools = McpToolService(context_root)
    resources = McpResourceService(tools.context_root)

    async def list_tools(
        _context: ServerRequestContext[None, object],
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=contract.name,
                    description=contract.description,
                    input_schema=contract.copied_input_schema(),
                    output_schema=contract.copied_output_schema(),
                    annotations=types.ToolAnnotations(
                        read_only_hint=not contract.mutation,
                        destructive_hint=contract.name == "transaction_apply",
                        idempotent_hint=contract.name
                        in {
                            "context_info",
                            "workspace_validate",
                            "search",
                            "ref_show",
                            "ref_related",
                            "ref_trace",
                            "context_pack",
                            "task_list",
                            "task_show",
                            "inbox_list",
                            "audit_summary",
                            "proposal_validate",
                            "transaction_dry_run",
                            "index_rebuild",
                        },
                        open_world_hint=False,
                    ),
                )
                for contract in TOOL_CONTRACTS
            ]
        )

    async def call_tool(
        _context: ServerRequestContext[None, object],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        response = tools.invoke(params.name, params.arguments or {})
        structured = response.envelope.model_dump(mode="json")
        return types.CallToolResult(
            content=[types.TextContent(text=response.summary)],
            structured_content=structured,
            is_error=response.is_error,
        )

    async def list_resources(
        _context: ServerRequestContext[None, object],
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=resource.uri,
                    name=resource.name,
                    title=resource.title,
                    description=resource.description,
                    mime_type=resource.mime_type,
                )
                for resource in resources.list_resources()
            ]
        )

    async def list_resource_templates(
        _context: ServerRequestContext[None, object],
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(
            resource_templates=[
                types.ResourceTemplate(
                    uri_template=template.uri_template,
                    name=template.name,
                    title=template.title,
                    description=template.description,
                    mime_type=template.mime_type,
                )
                for template in resources.list_templates()
            ]
        )

    async def read_resource(
        _context: ServerRequestContext[None, object],
        params: types.ReadResourceRequestParams,
    ) -> types.ReadResourceResult:
        try:
            content = resources.read(params.uri)
        except ResourceAccessError as exc:
            raise MCPError(
                code=types.INVALID_PARAMS,
                message=exc.diagnostic.message,
                data=exc.diagnostic.model_dump(mode="json"),
            ) from None
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=content.uri,
                    mime_type=content.mime_type,
                    text=content.text,
                )
            ]
        )

    return Server[None](
        "workctx",
        version=__version__,
        title="Work Context OS",
        description="Context-bound local Work Context MCP server.",
        lifespan=_lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_list_resource_templates=list_resource_templates,
        on_read_resource=read_resource,
    )


async def run_stdio_server(context_root: Path) -> None:
    """Run one server over stdio until the client closes its input stream."""

    server = create_server(context_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def serve_stdio_with_sdk(context_root: Path) -> None:
    """Synchronous entry point imported only after the optional SDK is available."""

    anyio.run(run_stdio_server, context_root)


__all__ = ["create_server", "run_stdio_server", "serve_stdio_with_sdk"]
