from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from workctx.adapters.filesystem.lock import ContextLock
from workctx.adapters.filesystem.serialization import dump_yaml_bytes
from workctx.adapters.filesystem.staging import (
    RecoveryRequiredError,
    StagedReplacement,
    StagedWrite,
)
from workctx.adapters.filesystem.store import CanonicalStore
from workctx.domain.artifacts import ArtifactManifest
from workctx.domain.entities import EntityFrontmatter
from workctx.domain.tasks import Task
from workctx.errors import ContextBoundaryError
from workctx.services.contexts import initialize_context

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "workspace" / "fixtures" / "positive"


def _load_model[ModelT](filename: str, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(json.loads((FIXTURES / filename).read_text(encoding="utf-8")))


@pytest.fixture
def context_root(tmp_path: Path) -> Path:
    root = tmp_path / "fictional-context"
    initialize_context(root, name="Fictional Context", context_id="fictional-context")
    return root


def test_context_init_and_store_use_identical_canonical_config_bytes(context_root: Path) -> None:
    store = CanonicalStore(context_root)
    config = store.read_context_config()

    assert (context_root / "context.yaml").read_bytes() == dump_yaml_bytes(config)
    assert not store.context_config_has_hand_edits()


def test_typed_entity_and_task_round_trip_through_staged_single_file_writes(
    context_root: Path,
) -> None:
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)
    task = _load_model("task.json", Task)

    store.write_entity("02_knowledge/person.md", entity, "Entity body.")
    store.write_task("03_work/tasks/task.md", task, "Task body.")

    loaded_entity = store.read_entity("02_knowledge/person.md")
    loaded_task = store.read_task("03_work/tasks/task.md")
    assert loaded_entity.frontmatter == entity
    assert loaded_entity.body == "Entity body.\n"
    assert loaded_task.frontmatter == task
    assert loaded_task.body == "Task body.\n"
    assert not (context_root / "98_state" / "lock.json").exists()
    assert not list((context_root / "98_state" / "staging").glob("single-*.stage"))


@pytest.mark.parametrize("suffix", ["yaml", "yml", "json"])
def test_artifact_manifest_round_trips_in_both_architecture_supported_formats(
    context_root: Path,
    suffix: str,
) -> None:
    store = CanonicalStore(context_root)
    manifest = _load_model("artifact-manifest.json", ArtifactManifest)
    relative = f"00_inbox/manifests/{manifest.id}.{suffix}"

    store.write_artifact_manifest(relative, manifest)

    assert store.read_artifact_manifest(relative) == manifest
    assert not store.artifact_manifest_has_hand_edits(relative)


def test_store_write_context_config_is_typed_and_canonical(context_root: Path) -> None:
    store = CanonicalStore(context_root)
    config = store.read_context_config()
    updated = config.model_copy(update={"name": "Renamed Fictional Context"})

    store.write_context_config(updated)

    assert store.read_context_config() == updated
    assert (context_root / "context.yaml").read_bytes() == dump_yaml_bytes(updated)


def test_store_prepares_typed_writes_for_future_multi_file_transactions(
    context_root: Path,
) -> None:
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)

    write = store.prepare_entity("02_knowledge/prepared.md", entity, "Prepared.")

    assert write.target == "02_knowledge/prepared.md"
    assert write.content.startswith(b"---\nschema_version: 1\n")
    assert not (context_root / "02_knowledge" / "prepared.md").exists()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("entity", "03_work/entity.txt"),
        ("entity", "04_views/entity.md"),
        ("task", "02_knowledge/task.md"),
        ("manifest", "00_inbox/raw/manifest.yaml"),
        ("manifest", "00_inbox/manifests/manifest.txt"),
    ],
)
def test_typed_documents_reject_wrong_zone_or_suffix(
    context_root: Path,
    method: str,
    path: str,
) -> None:
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)
    task = _load_model("task.json", Task)
    manifest = _load_model("artifact-manifest.json", ArtifactManifest)

    with pytest.raises(ContextBoundaryError):
        if method == "entity":
            store.prepare_entity(path, entity)
        elif method == "task":
            store.prepare_task(path, task)
        else:
            store.prepare_artifact_manifest(path, manifest)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "02_knowledge/../../outside.md",
        "02_knowledge/./entity.md",
        "C:outside.md",
        "C:/outside.md",
    ],
)
def test_store_rejects_traversal_absolute_and_drive_relative_paths(
    context_root: Path,
    path: str,
) -> None:
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)

    with pytest.raises(ContextBoundaryError):
        store.prepare_entity(path, entity)


def test_store_rejects_cross_context_entity_uri(context_root: Path) -> None:
    payload = json.loads((FIXTURES / "entity.json").read_text(encoding="utf-8"))
    payload["uri"] = "workctx://another-context/person/PER-jordan-lee"
    entity = EntityFrontmatter.model_validate(payload)
    store = CanonicalStore(context_root)

    with pytest.raises(ContextBoundaryError, match="another-context"):
        store.prepare_entity("02_knowledge/person.md", entity)


