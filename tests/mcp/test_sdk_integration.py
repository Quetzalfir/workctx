from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TextIO

import pytest

from workctx.services.contexts import initialize_context

# Pytest's default import mode names this local test package ``mcp``. Temporarily
# remove that package and the tests directory so importorskip resolves the SDK.
_LOCAL_TEST_PACKAGE = sys.modules.pop("mcp", None)
_ORIGINAL_SYS_PATH = sys.path[:]
_TESTS_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _TESTS_DIRECTORY]
try:
    mcp_sdk = pytest.importorskip(
        "mcp",
        reason="the optional workctx[mcp] extra is required for the stdio SDK integration test",
    )
    ClientSession = mcp_sdk.ClientSession
    StdioServerParameters = mcp_sdk.StdioServerParameters
    stdio_client = mcp_sdk.stdio_client
finally:
    sys.path[:] = _ORIGINAL_SYS_PATH
    if _LOCAL_TEST_PACKAGE is not None:
        sys.modules["mcp"] = _LOCAL_TEST_PACKAGE

anyio = pytest.importorskip(
    "anyio",
    reason="the optional workctx[mcp] extra must include its anyio dependency",
)


READ_TOOLS = (
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
)
MUTATION_TOOLS = (
    "artifact_register",
    "proposal_validate",
    "transaction_dry_run",
    "transaction_apply",
    "index_rebuild",
    "draft_save",
)


@pytest.mark.integration
def test_official_sdk_stdio_lifecycle_and_discovery(tmp_path: Path) -> None:
    context_root = tmp_path / "stdio-context"
    initialize_context(
        context_root,
        name="MCP stdio lifecycle",
        context_id="mcp-stdio",
    )
    stderr_path = tmp_path / "mcp-server.stderr.log"

    with stderr_path.open("w", encoding="utf-8") as errlog:
        anyio.run(_exercise_server, context_root, errlog)


async def _exercise_server(context_root: Path, errlog: TextIO) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "workctx",
            "mcp",
            "serve",
            "--context",
            str(context_root),
        ],
        cwd=repository_root,
        # Inherit the test process environment so isolation fences such as
        # WORKCTX_CONTEXT_REGISTRY reach the server; the SDK default is a
        # minimal allowlist that silently drops them.
        env=dict(os.environ),
    )

    with anyio.fail_after(30):
        async with stdio_client(parameters, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.server_info.name == "workctx"
                assert initialized.capabilities.tools is not None
                assert initialized.capabilities.resources is not None
                assert initialized.capabilities.prompts is None

                discovery = await session.list_tools()
                assert tuple(tool.name for tool in discovery.tools) == READ_TOOLS + MUTATION_TOOLS
                for tool in discovery.tools:
                    assert tool.input_schema["properties"]["schema_version"]["const"] == 1
                    assert tool.output_schema["properties"]["schema_version"]["const"] == 1
                    assert tool.annotations is not None
                    if tool.name in MUTATION_TOOLS:
                        assert "approved" in tool.input_schema["required"]
                        assert tool.input_schema["properties"]["approved"]["const"] is True
                        assert tool.annotations.read_only_hint is False
                    else:
                        assert tool.annotations.read_only_hint is True

                result = await session.call_tool("context_info", {"schema_version": 1})
                assert result.is_error is False
                assert isinstance(result.structured_content, dict)
                assert result.structured_content["schema_version"] == 1
                assert result.structured_content["ok"] is True
                assert result.structured_content["context_id"] == "mcp-stdio"

                resources = await session.list_resources()
                assert len(resources.resources) == 1
                configuration_uri = "workctx://mcp-stdio/context/configuration"
                assert str(resources.resources[0].uri) == configuration_uri

                templates = await session.list_resource_templates()
                assert len(templates.resource_templates) == 1
                assert (
                    templates.resource_templates[0].uri_template
                    == "workctx://mcp-stdio/{entity_type}/{entity_id}"
                )

                resource = await session.read_resource(configuration_uri)
                assert len(resource.contents) == 1
                payload = json.loads(resource.contents[0].text)
                assert payload["schema_version"] == 1
                assert payload["context_id"] == "mcp-stdio"
                assert payload["kind"] == "context_configuration"
