from __future__ import annotations

from pathlib import Path

from workctx.services.contexts import initialize_context
from workctx.validation import validate_workspace

INLINE_VALUE = "fictional-inline-material-123456"


def test_secret_ref_line_passes_while_inline_value_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "secret-validation"
    initialize_context(root, name="Secret Validation", context_id="secret-validation")
    integration = root / "90_integrations" / "fictional-connector.yaml"
    integration.write_text("secret_ref: fictional-service-token\n", encoding="utf-8")

    reference_report = validate_workspace(root)

    assert all(issue.code != "CTX-POSSIBLE-SECRET" for issue in reference_report.issues)

    integration.write_text(f"api_key: {INLINE_VALUE}\n", encoding="utf-8")
    inline_report = validate_workspace(root)

    issue = next(issue for issue in inline_report.issues if issue.code == "CTX-POSSIBLE-SECRET")
    assert INLINE_VALUE not in issue.message
    assert INLINE_VALUE not in (issue.repair_action or "")
