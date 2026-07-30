from pathlib import Path

ROOT = Path(__file__).parents[1]
PUBLIC_TEMPLATE = ROOT / "templates" / "context"
PACKAGE_TEMPLATE = ROOT / "src" / "workctx" / "resources" / "context_template"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_public_and_packaged_context_templates_are_identical() -> None:
    assert _files(PUBLIC_TEMPLATE) == _files(PACKAGE_TEMPLATE)
