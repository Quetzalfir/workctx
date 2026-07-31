from __future__ import annotations

import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from workctx.adapters.sqlite import (
    ContextIsolationError,
    Fts5UnavailableError,
    ProjectionBuildError,
    RebuildTrigger,
    SkipReason,
    SQLiteProjection,
)
from workctx.adapters.sqlite import projection as projection_module
from workctx.adapters.sqlite.schema import create_schema
from workctx.domain import WorkctxUri
from workctx.domain.frontmatter import parse_frontmatter
from workctx.presentation import ExitCode, exit_code_for

from .support import (
    create_fictional_context,
    entity_frontmatter,
    rewrite_entity,
    write_markdown,
)


@pytest.mark.acceptance
def test_two_contexts_never_share_rows_or_queries(tmp_path: Path) -> None:
    root_a = tmp_path / "context-a"
    root_b = tmp_path / "context-b"
    create_fictional_context(
        root_a,
        "context-a",
        identity_title="Context A Identity",
        identity_alias="A IdP",
    )
    create_fictional_context(
        root_b,
        "context-b",
        identity_title="Context B Identity",
        identity_alias="B IdP",
    )
    foreign = entity_frontmatter("context-b", "SYS-foreign-copy", "system", "Foreign context row")
    write_markdown(root_a / "02_knowledge" / "foreign.md", foreign, "Must be denied.")
    projection_a = SQLiteProjection(root_a)
    projection_b = SQLiteProjection(root_b)

    report_a = projection_a.rebuild()
    projection_b.rebuild()

    assert any(item.reason is SkipReason.CONTEXT_MISMATCH for item in report_a.skipped_documents)
    assert projection_a.get_entity_by_id("SYS-identity-service").title == "Context A Identity"  # type: ignore[union-attr]
    assert projection_b.get_entity_by_id("SYS-identity-service").title == "Context B Identity"  # type: ignore[union-attr]
    assert projection_a.find_entities_by_alias("B IdP") == ()
    assert all(hit.uri.context_id == "context-a" for hit in projection_a.search("identity"))
    with pytest.raises(ContextIsolationError):
        projection_a.get_entity_by_uri("workctx://context-b/system/SYS-identity-service")
    with pytest.raises(ContextIsolationError):
        projection_a.inbound_edges("workctx://context-b/system/SYS-identity-service")
    with pytest.raises(ContextIsolationError):
        projection_a.inbound_edges(
            WorkctxUri.parse("workctx://context-b/system/SYS-identity-service")
        )

    shutil.copyfile(projection_b.database_path, projection_a.database_path)
    rebuilt = projection_a.ensure_ready()
    assert rebuilt is not None
    assert rebuilt.trigger is RebuildTrigger.CONTEXT_MISMATCH
    restored = projection_a.get_entity_by_id("SYS-identity-service")
    assert restored is not None
    assert restored.title == "Context A Identity"


def test_foreign_structured_references_are_skipped_without_partial_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    foreign_system = "workctx://other-context/system/SYS-other"
    foreign_person = "workctx://other-context/person/PER-other"

    system, system_body = parse_frontmatter(paths["system"].read_text(encoding="utf-8"))
    system["references"] = [
        {"relation": "mentions", "target": foreign_system, "source_observations": []}
    ]
    write_markdown(paths["system"], system, system_body.strip())

    evidence, evidence_body = parse_frontmatter(paths["evidence"].read_text(encoding="utf-8"))
    evidence["observations"][0]["related"][0]["target"] = foreign_system
    write_markdown(paths["evidence"], evidence, evidence_body.strip())

    claim, claim_body = parse_frontmatter(paths["claim_current"].read_text(encoding="utf-8"))
    claim["subject"] = foreign_system
    write_markdown(paths["claim_current"], claim, claim_body.strip())

    task, task_body = parse_frontmatter(paths["task"].read_text(encoding="utf-8"))
    task["owner"] = foreign_person
    write_markdown(paths["task"], task, task_body.strip())

    projection = SQLiteProjection(root)
    report = projection.rebuild()
    mismatches = {
        item.path for item in report.skipped_documents if item.reason is SkipReason.CONTEXT_MISMATCH
    }

    assert {
        "02_knowledge/system-identity.md",
        "02_knowledge/evidence-auth-flow.md",
        "02_knowledge/claim-status-current.md",
        "03_work/task-auth-review.md",
    }.issubset(mismatches)
    assert projection.get_entity_by_id("SYS-identity-service") is None
    assert projection.get_observation("EVD-20260730-auth-flow-01#OBS-001") is None
    assert projection.get_claim("CLM-2026-00002") is None
    assert projection.get_task("TASK-2026-001") is None


