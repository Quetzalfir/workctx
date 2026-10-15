from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph.support import NOW, graph_context
from typer.testing import CliRunner

from workctx.cli import app
from workctx.graph import GraphMap
from workctx.obsidian import ObsidianInstallResult, ObsidianOpenResult

runner = CliRunner()


def test_graph_export_cli_returns_stable_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = graph_context(tmp_path / "context")
    monkeypatch.setattr("workctx.topics.heat._utc_now", lambda: NOW)

    result = runner.invoke(
        app,
        [
            "graph",
            "export",
            "--topic",
            "alpha-team",
            "--context",
            str(root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "graph.export"
    assert payload["context_id"] == "graph-test"
    assert payload["result"]["selected_topic"] == "alpha"
    assert payload["result"]["topics"] == ["alpha", "beta"]
    assert [item["map"] for item in payload["result"]["vaults"]] == [
        "topics",
        "references",
    ]


def test_obsidian_cli_wires_install_and_open_without_real_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("C:/fictional context")
    install_calls: list[tuple[bool, bool]] = []
    open_calls: list[tuple[Path, str]] = []

    def fake_install(*, yes: bool, accept_agreements: bool) -> ObsidianInstallResult:
        install_calls.append((yes, accept_agreements))
        command = (
            "winget",
            "install",
            "--id",
            "Obsidian.Obsidian",
            "-e",
            "--accept-package-agreements",
            "--accept-source-agreements",
        )
        return ObsidianInstallResult(
            platform="windows",
            installed=False,
            manager="winget",
            command=command,
            command_text=" ".join(command),
            executed=False,
        )

    def fake_open(path: Path, map_name: str) -> ObsidianOpenResult:
        open_calls.append((path, map_name))
        return ObsidianOpenResult(
            map=GraphMap.TOPICS,
            vault_path=str(path / "04_views/graph/topic-map"),
            uri="obsidian://open?path=fake",
            exported=True,
        )

    monkeypatch.setattr("workctx.obsidian.install_obsidian", fake_install)
    monkeypatch.setattr("workctx.obsidian.open_obsidian_map", fake_open)
    monkeypatch.setattr("workctx.cli.resolve_cli_context", lambda **_kwargs: root)
    monkeypatch.setattr(
        "workctx.cli.load_context_config",
        lambda _root: SimpleNamespace(id="fictional-context"),
    )

    install = runner.invoke(
        app,
        ["obsidian", "install", "--accept-agreements", "--json"],
    )
    opened = runner.invoke(
        app,
        ["obsidian", "open", "--map", "topics", "--context", str(root), "--json"],
    )

    assert install.exit_code == 0, install.output
    assert json.loads(install.stdout)["command"] == "obsidian.install"
    assert json.loads(install.stdout)["result"]["executed"] is False
    assert install_calls == [(False, True)]
    assert opened.exit_code == 0, opened.output
    assert json.loads(opened.stdout)["command"] == "obsidian.open"
    assert json.loads(opened.stdout)["result"]["exported"] is True
    assert open_calls == [(root, "topics")]
