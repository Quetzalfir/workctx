from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import workctx.services.contexts as contexts_module
from workctx.adapters.filesystem.registry import (
    ContextRegistry,
    RegistrySnapshot,
    unregister_context,
)
from workctx.cli import app
from workctx.services.contexts import initialize_context

runner = CliRunner()


def _context(tmp_path: Path, context_id: str) -> Path:
    root = tmp_path / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    return root


def test_context_resolution_registers_once_and_exact_reuse_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path, "register-on-use")
    assert unregister_context("register-on-use")
    saves = 0
    original_save = ContextRegistry._save

    def observed_save(self: ContextRegistry, snapshot: RegistrySnapshot) -> None:
        nonlocal saves
        saves += 1
        original_save(self, snapshot)

    monkeypatch.setattr(ContextRegistry, "_save", observed_save)

    first = runner.invoke(app, ["context", "inspect", str(root), "--json"])
    second = runner.invoke(app, ["context", "inspect", str(root), "--json"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert saves == 1
    assert ContextRegistry().get("register-on-use") == root.resolve()


def test_context_resolution_rebinds_a_moved_context(
    tmp_path: Path,
) -> None:
    first = _context(tmp_path, "moved-context")
    moved_parent = tmp_path / "moved"
    moved_parent.mkdir()
    second = moved_parent / "moved-context"
    shutil.copytree(first, second)

    result = runner.invoke(app, ["context", "inspect", str(second), "--json"])

    assert result.exit_code == 0, result.output
    assert ContextRegistry().get("moved-context") == second.resolve()


def test_registry_failure_never_fails_a_context_resolved_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path, "unwritable-registry")
    assert unregister_context("unwritable-registry")

    def fail_registration(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("injected unwritable registry")

    monkeypatch.setattr(
        contexts_module,
        "register_context_if_changed",
        fail_registration,
    )

    result = runner.invoke(app, ["context", "inspect", str(root), "--json"])

    assert result.exit_code == 0, result.output
    assert ContextRegistry().get("unwritable-registry") is None
