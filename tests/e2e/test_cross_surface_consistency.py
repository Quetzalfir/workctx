from __future__ import annotations

import json
from pathlib import Path

import pytest

from .support import (
    OBSERVATION_URI,
    QUESTION,
    TASK_URI,
    create_operational_context,
    invoke_envelope,
    invoke_mcp,
)

pytestmark = [pytest.mark.acceptance, pytest.mark.integration]


def test_cli_context_pack_and_mcp_return_consistent_references_for_one_question(
    tmp_path: Path,
) -> None:
    operational = create_operational_context(tmp_path / "cross-surface")
    root = operational.root

    cli_search = invoke_envelope(
        ["search", QUESTION, "--context", str(root), "--json"],
        command="search",
    )["result"]
    cli_pack = invoke_envelope(
        [
            "context-pack",
            TASK_URI,
            "--query",
            QUESTION,
            "--history",
            "--context",
            str(root),
            "--json",
        ],
        command="context-pack",
    )["result"]["pack"]

    _, responses = invoke_mcp(
        root,
        [
            (
                "search",
                {"schema_version": 1, "query": QUESTION, "limit": 20},
            ),
            (
                "context_pack",
                {
                    "schema_version": 1,
                    "uri": TASK_URI,
                    "budget": 12000,
                    "query": QUESTION,
                    "include_history": True,
                },
            ),
        ],
    )
    mcp_search = responses["search"]
    mcp_pack = responses["context_pack"]
    assert mcp_search["is_error"] is False
    assert mcp_pack["is_error"] is False
    mcp_search_result = mcp_search["structured_content"]["result"]
    mcp_pack_result = mcp_pack["structured_content"]["result"]["pack"]

    cli_hit_uris = [item["uri"] for item in cli_search["hits"]]
    mcp_hit_uris = [item["uri"] for item in mcp_search_result["hits"]]
    assert cli_search["query"] == QUESTION
    assert cli_search["count"] >= 1
    assert cli_hit_uris == mcp_hit_uris
    assert TASK_URI in cli_hit_uris

    assert cli_pack == mcp_pack_result
    assert cli_pack["focal_uri"] == TASK_URI
    assert cli_pack["query"] == QUESTION
    source_items = cli_pack["sections"]["source_observations"]["items"]
    assert {item["uri"] for item in source_items} == {OBSERVATION_URI}
    assert operational.artifact_ref in json.dumps(source_items, sort_keys=True)