@pytest.mark.integration
def test_readers_see_old_then_new_complete_projection_during_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    writer = SQLiteProjection(root)
    writer.rebuild()
    reader = SQLiteProjection(root)
    rewrite_entity(paths["system"], title="Replacement Identity", aliases=["Replacement IdP"])
    temporary_built = threading.Event()
    replace_started = threading.Event()
    original_build = SQLiteProjection._build_temporary_database
    original_replace = projection_module._replace_with_retry

    def paused_build(self: SQLiteProjection, *args: Any, **kwargs: Any) -> Any:
        result = original_build(self, *args, **kwargs)
        temporary_built.set()
        return result

    def observed_replace(source: Path, destination: Path) -> None:
        replace_started.set()
        original_replace(source, destination)

    monkeypatch.setattr(SQLiteProjection, "_build_temporary_database", paused_build)
    monkeypatch.setattr(projection_module, "_replace_with_retry", observed_replace)
    failures: list[BaseException] = []

    def rebuild() -> None:
        try:
            writer.rebuild()
        except BaseException as exc:  # pragma: no cover - asserted through failures
            failures.append(exc)

    with reader._reader_connection() as connection:
        old = connection.execute(
            "SELECT title FROM entities WHERE id = 'SYS-identity-service'"
        ).fetchone()
        assert old is not None
        assert old["title"] == "Identity Service"
        thread = threading.Thread(target=rebuild)
        thread.start()
        assert temporary_built.wait(timeout=5)
        assert not replace_started.wait(timeout=0.1)
        assert thread.is_alive()

    thread.join(timeout=5)

    assert not thread.is_alive()
    assert replace_started.is_set()
    assert failures == []
    new = reader.get_entity_by_id("SYS-identity-service")
    assert new is not None
    assert (new.title, new.aliases) == (
        "Replacement Identity",
        ("Replacement IdP",),
    )


@pytest.mark.integration
def test_failed_swap_preserves_old_projection_and_cleans_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    projection.rebuild()
    rewrite_entity(paths["system"], title="Unswapped Identity", aliases=["Unswapped"])

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("injected replacement failure")

    monkeypatch.setattr(projection_module.os, "replace", fail_replace)
    with pytest.raises(ProjectionBuildError, match="prior database is intact"):
        projection.rebuild()

    old = projection.get_entity_by_id("SYS-identity-service")
    assert old is not None
    assert old.title == "Identity Service"
    assert {path.name for path in (root / "98_state").iterdir()} == {"index.sqlite3"}


def test_state_database_symlink_escape_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"not a database")
    database_path = root / "98_state" / "index.sqlite3"
    try:
        database_path.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are unavailable for this test user")

    with pytest.raises(ContextIsolationError, match="symbolic link"):
        SQLiteProjection(root)


def test_state_directory_replacement_after_construction_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    state_path = root / "98_state"
    retained_state = root / "retained-state"
    outside = tmp_path / "outside-state"
    outside.mkdir()
    state_path.rename(retained_state)
    try:
        state_path.symlink_to(outside, target_is_directory=True)
    except OSError:
        retained_state.rename(state_path)
        pytest.skip("Symbolic links are unavailable for this test user")

    with pytest.raises(ContextIsolationError, match="state directory"):
        projection.rebuild()
    assert list(outside.iterdir()) == []


def test_state_directory_swap_during_temp_creation_leaves_no_outside_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    state_path = root / "98_state"
    retained_state = root / "retained-state"
    outside = tmp_path / "outside-state"
    outside.mkdir()
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(outside, target_is_directory=True)
        probe.unlink()
    except OSError:
        pytest.skip("Symbolic links are unavailable for this test user")
    original_mkstemp = projection_module.tempfile.mkstemp
    redirected = False

    def redirected_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        nonlocal redirected
        redirected = True
        state_path.rename(retained_state)
        state_path.symlink_to(outside, target_is_directory=True)
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(projection_module.tempfile, "mkstemp", redirected_mkstemp)

    with pytest.raises(ContextIsolationError, match="outside the state directory"):
        projection.rebuild()

    assert redirected
    assert list(outside.iterdir()) == []
    assert list(retained_state.iterdir()) == []


