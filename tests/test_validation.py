from pathlib import Path

from workctx.services.contexts import initialize_context
from workctx.validation.workspace import Severity, validate_workspace


def test_validation_detects_missing_required_directory(tmp_path: Path) -> None:
    target = tmp_path / "context"
    initialize_context(target, name="Context", context_id="context")
    (target / "03_work").rename(target / "03_work_missing")

    report = validate_workspace(target)

    assert not report.ok
    assert any(
        issue.code == "CTX-MISSING-DIRECTORY" and issue.path == "03_work"
        for issue in report.issues
    )


def test_validation_detects_possible_secret(tmp_path: Path) -> None:
    target = tmp_path / "context"
    initialize_context(target, name="Context", context_id="context")
    bad = target / "02_knowledge" / "questions" / "bad.md"
    bad.write_text("api_key = abcdefghijklmnopqrstuvwxyz", encoding="utf-8")

    report = validate_workspace(target)

    assert any(
        issue.severity is Severity.ERROR and issue.code == "CTX-POSSIBLE-SECRET"
        for issue in report.issues
    )
