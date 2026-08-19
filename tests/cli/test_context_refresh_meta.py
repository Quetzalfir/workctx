from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from workctx.cli import app
from workctx.services.contexts import initialize_context

ROOT = Path(__file__).parents[2]
SCHEMA_PATH = "99_meta/schemas/transaction-proposal.schema.json"
CANONICAL_SCHEMA = ROOT / "schemas" / "transaction-proposal.schema.json"
runner = CliRunner()


def _other_file_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() != SCHEMA_PATH
    }


def _invoke_refresh(root: Path) -> tuple[Any, dict[str, Any]]:
    result = runner.invoke(
        app,
        ["context", "refresh-meta", str(root), "--json"],
    )
    assert result.exit_code == 0, result.output
    return result, json.loads(result.stdout)


@pytest.mark.parametrize("state", ("missing", "stale"))
def test_context_refresh_meta_repairs_only_packaged_schema(
    tmp_path: Path,
    state: str,
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Metadata Refresh", context_id="metadata-refresh")
    schema = root.joinpath(*SCHEMA_PATH.split("/"))
    if state == "missing":
        schema.unlink()
    else:
        schema.write_bytes(b"{}\n")
    before = _other_file_snapshot(root)

    result, envelope = _invoke_refresh(root)

    assert result.stderr == ""
    assert envelope["command"] == "context.refresh-meta"
    assert envelope["context_id"] == "metadata-refresh"
    assert envelope["result"] == {
        "root": str(root.resolve()),
        "schemas": [{"path": SCHEMA_PATH, "status": "updated"}],
    }
    assert schema.read_bytes() == CANONICAL_SCHEMA.read_bytes()
    assert _other_file_snapshot(root) == before


def test_context_refresh_meta_is_idempotent_without_rewriting_current_schema(
    tmp_path: Path,
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Metadata Current", context_id="metadata-current")
    schema = root.joinpath(*SCHEMA_PATH.split("/"))
    before = _other_file_snapshot(root)
    schema_timestamp = schema.stat().st_mtime_ns

    _, envelope = _invoke_refresh(root)

    assert envelope["result"]["schemas"] == [{"path": SCHEMA_PATH, "status": "unchanged"}]
    assert schema.stat().st_mtime_ns == schema_timestamp
    assert _other_file_snapshot(root) == before
