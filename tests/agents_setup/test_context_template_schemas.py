from __future__ import annotations

import importlib.resources
import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[2]
SYNC_SCRIPT = ROOT / "scripts" / "sync_context_template.py"
CANONICAL_SCHEMA = ROOT / "schemas" / "transaction-proposal.schema.json"


def _load_sync_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_context_template", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load context-template sync script: {SYNC_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaged_proposal_schema_is_byte_identical_to_canonical_source() -> None:
    packaged = (
        importlib.resources.files("workctx.resources.context_template")
        .joinpath("99_meta", "schemas", "transaction-proposal.schema.json")
        .read_bytes()
    )

    assert packaged == CANONICAL_SCHEMA.read_bytes()


def test_template_sync_materializes_schema_from_source_idempotently(tmp_path: Path) -> None:
    module = _load_sync_module()
    template = tmp_path / "context-template"
    template.mkdir()

    first = module.materialize_reference_schemas(template, ROOT / "schemas")
    target = template / "99_meta" / "schemas" / "transaction-proposal.schema.json"

    assert first == ("99_meta/schemas/transaction-proposal.schema.json",)
    assert target.read_bytes() == CANONICAL_SCHEMA.read_bytes()

    target.write_bytes(b"stale\n")
    second = module.materialize_reference_schemas(template, ROOT / "schemas")
    third = module.materialize_reference_schemas(template, ROOT / "schemas")

    assert second == ("99_meta/schemas/transaction-proposal.schema.json",)
    assert third == ()
    assert target.read_bytes() == CANONICAL_SCHEMA.read_bytes()
