"""Guard against test packages that shadow stdlib or installed modules.

pytest inserts ``tests/`` into ``sys.path``, so a test package named after a
top-level importable module is imported IN ITS PLACE by product code during
test runs. A ``tests/secrets`` package once broke 314 tests this way by
shadowing the stdlib ``secrets`` module.
"""

from __future__ import annotations

import sys
from importlib.metadata import packages_distributions
from pathlib import Path

TESTS_ROOT = Path(__file__).parent

# tests/mcp predates this guard; tests/e2e/support.py works around it when it
# needs the real MCP SDK. Never add another entry here.
KNOWN_SHADOWS = {"mcp"}


def test_test_packages_do_not_shadow_importable_modules() -> None:
    packages = {
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    importable = set(sys.stdlib_module_names) | set(packages_distributions())
    shadows = (packages & importable) - KNOWN_SHADOWS
    assert not shadows, (
        f"tests packages {sorted(shadows)} shadow importable modules "
        "via pytest sys.path insertion; rename them"
    )