def test_context_configuration_replacement_after_construction_is_denied(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    context_path = root / "context.yaml"
    retained_context = root / "retained-context.yaml"
    outside = tmp_path / "outside-context.yaml"
    context_path.rename(retained_context)
    shutil.copyfile(retained_context, outside)
    try:
        context_path.symlink_to(outside)
    except OSError:
        retained_context.rename(context_path)
        pytest.skip("Symbolic links are unavailable for this test user")

    with pytest.raises(ContextIsolationError, match="outside the context root"):
        projection.rebuild()


def test_live_destination_sidecar_refuses_swap_and_preserves_old_database(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    paths = create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    projection.rebuild()
    rewrite_entity(paths["system"], title="Unsafe replacement", aliases=["Unsafe"])
    external_writer = sqlite3.connect(projection.database_path)
    assert external_writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    external_writer.execute("BEGIN IMMEDIATE")
    external_writer.execute(
        "UPDATE entities SET title = 'Uncommitted title' WHERE id = 'SYS-identity-service'"
    )
    assert projection.database_path.with_name("index.sqlite3-wal").exists()
    try:
        with pytest.raises(ProjectionBuildError, match=r"WAL|sidecars|busy"):
            projection.rebuild()
    finally:
        external_writer.rollback()
        external_writer.close()

    old = projection.get_entity_by_id("SYS-identity-service")
    assert old is not None
    assert old.title == "Identity Service"


def test_orphaned_sidecars_refuse_rebuild_when_main_database_is_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    wal_path = projection.database_path.with_name("index.sqlite3-wal")
    shm_path = projection.database_path.with_name("index.sqlite3-shm")
    wal_path.write_bytes(b"fictional orphaned WAL")
    shm_path.write_bytes(b"fictional orphaned shared memory")

    with pytest.raises(ProjectionBuildError, match="Orphaned SQLite sidecars"):
        projection.ensure_ready()

    assert not projection.database_path.exists()
    assert wal_path.read_bytes() == b"fictional orphaned WAL"
    assert shm_path.read_bytes() == b"fictional orphaned shared memory"
    assert not any((root / "98_state").glob("index.sqlite3.*.tmp"))


@pytest.mark.parametrize(
    ("suffix", "target_exists"),
    (("-shm", True), ("-wal", False)),
)
def test_sidecar_symlink_is_denied_without_touching_its_target(
    tmp_path: Path,
    suffix: str,
    target_exists: bool,
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")
    projection = SQLiteProjection(root)
    projection.rebuild()
    outside = tmp_path / f"outside{suffix}"
    sentinel = b"fictional outside sidecar sentinel"
    if target_exists:
        outside.write_bytes(sentinel)
    sidecar = projection.database_path.with_name(f"index.sqlite3{suffix}")
    try:
        sidecar.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are unavailable for this test user")

    with pytest.raises(ContextIsolationError, match="sidecars cannot be symbolic links"):
        projection.rebuild()

    assert sidecar.is_symlink()
    if target_exists:
        assert outside.read_bytes() == sentinel
    else:
        assert not outside.exists()
    sidecar.unlink()
    assert projection.get_entity_by_id("SYS-identity-service") is not None


def test_fts5_unavailable_is_typed_and_leaves_no_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fictional-context"
    create_fictional_context(root, "fictional-context")

    def unavailable(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(projection_module, "create_schema", unavailable)
    projection = SQLiteProjection(root)

    with pytest.raises(Fts5UnavailableError, match="FTS5"):
        projection.rebuild()
    assert list((root / "98_state").iterdir()) == []


def test_replace_retries_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "replacement.sqlite3"
    destination = tmp_path / "index.sqlite3"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    attempts = 0
    original_replace = projection_module.os.replace

    def transient_replace(current: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("fictional sharing violation")
        original_replace(current, target)

    monkeypatch.setattr(projection_module.os, "replace", transient_replace)
    monkeypatch.setattr(projection_module, "_REPLACE_RETRY_SECONDS", 0)

    projection_module._replace_with_retry(source, destination)

    assert attempts == 2
    assert destination.read_bytes() == b"new"


def test_projection_errors_map_to_stable_exit_codes() -> None:
    assert exit_code_for(ContextIsolationError("denied")) is ExitCode.CONTEXT_BOUNDARY
    assert exit_code_for(Fts5UnavailableError("missing")) is ExitCode.UNAVAILABLE_DEPENDENCY
    assert exit_code_for(ProjectionBuildError("failed")) is ExitCode.USER_CORRECTABLE


def test_schema_context_guards_fail_closed_without_metadata(tmp_path: Path) -> None:
    database = tmp_path / "guard.sqlite3"
    connection = sqlite3.connect(database)
    try:
        create_schema(connection)
        with pytest.raises(sqlite3.IntegrityError, match="context isolation violation"):
            connection.execute(
                """
                INSERT INTO entities (
                    context_id, id, entity_type, uri, title, status, confidence,
                    body, source_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "unbound-context",
                    "SYS-unbound",
                    "system",
                    "workctx://unbound-context/system/SYS-unbound",
                    "Unbound row",
                    "active",
                    "high",
                    "Fictional body",
                    "02_knowledge/unbound.md",
                    "2026-07-30T12:00:00+00:00",
                    "2026-07-30T12:00:00+00:00",
                ),
            )
    finally:
        connection.close()
