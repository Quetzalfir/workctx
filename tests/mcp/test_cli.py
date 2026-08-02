"""CLI tests for the lazy, context-bound ``workctx mcp serve`` entry point."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from workctx.cli import app
from workctx.services.contexts import initialize_context

runner = CliRunner()


def _context(tmp_path: Path) -> Path:
    root = tmp_path / "context"
    initialize_context(root, name="MCP CLI Context", context_id="mcp-cli-context")
    return root


def test_mcp_serve_resolves_explicit_context_and_preserves_stdio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path)
    received: list[Path] = []
    monkeypatch.setattr("workctx.mcp.serve_stdio", received.append)

    result = runner.invoke(app, ["mcp", "serve", "--context", str(root)])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr == ""
    assert received == [root.resolve()]


def test_mcp_serve_discovers_context_from_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path)
    nested = root / "03_work" / "nested"
    nested.mkdir(parents=True)
    received: list[Path] = []
    monkeypatch.chdir(nested)
    monkeypatch.setattr("workctx.mcp.serve_stdio", received.append)

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr == ""
    assert received == [root.resolve()]


def test_missing_mcp_extra_has_clear_exit_5_without_breaking_other_cli_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path)
    attempted_imports: list[str] = []

    def missing_sdk(name: str) -> object:
        attempted_imports.append(name)
        raise ModuleNotFoundError("No module named 'mcp'", name="mcp")

    monkeypatch.setattr("workctx.mcp.runner.import_module", missing_sdk)

    version = runner.invoke(app, ["version"])
    serve = runner.invoke(app, ["mcp", "serve", "--context", str(root)])

    assert version.exit_code == 0
    assert version.stdout.strip()
    assert version.stderr == ""
    assert serve.exit_code == 5
    assert serve.stdout == ""
    stderr = " ".join(serve.stderr.split())
    assert "MCP support is unavailable" in stderr
    assert "workctx[mcp]" in stderr
    assert "uv sync --extra mcp" in stderr
    assert "Traceback" not in stderr
    assert attempted_imports == ["mcp"]
