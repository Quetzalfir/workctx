from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from workctx.cli import app
from workctx.services.contexts import initialize_context

runner = CliRunner()


def test_context_validate_strict_still_reports_unrelated_legacy_violation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Strict Validation", context_id="strict-validation")
    legacy = root / "90_integrations" / "legacy-notes.md"
    legacy.write_text("C:\\legacy\\notes.txt\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["context", "validate", str(root), "--strict", "--json"],
    )

    assert result.exit_code == 1, result.output
    envelope = json.loads(result.stdout)
    matching_errors = [
        error for error in envelope["errors"] if error["code"] == "CTX-ABSOLUTE-PATH"
    ]
    assert matching_errors == [
        {
            "code": "CTX-ABSOLUTE-PATH",
            "message": ("A durable workspace value contains a machine-specific absolute path."),
            "path": "90_integrations/legacy-notes.md",
            "repair_action": (
                "Replace the path with a context-relative path or a canonical durable URI."
            ),
        }
    ]
