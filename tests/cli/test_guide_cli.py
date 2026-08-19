"""Acceptance coverage for the deterministic ownership guide command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.adapters.filesystem.registry import ContextRegistry, unregister_context
from workctx.cli import app
from workctx.guide import GUIDE, EditPolicy, OwnershipClass
from workctx.services.contexts import initialize_context

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()

EXPECTED_OWNERSHIP = {
    "00_inbox/": ("canonical-via-proposals", "through proposals or transactions"),
    "01_processed/": ("canonical-via-proposals", "through proposals or transactions"),
    "02_knowledge/": ("canonical-via-proposals", "through proposals or transactions"),
    "03_work/": ("canonical-via-proposals", "through proposals or transactions"),
    "04_views/": ("generated", "never edit by hand"),
    "05_outbox/": ("canonical-via-proposals", "through proposals or transactions"),
    "06_overrides/": ("operator-owned", "edit freely"),
    "90_integrations/": ("canonical-via-proposals", "through proposals or transactions"),
    "98_state/": ("machine-state", "never edit by hand"),
    "99_meta/": ("canonical-via-proposals", "through proposals or transactions"),
    "context.yaml": ("operator-owned", "edit freely"),
    ".gitignore": ("operator-owned", "edit freely"),
    "README.md": ("operator-owned", "edit freely"),
    "instructions.md": ("operator-owned", "edit freely"),
    ".agents/skills/": ("adapter-managed", "preserved-but-freezes-updates"),
    ".agents/skills/registry.yaml custom_skills + .agents/skills/<id>/": (
        "operator-owned",
        "edit freely",
    ),
    "AGENTS.md": ("adapter-managed", "preserved-but-freezes-updates"),
    "CLAUDE.md": ("adapter-managed", "preserved-but-freezes-updates"),
    "GEMINI.md": ("adapter-managed", "preserved-but-freezes-updates"),
    ".codex/": ("adapter-managed", "preserved-but-freezes-updates"),
    ".mcp.json": ("adapter-managed", "preserved-but-freezes-updates"),
    ".claude/skills/": ("generated", "never edit by hand"),
    ".gemini/skills/": ("generated", "never edit by hand"),
    ".gemini/settings.json": ("adapter-managed", "preserved-but-freezes-updates"),
}

EXPECTED_ROUTING = [
    {
        "kind": "person fact",
        "destination": "person entity under 02_knowledge/",
        "via": "proposal or transaction",
    },
    {
        "kind": "access or process fact",
        "destination": "integration entity in 90_integrations/ or system entity in 02_knowledge/",
        "via": "proposal or transaction",
    },
    {
        "kind": "standing operator preference",
        "destination": "context instructions.md; user-level instructions.md for all contexts",
        "via": "operator-reviewed suggestion",
    },
    {"kind": "evidence", "destination": "00_inbox/", "via": "workctx inbox add"},
    {
        "kind": "task or work item",
        "destination": "03_work/",
        "via": "proposal or transaction",
    },
    {"kind": "outbound draft", "destination": "05_outbox/", "via": "draft flow"},
    {
        "kind": "workflow customization",
        "destination": "06_overrides/skills/<name>/SKILL.md",
        "via": "operator-owned override flow",
    },
    {
        "kind": "custom agent workflow",
        "destination": "custom_skills in .agents/skills/registry.yaml + .agents/skills/<id>/",
        "via": "operator-reviewed custom skill registration",
    },
    {
        "kind": "secret material",
        "destination": "nowhere in the context; keep reference names only",
        "via": "the configured secret reference name",
    },
]


def _context(tmp_path: Path, context_id: str = "guide-context") -> Path:
    root = tmp_path / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    return root


def _envelope(result: Any, *, exit_code: int = 0) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == "guide"
    assert payload["ok"] is (exit_code == 0)
    return payload


def _normalized(text: str) -> str:
    return " ".join(text.split())


def test_human_guide_contains_every_class_routing_section_and_escape_hatch(
    tmp_path: Path,
) -> None:
    root = _context(tmp_path)

    result = runner.invoke(app, ["guide", "--context", str(root)])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert "File placement and ownership — guide-context" in result.stdout
    assert "Ownership" in result.stdout
    assert "Where does it go?" in result.stdout
    assert "Never edit by hand" in result.stdout
    for ownership_class in OwnershipClass:
        assert ownership_class.value in result.stdout
    assert _normalized(GUIDE.escape_hatch) in _normalized(result.stdout)


def test_json_shape_pins_paths_classes_policies_and_routing_entries(tmp_path: Path) -> None:
    root = _context(tmp_path)

    payload = _envelope(runner.invoke(app, ["guide", "--context", str(root), "--json"]))
    result = payload["result"]

    assert payload["context_id"] == "guide-context"
    assert set(result) == {
        "schema_version",
        "root",
        "ownership",
        "routing",
        "never_edit",
        "adapter_note",
        "escape_hatch",
    }
    assert result["schema_version"] == 1
    assert result["root"] == str(root.resolve())
    assert result["ownership"] == [entry.to_payload() for entry in GUIDE.ownership]
    assert result["routing"] == EXPECTED_ROUTING
    assert result["never_edit"] == [entry.to_payload() for entry in GUIDE.never_edit]
    assert result["adapter_note"] == GUIDE.adapter_note
    assert result["escape_hatch"] == GUIDE.escape_hatch
    actual_ownership = {
        entry["path"]: (entry["class"], entry["policy"]) for entry in result["ownership"]
    }
    assert actual_ownership == EXPECTED_OWNERSHIP
    assert {entry["class"] for entry in result["ownership"]} == {
        ownership_class.value for ownership_class in OwnershipClass
    }
    assert {entry["policy"] for entry in result["ownership"]} == {
        policy.value for policy in EditPolicy
    }


def test_guide_resolves_explicit_and_discovered_contexts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_root = _context(tmp_path, "explicit-guide")
    discovered_root = _context(tmp_path, "discovered-guide")
    nested = discovered_root / "02_knowledge" / "nested"
    nested.mkdir()

    explicit = _envelope(runner.invoke(app, ["guide", "--context", str(explicit_root), "--json"]))
    monkeypatch.chdir(nested)
    discovered = _envelope(runner.invoke(app, ["guide", "--json"]))

    assert explicit["context_id"] == "explicit-guide"
    assert explicit["result"]["root"] == str(explicit_root.resolve())
    assert discovered["context_id"] == "discovered-guide"
    assert discovered["result"]["root"] == str(discovered_root.resolve())


def test_guide_registers_a_resolved_context_on_use(tmp_path: Path) -> None:
    root = _context(tmp_path, "guide-register-on-use")
    assert unregister_context("guide-register-on-use")

    payload = _envelope(runner.invoke(app, ["guide", "--context", str(root), "--json"]))

    assert payload["context_id"] == "guide-register-on-use"
    assert ContextRegistry().get("guide-register-on-use") == root.resolve()


def test_guide_reads_no_context_tree_beyond_standard_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _context(tmp_path, "guide-no-scan")

    def reject_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("guide attempted a context tree scan")

    monkeypatch.setattr(Path, "iterdir", reject_scan)
    monkeypatch.setattr(Path, "glob", reject_scan)
    monkeypatch.setattr(Path, "rglob", reject_scan)

    payload = _envelope(runner.invoke(app, ["guide", "--context", str(root), "--json"]))

    assert payload["context_id"] == "guide-no-scan"


def test_guide_uses_standard_context_and_usage_exit_bands(tmp_path: Path) -> None:
    missing = _envelope(
        runner.invoke(
            app,
            ["guide", "--context", str(tmp_path / "missing"), "--json"],
        ),
        exit_code=1,
    )

    assert missing["context_id"] is None
    assert missing["result"] == {}
    assert missing["errors"][0]["code"] == "CONTEXT_NOT_FOUND"

    usage = runner.invoke(app, ["guide", "--unknown-option", "--json"])
    assert usage.exit_code == 2
    assert usage.stdout == ""
    assert "No such option" in usage.stderr