def test_generic_entity_apis_reject_task_documents(context_root: Path) -> None:
    store = CanonicalStore(context_root)
    task = _load_model("task.json", Task)

    with pytest.raises(ContextBoundaryError, match="dedicated task APIs"):
        store.prepare_entity("03_work/tasks/task.md", task)

    store.write_task("03_work/tasks/task.md", task)
    with pytest.raises(ContextBoundaryError, match="dedicated task APIs"):
        store.read_entity("03_work/tasks/task.md")


def test_store_rejects_artifact_paths_that_escape_canonical_artifact_zones(
    context_root: Path,
) -> None:
    manifest = _load_model("artifact-manifest.json", ArtifactManifest).model_copy(
        update={"preserved_path": "../outside.txt"}
    )
    store = CanonicalStore(context_root)

    with pytest.raises(ContextBoundaryError):
        store.prepare_artifact_manifest("00_inbox/manifests/artifact.yaml", manifest)


def test_store_rejects_nested_context_boundary(context_root: Path) -> None:
    nested = context_root / "02_knowledge" / "nested"
    initialize_context(nested, name="Nested", context_id="nested-context")
    store = CanonicalStore(context_root)

    with pytest.raises(ContextBoundaryError, match="nested context"):
        store.read_entity("02_knowledge/nested/02_knowledge/entity.md")


def test_store_rejects_symlink_escape(context_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = context_root / "02_knowledge" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable for this test user: {exc}")
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)

    with pytest.raises(ContextBoundaryError, match="escapes"):
        store.prepare_entity("02_knowledge/escape/entity.md", entity)

    assert not (outside / "entity.md").exists()


def test_store_rejects_symlink_crossing_into_another_context_zone(context_root: Path) -> None:
    state = context_root / "98_state"
    link = context_root / "02_knowledge" / "state-link"
    try:
        link.symlink_to(state, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable for this test user: {exc}")
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)

    with pytest.raises(ContextBoundaryError, match="allowed zone"):
        store.prepare_entity("02_knowledge/state-link/entity.md", entity)

    assert not (state / "entity.md").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction-specific boundary test")
def test_store_rejects_windows_junction_escape(context_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "junction-outside"
    outside.mkdir()
    junction = context_root / "02_knowledge" / "junction-escape"
    completed = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("Windows junction creation is unavailable for this test user")
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)

    with pytest.raises(ContextBoundaryError, match="escapes"):
        store.prepare_entity("02_knowledge/junction-escape/entity.md", entity)

    assert not (outside / "entity.md").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction-specific boundary test")
def test_store_rejects_junction_crossing_into_another_context_zone(context_root: Path) -> None:
    state = context_root / "98_state"
    junction = context_root / "02_knowledge" / "state-junction"
    completed = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(state)],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("Windows junction creation is unavailable for this test user")
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)

    with pytest.raises(ContextBoundaryError, match="allowed zone"):
        store.prepare_entity("02_knowledge/state-junction/entity.md", entity)

    assert not (state / "entity.md").exists()


def test_store_resolve_path_requires_at_least_one_zone(context_root: Path) -> None:
    store = CanonicalStore(context_root)

    with pytest.raises(ContextBoundaryError, match="At least one"):
        store.resolve_path("02_knowledge/entity.md", zones=())


def test_store_direct_write_is_blocked_by_existing_recovery_intent(context_root: Path) -> None:
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)
    holder = ContextLock.acquire(context_root, session_id="pending-intent", tool_version="test")
    stager = StagedReplacement(context_root)
    stager.prepare(
        "TXN-PENDING",
        holder.nonce,
        [StagedWrite("02_knowledge/recovery-target.md", b"postimage\n")],
        lock=holder,
    )

    try:
        with pytest.raises(RecoveryRequiredError, match="requires recovery"):
            store.write_entity("02_knowledge/direct.md", entity, lock=holder)
        assert not (context_root / "02_knowledge" / "direct.md").exists()
    finally:
        holder.release()


def test_store_hand_edit_detection_flags_reordered_yaml(context_root: Path) -> None:
    store = CanonicalStore(context_root)
    entity = _load_model("entity.json", EntityFrontmatter)
    path = context_root / "02_knowledge" / "hand-edited.md"
    store.write_entity("02_knowledge/hand-edited.md", entity, "Body.")
    raw = path.read_bytes()
    path.write_bytes(
        raw.replace(
            b"status: active\nconfidence: high\n",
            b"confidence: high\nstatus: active\n",
        )
    )

    assert store.entity_has_hand_edits("02_knowledge/hand-edited.md")


def test_store_rejects_a_lock_from_another_context(context_root: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    initialize_context(other, name="Other", context_id="other-context")
    wrong_lock = ContextLock.acquire(other, session_id="wrong", tool_version="test")
    store = CanonicalStore(context_root)
    config = store.read_context_config()

    with pytest.raises(ContextBoundaryError, match="another context"):
        store.write_context_config(config, lock=wrong_lock)

    wrong_lock.release()
