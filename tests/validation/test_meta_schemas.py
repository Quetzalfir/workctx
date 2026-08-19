from __future__ import annotations

from pathlib import Path

import pytest

from workctx.services.contexts import initialize_context
from workctx.validation import Severity, validate_workspace

SCHEMA_PATH = "99_meta/schemas/transaction-proposal.schema.json"


@pytest.mark.parametrize("state", ("missing", "stale"))
def test_validation_advises_refresh_for_missing_or_stale_packaged_schema(
    tmp_path: Path,
    state: str,
) -> None:
    root = tmp_path / "context"
    initialize_context(root, name="Metadata Validation", context_id="metadata-validation")
    schema = root.joinpath(*SCHEMA_PATH.split("/"))
    if state == "missing":
        schema.unlink()
    else:
        schema.write_bytes(b"{}\n")

    report = validate_workspace(root, strict=True)

    issues = [issue for issue in report.issues if issue.code == "META-SCHEMA-STALE"]
    assert len(issues) == 1
    assert issues[0].severity is Severity.ADVISORY
    assert issues[0].path == SCHEMA_PATH
    assert issues[0].repair_action == "Run `workctx context refresh-meta`."
    assert report.ok is True
