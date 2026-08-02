"""Lead integration tests (D-036): evidence zones are opaque to content checks."""

from pathlib import Path

from workctx.services.contexts import initialize_context
from workctx.validation import validate_workspace


def _context(tmp_path: Path) -> Path:
    root = tmp_path / "ctx"
    initialize_context(root, name="Opaque Evidence Test")
    return root


def test_secretlike_quarantined_text_is_not_content_scanned(tmp_path: Path) -> None:
    root = _context(tmp_path)
    quarantined = root / "00_inbox" / "quarantine" / "suspicious.md"
    quarantined.write_text('api_key = "sk-fictional-1234567890abcdef"\n', encoding="utf-8")
    raw = root / "00_inbox" / "raw" / "evidence.txt"
    raw.write_text("C:\machine\specific\path\n", encoding="utf-8")
    report = validate_workspace(root)
    assert report.ok
    assert not [i for i in report.issues if i.path and "00_inbox" in i.path]


def test_canonical_zones_are_still_content_scanned(tmp_path: Path) -> None:
    root = _context(tmp_path)
    doc = root / "02_knowledge" / "notes" / "leak.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text('api_key = "sk-fictional-1234567890abcdef"\n', encoding="utf-8")
    report = validate_workspace(root)
    assert not report.ok
