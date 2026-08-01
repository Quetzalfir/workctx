"""Lead integration tests: durable projection invalidation for WP-300."""

from pathlib import Path

from workctx.adapters.sqlite import SQLiteProjection
from workctx.adapters.sqlite.projection import projection_database_path
from workctx.services.contexts import initialize_context


def _context(tmp_path: Path) -> Path:
    target = tmp_path / "ctx"
    initialize_context(target, name="Invalidate Test")
    return target


def test_invalidate_marks_projection_for_rebuild(tmp_path: Path) -> None:
    root = _context(tmp_path)
    projection = SQLiteProjection(root)
    projection.rebuild()
    assert projection.readiness_trigger() is None

    projection.invalidate()
    assert not projection_database_path(root).exists()
    assert projection.readiness_trigger() is not None

    report = projection.ensure_ready()
    assert report is not None
    assert projection.readiness_trigger() is None


def test_invalidate_before_first_build_is_a_no_op(tmp_path: Path) -> None:
    root = _context(tmp_path)
    projection = SQLiteProjection(root)
    projection.invalidate()
    assert projection.readiness_trigger() is not None
