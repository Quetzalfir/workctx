"""Lead integration tests: Wave 2 engines wired into the CLI surface."""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.adapters.filesystem.registry import ContextRegistry
from workctx.adapters.sqlite.projection import projection_database_path
from workctx.cli import app
from workctx.services.contexts import initialize_context

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()


def _envelope(result: Any, *, exit_code: int) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    return payload


def _init(tmp_path: Path, name: str = "Wiring Test") -> Path:
    target = tmp_path / "ctx"
    initialize_context(target, name=name)
    return target


def _issue_codes(payload: dict[str, Any]) -> set[str]:
    return {issue["code"] for issue in payload["result"]["issues"]}


def test_index_rebuild_emits_envelope_and_creates_database(tmp_path: Path) -> None:
    root = _init(tmp_path)
    result = runner.invoke(app, ["index", "rebuild", str(root), "--json"])
    payload = _envelope(result, exit_code=0)
    assert payload["ok"] is True
    assert payload["command"] == "index.rebuild"
    assert payload["result"]["counts"]["entities"] == 0
    assert payload["result"]["skipped"] == []
    assert projection_database_path(root).is_file()


def test_validate_without_projection_reports_no_projection_codes(tmp_path: Path) -> None:
    root = _init(tmp_path)
    payload = _envelope(
        runner.invoke(app, ["context", "validate", str(root), "--json"]), exit_code=0
    )
    assert not {code for code in _issue_codes(payload) if code.startswith("PROJECTION-")}


def test_validate_after_rebuild_is_fresh_and_clean(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert runner.invoke(app, ["index", "rebuild", str(root)]).exit_code == 0
    payload = _envelope(
        runner.invoke(app, ["context", "validate", str(root), "--json"]), exit_code=0
    )
    assert payload["ok"] is True
    assert not {code for code in _issue_codes(payload) if code.startswith("PROJECTION-")}


def test_validate_reports_stale_projection_after_corruption(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert runner.invoke(app, ["index", "rebuild", str(root)]).exit_code == 0
    projection_database_path(root).write_bytes(b"not a sqlite database")
    payload = _envelope(
        runner.invoke(app, ["context", "validate", str(root), "--json"]), exit_code=0
    )
    assert "PROJECTION-STALE" in _issue_codes(payload)
    assert payload["ok"] is True  # warning severity by default


def test_strict_escalates_warnings_to_errors(tmp_path: Path) -> None:
    root = _init(tmp_path)
    assert runner.invoke(app, ["index", "rebuild", str(root)]).exit_code == 0
    projection_database_path(root).write_bytes(b"not a sqlite database")

    relaxed = _envelope(
        runner.invoke(app, ["context", "validate", str(root), "--json"]), exit_code=0
    )
    assert relaxed["ok"] is True

    strict = _envelope(
        runner.invoke(app, ["context", "validate", str(root), "--strict", "--json"]),
        exit_code=1,
    )
    assert strict["ok"] is False
    assert any(error["code"] == "PROJECTION-STALE" for error in strict["errors"])


def test_registry_active_context_is_resolution_step_three(tmp_path: Path, monkeypatch: Any) -> None:
    root = _init(tmp_path)
    registry_file = tmp_path / "registry.json"
    registry = ContextRegistry(registry_file=registry_file)
    registry.register("wiring-test", root)
    registry.set_active("wiring-test")

    monkeypatch.setattr(
        "workctx.adapters.filesystem.registry.ContextRegistry",
        lambda: ContextRegistry(registry_file=registry_file),
    )

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    payload = _envelope(runner.invoke(app, ["context", "inspect", "--json"]), exit_code=0)
    assert payload["context_id"] == "wiring-test"
