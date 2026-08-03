from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def legacy_source(tmp_path: Path) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "legacy-repo"
    source = tmp_path / "legacy-repo"
    shutil.copytree(fixture, source)
    return source
