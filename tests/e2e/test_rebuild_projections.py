from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from workctx.adapters.sqlite import SQLiteProjection
from workctx.retrieval import build_pack, serialize_context_pack
from workctx.transactions import verify_ledger
from workctx.views import ViewService

from .support import QUESTION, TASK_ID, TASK_URI, VIEW_TIME, create_operational_context

pytestmark = [pytest.mark.acceptance, pytest.mark.integration]


def test_disposable_projection_and_views_rebuild_equivalently_from_canonical_data(
    tmp_path: Path,
) -> None:
    operational = create_operational_context(tmp_path / "projection-rebuild")
    root = operational.root
    projection = SQLiteProjection(root)
    hits_before = projection.search(QUESTION, limit=20)
    pack_before = build_pack(
        projection,
        TASK_URI,
        budget=12000,
        query=QUESTION,
        include_history=True,
    )
    assert pack_before.built and pack_before.pack is not None
    serialized_pack_before = serialize_context_pack(pack_before.pack)

    first_views = ViewService(root, clock=lambda: VIEW_TIME).rebuild_views()
    view_bytes_before = {item.path: (root / item.path).read_bytes() for item in first_views.views}
    ledger_before = verify_ledger(root)
    database = projection.database_path
    views_directory = root / "04_views"
    assert database.is_file()
    assert views_directory.is_dir()

    database.unlink()
    shutil.rmtree(views_directory)
    assert not database.exists()
    assert not views_directory.exists()
    assert (root / "03_work" / "tasks" / f"{TASK_ID}.md").is_file()

    rebuilt_projection = SQLiteProjection(root)
    report = rebuilt_projection.rebuild()
    assert report.counts.tasks >= 1
    hits_after = rebuilt_projection.search(QUESTION, limit=20)
    pack_after = build_pack(
        rebuilt_projection,
        TASK_URI,
        budget=12000,
        query=QUESTION,
        include_history=True,
    )
    assert pack_after.built and pack_after.pack is not None
    rebuilt_views = ViewService(root, clock=lambda: VIEW_TIME).rebuild_views()
    view_bytes_after = {item.path: (root / item.path).read_bytes() for item in rebuilt_views.views}

    assert hits_after == hits_before
    assert serialize_context_pack(pack_after.pack) == serialized_pack_before
    assert rebuilt_views == first_views
    assert view_bytes_after == view_bytes_before
    assert verify_ledger(root) == ledger_before
    assert database.is_file()
    assert views_directory.is_dir()
