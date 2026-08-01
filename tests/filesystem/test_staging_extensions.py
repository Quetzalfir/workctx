from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from workctx.adapters.filesystem import lock as lock_module
from workctx.adapters.filesystem import staging as staging_module
from workctx.adapters.filesystem.lock import ContextLock, LockFenceError
from workctx.adapters.filesystem.staging import (
    IntentRecord,
    IntentTargetKind,
    InvalidIntentError,
    RecoverableReplaceError,
    RecoveryRequiredError,
    RecoveryState,
    ReplaceRetryPolicy,
    StagedDelete,
    StagedMove,
    StagedReplacement,
    StagedWrite,
    StagingError,
    atomic_append_line_bytes,
)
from workctx.errors import ContextBoundaryError
from workctx.services.contexts import initialize_context


@pytest.fixture
def context_root(tmp_path: Path) -> Path:
    root = tmp_path / "context"
    initialize_context(root, name="Extension Context", context_id="extension-context")
    return root


def _lock(context_root: Path, session_id: str) -> ContextLock:
    return ContextLock.acquire(context_root, session_id=session_id, tool_version="test")


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _swap_directory_for_symlink(directory: Path, redirect: Path) -> Path:
    original = directory.with_name(f"{directory.name}-original")
    directory.rename(original)
    try:
        directory.symlink_to(redirect, target_is_directory=True)
    except OSError as exc:
        original.rename(directory)
        pytest.skip(f"Directory symlink creation is unavailable for this test user: {exc}")
    return original


def _extended_move_target(**overrides: object) -> dict[str, object]:
    preimage_hash = _sha256(b"source\n")
    target: dict[str, object] = {
        "target": "02_knowledge/source.md",
        "staged": None,
        "content_hash": preimage_hash,
        "backup": ("98_state/staging/transactions/txn-aaaaaaaaaaaaaaaaaaaaaaaa/00000000.backup"),
        "preimage_hash": preimage_hash,
        "kind": "move",
        "destination": "03_work/destination.md",
    }
    target.update(overrides)
    return target


def test_legacy_replace_intent_round_trips_with_identical_bytes() -> None:
    content_hash = _sha256(b"new\n")
    preimage_hash = _sha256(b"old\n")
    legacy = (
        "{\n"
        '  "schema_version": 1,\n'
        '  "transaction_id": "TXN-LEGACY",\n'
        f'  "nonce": "{"a" * 32}",\n'
        '  "targets": [\n'
        "    {\n"
        '      "target": "02_knowledge/legacy.md",\n'
        '      "staged": '
        '"98_state/staging/transactions/txn-aaaaaaaaaaaaaaaaaaaaaaaa/00000000.stage",\n'
        f'      "content_hash": "{content_hash}",\n'
        '      "backup": '
        '"98_state/staging/transactions/txn-aaaaaaaaaaaaaaaaaaaaaaaa/00000000.backup",\n'
        f'      "preimage_hash": "{preimage_hash}"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    ).encode()

    record = IntentRecord.from_dict(json.loads(legacy))
    encoded = (json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n").encode()

    assert encoded == legacy
    assert record.targets[0].kind is IntentTargetKind.REPLACE
    assert record.targets[0].destination is None
    assert "kind" not in record.targets[0].to_dict()


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "replace", "destination": None},
        {"kind": "unknown"},
        {"staged": ("98_state/staging/transactions/txn-aaaaaaaaaaaaaaaaaaaaaaaa/00000000.stage")},
        {"content_hash": _sha256(b"different\n")},
        {"kind": "delete", "destination": None},
    ],
)
def test_malformed_extended_intent_target_shapes_are_rejected(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        IntentRecord.from_dict(
            {
                "schema_version": 1,
                "transaction_id": "TXN-INVALID-EXTENDED",
                "nonce": "a" * 32,
                "targets": [_extended_move_target(**overrides)],
            }
        )


def test_prepare_records_move_and_delete_kinds_with_preimage_backups(
    context_root: Path,
) -> None:
    source = context_root / "02_knowledge" / "move-source.md"
    deleted = context_root / "03_work" / "delete-target.md"
    source.write_bytes(b"move bytes\n")
    deleted.write_bytes(b"delete bytes\n")

    with _lock(context_root, "prepare-kinds") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-KINDS",
            holder.nonce,
            [
                StagedMove(
                    "02_knowledge/move-source.md",
                    "02_knowledge/move-destination.md",
                ),
                StagedDelete("03_work/delete-target.md"),
            ],
            lock=holder,
        )

        move, delete = intent.targets
        assert move.kind is IntentTargetKind.MOVE
        assert move.destination == "02_knowledge/move-destination.md"
        assert move.staged is None
        assert move.content_hash == move.preimage_hash == _sha256(b"move bytes\n")
        assert move.backup is not None
        assert Path(context_root, move.backup).read_bytes() == b"move bytes\n"

        assert delete.kind is IntentTargetKind.DELETE
        assert delete.destination is None
        assert delete.staged is None
        assert delete.content_hash is None
        assert delete.preimage_hash == _sha256(b"delete bytes\n")
        assert delete.backup is not None
        assert Path(context_root, delete.backup).read_bytes() == b"delete bytes\n"

        raw_targets = json.loads(
            (context_root / "98_state" / "staging" / "intent.json").read_bytes()
        )["targets"]
        assert [item["kind"] for item in raw_targets] == ["move", "delete"]
        assert stager.inspect_recovery().state is RecoveryState.PREPARED
        assert stager.inspect_recovery().pending_targets == (
            "02_knowledge/move-source.md",
            "03_work/delete-target.md",
        )


def test_move_apply_inspect_finalize(context_root: Path) -> None:
    source = context_root / "02_knowledge" / "apply-source.md"
    destination = context_root / "03_work" / "apply-destination.md"
    source.write_bytes(b"movable\n")

    with _lock(context_root, "move-apply") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-MOVE-APPLY",
            holder.nonce,
            [StagedMove("02_knowledge/apply-source.md", "03_work/apply-destination.md")],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        stager.complete_recovery(intent, lock=holder)

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
        assert inspection.applied_targets == ("02_knowledge/apply-source.md",)
        assert inspection.targets[0].kind is IntentTargetKind.MOVE
        assert inspection.targets[0].destination_hash == _sha256(b"movable\n")
        assert not source.exists()
        assert destination.read_bytes() == b"movable\n"

        stager.finalize_after_audit("TXN-MOVE-APPLY", lock=holder)
        assert stager.inspect_recovery().state is RecoveryState.CLEAN


def test_delete_apply_inspect_finalize(context_root: Path) -> None:
    target = context_root / "05_outbox" / "delete-applied.md"
    target.write_bytes(b"generated\n")

    with _lock(context_root, "delete-apply") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-DELETE-APPLY",
            holder.nonce,
            [StagedDelete("05_outbox/delete-applied.md")],
            lock=holder,
        )
        stager.apply(intent, lock=holder)

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
        assert inspection.applied_targets == ("05_outbox/delete-applied.md",)
        assert inspection.targets[0].kind is IntentTargetKind.DELETE
        assert inspection.targets[0].current_hash is None
        assert not target.exists()

        stager.finalize_after_audit("TXN-DELETE-APPLY", lock=holder)
        assert stager.inspect_recovery().state is RecoveryState.CLEAN


def test_partial_move_sequence_can_complete(context_root: Path) -> None:
    first_source = context_root / "02_knowledge" / "move-first.md"
    second_source = context_root / "03_work" / "move-second.md"
    first_destination = context_root / "04_views" / "move-first.md"
    second_destination = context_root / "05_outbox" / "move-second.md"
    first_source.write_bytes(b"first\n")
    second_source.write_bytes(b"second\n")
    calls = 0

    def fail_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected move failure")
        os.replace(source, destination)

    with _lock(context_root, "move-complete") as holder:
        stager = StagedReplacement(context_root, replace_function=fail_second)
        intent = stager.prepare(
            "TXN-MOVE-COMPLETE",
            holder.nonce,
            [
                StagedMove("02_knowledge/move-first.md", "04_views/move-first.md"),
                StagedMove("03_work/move-second.md", "05_outbox/move-second.md"),
            ],
            lock=holder,
        )
        with pytest.raises(OSError, match="move failure"):
            stager.apply(intent, lock=holder)

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.PARTIALLY_APPLIED
        assert inspection.applied_targets == ("02_knowledge/move-first.md",)
        assert inspection.pending_targets == ("03_work/move-second.md",)

        StagedReplacement(context_root).complete_recovery(intent, lock=holder)
        assert not first_source.exists()
        assert not second_source.exists()
        assert first_destination.read_bytes() == b"first\n"
        assert second_destination.read_bytes() == b"second\n"


def test_partial_move_sequence_can_roll_back_and_finalize(context_root: Path) -> None:
    first_source = context_root / "02_knowledge" / "move-rb-first.md"
    second_source = context_root / "03_work" / "move-rb-second.md"
    first_destination = context_root / "04_views" / "move-rb-first.md"
    second_destination = context_root / "05_outbox" / "move-rb-second.md"
    first_source.write_bytes(b"first original\n")
    second_source.write_bytes(b"second original\n")
    calls = 0

    def fail_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected move rollback point")
        os.replace(source, destination)

    with _lock(context_root, "move-rollback") as holder:
        stager = StagedReplacement(context_root, replace_function=fail_second)
        intent = stager.prepare(
            "TXN-MOVE-ROLLBACK",
            holder.nonce,
            [
                StagedMove("02_knowledge/move-rb-first.md", "04_views/move-rb-first.md"),
                StagedMove("03_work/move-rb-second.md", "05_outbox/move-rb-second.md"),
            ],
            lock=holder,
        )
        with pytest.raises(OSError, match="rollback point"):
            stager.apply(intent, lock=holder)

        StagedReplacement(context_root).rollback(intent, lock=holder)
        assert first_source.read_bytes() == b"first original\n"
        assert second_source.read_bytes() == b"second original\n"
        assert not first_destination.exists()
        assert not second_destination.exists()
        assert stager.inspect_recovery().state is RecoveryState.PREPARED

        stager.finalize_rollback_after_audit("TXN-MOVE-ROLLBACK", lock=holder)
        assert stager.inspect_recovery().state is RecoveryState.CLEAN


def test_partial_delete_sequence_can_complete(context_root: Path) -> None:
    first = context_root / "03_work" / "delete-first.md"
    second = context_root / "05_outbox" / "delete-second.md"
    first.write_bytes(b"first\n")
    second.write_bytes(b"second\n")
    calls = 0

    def fail_second(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected delete failure")
        path.unlink()

    with _lock(context_root, "delete-complete") as holder:
        stager = StagedReplacement(context_root, unlink_function=fail_second)
        intent = stager.prepare(
            "TXN-DELETE-COMPLETE",
            holder.nonce,
            [StagedDelete("03_work/delete-first.md"), StagedDelete("05_outbox/delete-second.md")],
            lock=holder,
        )
        with pytest.raises(OSError, match="delete failure"):
            stager.apply(intent, lock=holder)

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.PARTIALLY_APPLIED
        assert inspection.applied_targets == ("03_work/delete-first.md",)
        assert inspection.pending_targets == ("05_outbox/delete-second.md",)

        StagedReplacement(context_root).complete_recovery(intent, lock=holder)
        assert not first.exists()
        assert not second.exists()


def test_partial_delete_sequence_can_roll_back_and_finalize(context_root: Path) -> None:
    first = context_root / "03_work" / "delete-rb-first.md"
    second = context_root / "05_outbox" / "delete-rb-second.md"
    first.write_bytes(b"first original\n")
    second.write_bytes(b"second original\n")
    calls = 0

    def fail_second(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected delete rollback point")
        path.unlink()

    with _lock(context_root, "delete-rollback") as holder:
        stager = StagedReplacement(context_root, unlink_function=fail_second)
        intent = stager.prepare(
            "TXN-DELETE-ROLLBACK",
            holder.nonce,
            [
                StagedDelete("03_work/delete-rb-first.md"),
                StagedDelete("05_outbox/delete-rb-second.md"),
            ],
            lock=holder,
        )
        with pytest.raises(OSError, match="rollback point"):
            stager.apply(intent, lock=holder)

        StagedReplacement(context_root).rollback(intent, lock=holder)
        assert first.read_bytes() == b"first original\n"
        assert second.read_bytes() == b"second original\n"
        assert stager.inspect_recovery().state is RecoveryState.PREPARED

        stager.finalize_rollback_after_audit("TXN-DELETE-ROLLBACK", lock=holder)
        assert stager.inspect_recovery().state is RecoveryState.CLEAN


def test_interrupted_move_rollback_is_inspectable_and_resumable(context_root: Path) -> None:
    first_source = context_root / "02_knowledge" / "move-rb-resume-first.md"
    second_source = context_root / "03_work" / "move-rb-resume-second.md"
    first_destination = context_root / "04_views" / "move-rb-resume-first.md"
    second_destination = context_root / "05_outbox" / "move-rb-resume-second.md"
    first_source.write_bytes(b"first\n")
    second_source.write_bytes(b"second\n")

    with _lock(context_root, "move-rollback-resume") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-MOVE-ROLLBACK-RESUME",
            holder.nonce,
            [
                StagedMove(
                    first_source.relative_to(context_root),
                    first_destination.relative_to(context_root),
                ),
                StagedMove(
                    second_source.relative_to(context_root),
                    second_destination.relative_to(context_root),
                ),
            ],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        calls = 0

        def fail_second(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected interrupted move rollback")
            os.replace(source, destination)

        with pytest.raises(OSError, match="interrupted move rollback"):
            StagedReplacement(context_root, replace_function=fail_second).rollback(
                intent, lock=holder
            )

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.PARTIALLY_APPLIED
        assert inspection.applied_targets == ("02_knowledge/move-rb-resume-first.md",)
        assert inspection.pending_targets == ("03_work/move-rb-resume-second.md",)

        stager.rollback(intent, lock=holder)
        assert first_source.read_bytes() == b"first\n"
        assert second_source.read_bytes() == b"second\n"
        assert not first_destination.exists()
        assert not second_destination.exists()


def test_interrupted_delete_rollback_is_inspectable_and_resumable(context_root: Path) -> None:
    first = context_root / "03_work" / "delete-rb-resume-first.md"
    second = context_root / "05_outbox" / "delete-rb-resume-second.md"
    first.write_bytes(b"first\n")
    second.write_bytes(b"second\n")

    with _lock(context_root, "delete-rollback-resume") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-DELETE-ROLLBACK-RESUME",
            holder.nonce,
            [
                StagedDelete(first.relative_to(context_root)),
                StagedDelete(second.relative_to(context_root)),
            ],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        calls = 0

        def fail_second(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected interrupted delete rollback")
            os.replace(source, destination)

        with pytest.raises(OSError, match="interrupted delete rollback"):
            StagedReplacement(context_root, replace_function=fail_second).rollback(
                intent, lock=holder
            )

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.PARTIALLY_APPLIED
        assert inspection.applied_targets == ("03_work/delete-rb-resume-first.md",)
        assert inspection.pending_targets == ("05_outbox/delete-rb-resume-second.md",)

        stager.rollback(intent, lock=holder)
        assert first.read_bytes() == b"first\n"
        assert second.read_bytes() == b"second\n"


def test_move_and_delete_finalizer_refuses_prepared_operations(context_root: Path) -> None:
    source = context_root / "02_knowledge" / "finalizer-source.md"
    deleted = context_root / "05_outbox" / "finalizer-delete.md"
    source.write_bytes(b"source\n")
    deleted.write_bytes(b"delete\n")

    with _lock(context_root, "operation-finalizer") as holder:
        stager = StagedReplacement(context_root)
        stager.prepare(
            "TXN-OPERATION-FINALIZER",
            holder.nonce,
            [
                StagedMove("02_knowledge/finalizer-source.md", "03_work/finalizer-destination.md"),
                StagedDelete("05_outbox/finalizer-delete.md"),
            ],
            lock=holder,
        )
        with pytest.raises(RecoveryRequiredError, match="recorded postimage"):
            stager.finalize_after_audit("TXN-OPERATION-FINALIZER", lock=holder)

        assert stager.inspect_recovery().state is RecoveryState.PREPARED


def test_applied_move_finalizer_retains_recovery_assets_when_source_parent_is_missing(
    context_root: Path,
) -> None:
    source_parent = context_root / "02_knowledge" / "missing-move-source-parent"
    source_parent.mkdir()
    source = source_parent / "source.md"
    source.write_bytes(b"source\n")

    with _lock(context_root, "missing-move-source-parent") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-MISSING-MOVE-SOURCE-PARENT",
            holder.nonce,
            [
                StagedMove(
                    "02_knowledge/missing-move-source-parent/source.md",
                    "03_work/moved-from-missing-parent.md",
                )
            ],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        source_parent.rmdir()

        assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
        with pytest.raises(RecoveryRequiredError, match="parent is unavailable"):
            stager.finalize_after_audit("TXN-MISSING-MOVE-SOURCE-PARENT", lock=holder)

        assert (context_root / "98_state" / "staging" / "intent.json").is_file()
        assert intent.targets[0].backup is not None
        assert Path(context_root, intent.targets[0].backup).is_file()


def test_applied_delete_recovery_finalizer_retains_assets_when_parent_is_missing(
    context_root: Path,
) -> None:
    target_parent = context_root / "05_outbox" / "missing-delete-parent"
    target_parent.mkdir()
    target = target_parent / "target.md"
    target.write_bytes(b"delete\n")

    with _lock(context_root, "missing-delete-parent") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-MISSING-DELETE-PARENT",
            holder.nonce,
            [StagedDelete("05_outbox/missing-delete-parent/target.md")],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        target_parent.rmdir()

        assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
        with pytest.raises(RecoveryRequiredError, match="parent is unavailable"):
            stager.finalize_recovery_after_audit(
                "TXN-MISSING-DELETE-PARENT",
                lock=holder,
            )

        assert (context_root / "98_state" / "staging" / "intent.json").is_file()
        assert intent.targets[0].backup is not None
        assert Path(context_root, intent.targets[0].backup).is_file()


def test_move_rollback_finalizer_refuses_a_missing_destination_parent(
    context_root: Path,
) -> None:
    source = context_root / "02_knowledge" / "rollback-parent-source.md"
    destination_parent = context_root / "03_work" / "missing-rollback-destination-parent"
    destination_parent.mkdir()
    source.write_bytes(b"source\n")

    with _lock(context_root, "missing-rollback-destination-parent") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-MISSING-ROLLBACK-DESTINATION-PARENT",
            holder.nonce,
            [
                StagedMove(
                    "02_knowledge/rollback-parent-source.md",
                    "03_work/missing-rollback-destination-parent/destination.md",
                )
            ],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        stager.rollback(intent, lock=holder)
        destination_parent.rmdir()

        assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT
        with pytest.raises(RecoveryRequiredError, match="destination parent is unavailable"):
            stager.finalize_rollback_after_audit(
                "TXN-MISSING-ROLLBACK-DESTINATION-PARENT",
                lock=holder,
            )
        assert (context_root / "98_state" / "staging" / "intent.json").is_file()


def test_corrupt_move_backup_is_a_recovery_conflict(context_root: Path) -> None:
    source = context_root / "02_knowledge" / "move-backup-conflict.md"
    source.write_bytes(b"source\n")

    with _lock(context_root, "move-backup-conflict") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-MOVE-BACKUP-CONFLICT",
            holder.nonce,
            [StagedMove("02_knowledge/move-backup-conflict.md", "03_work/move-backup-conflict.md")],
            lock=holder,
        )
        backup = intent.targets[0].backup
        assert backup is not None
        Path(context_root, backup).write_bytes(b"corrupt\n")

        assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT


def test_mixed_sequence_can_complete_after_delete_failure(context_root: Path) -> None:
    replaced = context_root / "02_knowledge" / "mixed-replace.md"
    move_source = context_root / "03_work" / "mixed-move.md"
    move_destination = context_root / "04_views" / "mixed-move.md"
    deleted = context_root / "05_outbox" / "mixed-delete.md"
    replaced.write_bytes(b"old replace\n")
    move_source.write_bytes(b"move\n")
    deleted.write_bytes(b"delete\n")

    def fail_delete(_path: Path) -> None:
        raise OSError("injected mixed delete failure")

    with _lock(context_root, "mixed-complete") as holder:
        stager = StagedReplacement(context_root, unlink_function=fail_delete)
        intent = stager.prepare(
            "TXN-MIXED-COMPLETE",
            holder.nonce,
            [
                StagedWrite("02_knowledge/mixed-replace.md", b"new replace\n"),
                StagedMove("03_work/mixed-move.md", "04_views/mixed-move.md"),
                StagedDelete("05_outbox/mixed-delete.md"),
            ],
            lock=holder,
        )
        with pytest.raises(OSError, match="mixed delete"):
            stager.apply(intent, lock=holder)

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.PARTIALLY_APPLIED
        assert inspection.applied_targets == (
            "02_knowledge/mixed-replace.md",
            "03_work/mixed-move.md",
        )
        assert inspection.pending_targets == ("05_outbox/mixed-delete.md",)

        StagedReplacement(context_root).complete_recovery(intent, lock=holder)
        assert replaced.read_bytes() == b"new replace\n"
        assert not move_source.exists()
        assert move_destination.read_bytes() == b"move\n"
        assert not deleted.exists()


def test_mixed_sequence_can_roll_back_after_late_failure(context_root: Path) -> None:
    replaced = context_root / "02_knowledge" / "mixed-rb-replace.md"
    move_source = context_root / "03_work" / "mixed-rb-move.md"
    move_destination = context_root / "04_views" / "mixed-rb-move.md"
    deleted = context_root / "05_outbox" / "mixed-rb-delete.md"
    final = context_root / "02_knowledge" / "mixed-rb-final.md"
    replaced.write_bytes(b"old replace\n")
    move_source.write_bytes(b"move original\n")
    deleted.write_bytes(b"delete original\n")
    final.write_bytes(b"old final\n")
    replace_calls = 0

    def fail_third_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 3:
            raise OSError("injected late mixed failure")
        os.replace(source, destination)

    with _lock(context_root, "mixed-rollback") as holder:
        stager = StagedReplacement(context_root, replace_function=fail_third_replace)
        intent = stager.prepare(
            "TXN-MIXED-ROLLBACK",
            holder.nonce,
            [
                StagedWrite("02_knowledge/mixed-rb-replace.md", b"new replace\n"),
                StagedMove("03_work/mixed-rb-move.md", "04_views/mixed-rb-move.md"),
                StagedDelete("05_outbox/mixed-rb-delete.md"),
                StagedWrite("02_knowledge/mixed-rb-final.md", b"new final\n"),
            ],
            lock=holder,
        )
        with pytest.raises(OSError, match="late mixed"):
            stager.apply(intent, lock=holder)

        inspection = stager.inspect_recovery()
        assert inspection.state is RecoveryState.PARTIALLY_APPLIED
        assert inspection.applied_targets == (
            "02_knowledge/mixed-rb-replace.md",
            "03_work/mixed-rb-move.md",
            "05_outbox/mixed-rb-delete.md",
        )
        assert inspection.pending_targets == ("02_knowledge/mixed-rb-final.md",)

        StagedReplacement(context_root).rollback(intent, lock=holder)
        assert replaced.read_bytes() == b"old replace\n"
        assert move_source.read_bytes() == b"move original\n"
        assert not move_destination.exists()
        assert deleted.read_bytes() == b"delete original\n"
        assert final.read_bytes() == b"old final\n"


@pytest.mark.parametrize("persistent", [False, True])
def test_move_permission_error_retry_policy(context_root: Path, persistent: bool) -> None:
    source = context_root / "02_knowledge" / f"move-retry-{persistent}.md"
    destination = context_root / "03_work" / f"move-retry-{persistent}.md"
    source.write_bytes(b"retry move\n")
    attempts = 0
    delays: list[float] = []

    def flaky(source_path: Path, destination_path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if persistent or attempts < 3:
            raise PermissionError("injected move sharing violation")
        os.replace(source_path, destination_path)

    policy = ReplaceRetryPolicy(max_attempts=3, initial_delay_seconds=0.01, multiplier=2)
    with _lock(context_root, f"move-retry-{persistent}") as holder:
        stager = StagedReplacement(
            context_root,
            retry_policy=policy,
            replace_function=flaky,
            sleep_function=delays.append,
        )
        intent = stager.prepare(
            f"TXN-MOVE-RETRY-{persistent}",
            holder.nonce,
            [StagedMove(source.relative_to(context_root), destination.relative_to(context_root))],
            lock=holder,
        )
        if persistent:
            with pytest.raises(RecoverableReplaceError, match="3 attempts"):
                stager.apply(intent, lock=holder)
            assert source.read_bytes() == b"retry move\n"
            assert not destination.exists()
            assert stager.inspect_recovery().state is RecoveryState.PREPARED
        else:
            stager.apply(intent, lock=holder)
            assert not source.exists()
            assert destination.read_bytes() == b"retry move\n"
        assert attempts == 3
        assert delays == [0.01, 0.02]


@pytest.mark.parametrize("persistent", [False, True])
def test_delete_permission_error_retry_policy(context_root: Path, persistent: bool) -> None:
    target = context_root / "05_outbox" / f"delete-retry-{persistent}.md"
    target.write_bytes(b"retry delete\n")
    attempts = 0
    delays: list[float] = []

    def flaky(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if persistent or attempts < 3:
            raise PermissionError("injected delete sharing violation")
        path.unlink()

    policy = ReplaceRetryPolicy(max_attempts=3, initial_delay_seconds=0.01, multiplier=2)
    with _lock(context_root, f"delete-retry-{persistent}") as holder:
        stager = StagedReplacement(
            context_root,
            retry_policy=policy,
            unlink_function=flaky,
            sleep_function=delays.append,
        )
        intent = stager.prepare(
            f"TXN-DELETE-RETRY-{persistent}",
            holder.nonce,
            [StagedDelete(target.relative_to(context_root))],
            lock=holder,
        )
        if persistent:
            with pytest.raises(RecoverableReplaceError, match="3 attempts"):
                stager.apply(intent, lock=holder)
            assert target.read_bytes() == b"retry delete\n"
            assert stager.inspect_recovery().state is RecoveryState.PREPARED
        else:
            stager.apply(intent, lock=holder)
            assert not target.exists()
        assert attempts == 3
        assert delays == [0.01, 0.02]


def test_move_retry_rejects_old_holder_after_takeover(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = context_root / "02_knowledge" / "move-takeover-source.md"
    destination = context_root / "03_work" / "move-takeover-destination.md"
    source.write_bytes(b"source\n")
    old = ContextLock.acquire(context_root, session_id="move-old", tool_version="test")
    successor: ContextLock | None = None
    attempts = 0

    def blocked(_source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("injected move takeover retry")

    def take_over(_seconds: float) -> None:
        nonlocal successor
        monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
        successor = ContextLock.acquire(
            context_root,
            session_id="move-successor",
            tool_version="test",
        )

    stager = StagedReplacement(
        context_root,
        replace_function=blocked,
        sleep_function=take_over,
    )
    intent = stager.prepare(
        "TXN-MOVE-TAKEOVER",
        old.nonce,
        [StagedMove(source.relative_to(context_root), destination.relative_to(context_root))],
        lock=old,
    )

    with pytest.raises(LockFenceError):
        stager.apply(intent, lock=old)

    assert successor is not None
    assert attempts == 1
    assert source.read_bytes() == b"source\n"
    assert not destination.exists()
    successor.release()


def test_delete_retry_fences_before_accepting_absence_after_takeover(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "05_outbox" / "delete-takeover.md"
    target.write_bytes(b"delete\n")
    old = ContextLock.acquire(context_root, session_id="delete-old", tool_version="test")
    successor: ContextLock | None = None
    attempts = 0

    def blocked(_path: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("injected delete takeover retry")

    def take_over_and_remove(_seconds: float) -> None:
        nonlocal successor
        monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
        successor = ContextLock.acquire(
            context_root,
            session_id="delete-successor",
            tool_version="test",
        )
        target.unlink()

    stager = StagedReplacement(
        context_root,
        unlink_function=blocked,
        sleep_function=take_over_and_remove,
    )
    intent = stager.prepare(
        "TXN-DELETE-TAKEOVER",
        old.nonce,
        [StagedDelete(target.relative_to(context_root))],
        lock=old,
    )

    with pytest.raises(LockFenceError):
        stager.apply(intent, lock=old)

    assert successor is not None
    assert attempts == 1
    assert not target.exists()
    successor.release()


def test_inverse_move_and_delete_restore_retry_transient_permission_errors(
    context_root: Path,
) -> None:
    source = context_root / "02_knowledge" / "inverse-move-source.md"
    destination = context_root / "03_work" / "inverse-move-destination.md"
    deleted = context_root / "05_outbox" / "inverse-delete.md"
    source.write_bytes(b"move\n")
    deleted.write_bytes(b"delete\n")

    with _lock(context_root, "inverse-retries") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-INVERSE-RETRIES",
            holder.nonce,
            [
                StagedMove(source.relative_to(context_root), destination.relative_to(context_root)),
                StagedDelete(deleted.relative_to(context_root)),
            ],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        attempts = 0
        delays: list[float] = []

        def transient(source_path: Path, target_path: Path) -> None:
            nonlocal attempts
            attempts += 1
            if attempts in {1, 2, 4, 5}:
                raise PermissionError("injected inverse sharing violation")
            os.replace(source_path, target_path)

        StagedReplacement(
            context_root,
            replace_function=transient,
            sleep_function=delays.append,
        ).rollback(intent, lock=holder)

        assert attempts == 6
        assert delays == [0.01, 0.02, 0.01, 0.02]
        assert source.read_bytes() == b"move\n"
        assert not destination.exists()
        assert deleted.read_bytes() == b"delete\n"
        assert stager.inspect_recovery().state is RecoveryState.PREPARED


def test_delete_restore_retry_rejects_old_holder_after_takeover(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = context_root / "05_outbox" / "delete-restore-takeover.md"
    target.write_bytes(b"delete\n")
    old = ContextLock.acquire(
        context_root,
        session_id="delete-restore-old",
        tool_version="test",
    )
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-DELETE-RESTORE-TAKEOVER",
        old.nonce,
        [StagedDelete(target.relative_to(context_root))],
        lock=old,
    )
    stager.apply(intent, lock=old)
    successor: ContextLock | None = None
    attempts = 0

    def blocked(_source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("injected delete restore takeover retry")

    def take_over(_seconds: float) -> None:
        nonlocal successor
        monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
        successor = ContextLock.acquire(
            context_root,
            session_id="delete-restore-successor",
            tool_version="test",
        )

    with pytest.raises(LockFenceError):
        StagedReplacement(
            context_root,
            replace_function=blocked,
            sleep_function=take_over,
        ).rollback(intent, lock=old)

    assert successor is not None
    assert attempts == 1
    assert not target.exists()
    StagedReplacement(context_root).rollback_recovery(intent, lock=successor)
    assert target.read_bytes() == b"delete\n"
    StagedReplacement(context_root).finalize_rollback_after_audit(
        "TXN-DELETE-RESTORE-TAKEOVER",
        lock=successor,
    )
    successor.release()


def test_move_destination_created_during_retry_is_not_overwritten(context_root: Path) -> None:
    source = context_root / "02_knowledge" / "move-race-source.md"
    destination = context_root / "03_work" / "move-race-destination.md"
    source.write_bytes(b"source\n")
    attempts = 0

    def create_destination_then_block(_source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        target.write_bytes(b"external\n")
        raise PermissionError("injected move race")

    with _lock(context_root, "move-race") as holder:
        stager = StagedReplacement(
            context_root,
            replace_function=create_destination_then_block,
            sleep_function=lambda _seconds: None,
        )
        intent = stager.prepare(
            "TXN-MOVE-RACE",
            holder.nonce,
            [StagedMove("02_knowledge/move-race-source.md", "03_work/move-race-destination.md")],
            lock=holder,
        )
        with pytest.raises(RecoveryRequiredError, match="changed during atomic replacement"):
            stager.apply(intent, lock=holder)

        assert attempts == 1
        assert source.read_bytes() == b"source\n"
        assert destination.read_bytes() == b"external\n"
        assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT


def test_delete_target_edited_during_retry_is_not_removed(context_root: Path) -> None:
    target = context_root / "05_outbox" / "delete-race.md"
    target.write_bytes(b"original\n")
    attempts = 0

    def edit_then_block(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        path.write_bytes(b"external\n")
        raise PermissionError("injected delete race")

    with _lock(context_root, "delete-race") as holder:
        stager = StagedReplacement(
            context_root,
            unlink_function=edit_then_block,
            sleep_function=lambda _seconds: None,
        )
        intent = stager.prepare(
            "TXN-DELETE-RACE",
            holder.nonce,
            [StagedDelete("05_outbox/delete-race.md")],
            lock=holder,
        )
        with pytest.raises(RecoveryRequiredError, match="changed during atomic replacement"):
            stager.apply(intent, lock=holder)

        assert attempts == 1
        assert target.read_bytes() == b"external\n"
        assert stager.inspect_recovery().state is RecoveryState.RECOVERY_CONFLICT


def test_move_retry_rejects_same_zone_parent_symlink_substitution(
    context_root: Path,
) -> None:
    source = context_root / "02_knowledge" / "move-link-source.md"
    destination_parent = context_root / "03_work" / "move-link-parent"
    redirect = context_root / "03_work" / "move-link-redirect"
    destination = destination_parent / "destination.md"
    redirected_destination = redirect / "destination.md"
    source.write_bytes(b"source\n")
    destination_parent.mkdir()
    redirect.mkdir()
    original_parent: Path | None = None

    def blocked(_source: Path, _destination: Path) -> None:
        raise PermissionError("injected move parent substitution")

    def swap_parent(_seconds: float) -> None:
        nonlocal original_parent
        original_parent = _swap_directory_for_symlink(destination_parent, redirect)

    with _lock(context_root, "move-link-swap") as holder:
        stager = StagedReplacement(
            context_root,
            replace_function=blocked,
            sleep_function=swap_parent,
        )
        intent = stager.prepare(
            "TXN-MOVE-LINK-SWAP",
            holder.nonce,
            [StagedMove(source.relative_to(context_root), destination.relative_to(context_root))],
            lock=holder,
        )
        with pytest.raises(ContextBoundaryError, match="symlink or junction"):
            stager.apply(intent, lock=holder)

    assert original_parent is not None
    assert source.read_bytes() == b"source\n"
    assert not redirected_destination.exists()


def test_delete_retry_rejects_same_zone_parent_symlink_substitution(
    context_root: Path,
) -> None:
    target_parent = context_root / "05_outbox" / "delete-link-parent"
    redirect = context_root / "05_outbox" / "delete-link-redirect"
    target = target_parent / "target.md"
    redirected_target = redirect / "target.md"
    target_parent.mkdir()
    redirect.mkdir()
    target.write_bytes(b"same bytes\n")
    redirected_target.write_bytes(b"same bytes\n")
    original_parent: Path | None = None

    def blocked(_path: Path) -> None:
        raise PermissionError("injected delete parent substitution")

    def swap_parent(_seconds: float) -> None:
        nonlocal original_parent
        original_parent = _swap_directory_for_symlink(target_parent, redirect)

    with _lock(context_root, "delete-link-swap") as holder:
        stager = StagedReplacement(
            context_root,
            unlink_function=blocked,
            sleep_function=swap_parent,
        )
        intent = stager.prepare(
            "TXN-DELETE-LINK-SWAP",
            holder.nonce,
            [StagedDelete(target.relative_to(context_root))],
            lock=holder,
        )
        with pytest.raises(ContextBoundaryError, match="symlink or junction"):
            stager.apply(intent, lock=holder)

    assert original_parent is not None
    assert (original_parent / "target.md").read_bytes() == b"same bytes\n"
    assert redirected_target.read_bytes() == b"same bytes\n"


def test_move_to_existing_destination_is_refused_without_intent(context_root: Path) -> None:
    source = context_root / "02_knowledge" / "existing-source.md"
    destination = context_root / "03_work" / "existing-destination.md"
    source.write_bytes(b"source\n")
    destination.write_bytes(b"destination\n")

    with (
        _lock(context_root, "existing-destination") as holder,
        pytest.raises(StagingError, match="destination already exists"),
    ):
        StagedReplacement(context_root).prepare(
            "TXN-EXISTING-DESTINATION",
            holder.nonce,
            [StagedMove(source.relative_to(context_root), destination.relative_to(context_root))],
            lock=holder,
        )

    assert source.read_bytes() == b"source\n"
    assert destination.read_bytes() == b"destination\n"
    assert not (context_root / "98_state" / "staging" / "intent.json").exists()


@pytest.mark.parametrize(
    "operations",
    [
        [StagedMove("../outside.md", "02_knowledge/destination.md")],
        [StagedMove("02_knowledge/source.md", "../outside.md")],
        [StagedDelete("../outside.md")],
    ],
)
def test_move_and_delete_reject_boundary_escape(
    context_root: Path,
    operations: list[StagedMove | StagedDelete],
) -> None:
    (context_root / "02_knowledge" / "source.md").write_bytes(b"source\n")

    with (
        _lock(context_root, "operation-boundary") as holder,
        pytest.raises(ContextBoundaryError),
    ):
        StagedReplacement(context_root).prepare(
            "TXN-OPERATION-ESCAPE",
            holder.nonce,
            operations,
            lock=holder,
        )


@pytest.mark.parametrize(
    ("destination", "colliding_target"),
    [
        ("03_work/Collision.md", "03_work/collision.md"),
        ("03_work/caf\u00e9.md", "03_work/cafe\u0301.md"),
    ],
)
def test_operation_paths_reject_casefold_and_unicode_collisions(
    context_root: Path,
    destination: str,
    colliding_target: str,
) -> None:
    source = context_root / "02_knowledge" / "collision-source.md"
    source.write_bytes(b"source\n")

    with (
        _lock(context_root, "operation-collision") as holder,
        pytest.raises(StagingError, match="Duplicate staged"),
    ):
        StagedReplacement(context_root).prepare(
            "TXN-OPERATION-COLLISION",
            holder.nonce,
            [
                StagedMove(source.relative_to(context_root), destination),
                StagedWrite(colliding_target, b"new\n"),
            ],
            lock=holder,
        )


def test_applied_move_and_delete_can_be_rolled_back_then_completed(context_root: Path) -> None:
    source = context_root / "02_knowledge" / "roundtrip-move-source.md"
    destination = context_root / "03_work" / "roundtrip-move-destination.md"
    deleted = context_root / "05_outbox" / "roundtrip-delete.md"
    source.write_bytes(b"move original\n")
    deleted.write_bytes(b"delete original\n")

    with _lock(context_root, "operation-roundtrip") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-OPERATION-ROUNDTRIP",
            holder.nonce,
            [
                StagedMove(source.relative_to(context_root), destination.relative_to(context_root)),
                StagedDelete(deleted.relative_to(context_root)),
            ],
            lock=holder,
        )
        stager.apply(intent, lock=holder)
        stager.rollback(intent, lock=holder)
        assert source.read_bytes() == b"move original\n"
        assert not destination.exists()
        assert deleted.read_bytes() == b"delete original\n"
        assert stager.inspect_recovery().state is RecoveryState.PREPARED

        stager.apply(intent, lock=holder)
        assert not source.exists()
        assert destination.read_bytes() == b"move original\n"
        assert not deleted.exists()


def test_move_and_delete_recovery_can_use_successor_holder(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = context_root / "02_knowledge" / "successor-move-source.md"
    destination = context_root / "03_work" / "successor-move-destination.md"
    deleted = context_root / "05_outbox" / "successor-delete.md"
    source.write_bytes(b"move\n")
    deleted.write_bytes(b"delete\n")
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: True)
    old = _lock(context_root, "old-holder")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-OPERATION-SUCCESSOR",
        old.nonce,
        [
            StagedMove(source.relative_to(context_root), destination.relative_to(context_root)),
            StagedDelete(deleted.relative_to(context_root)),
        ],
        lock=old,
    )
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
    successor = _lock(context_root, "successor-holder")
    try:
        with pytest.raises(LockFenceError, match="intent nonce"):
            stager.apply(intent, lock=successor)
        stager.complete_recovery(intent, lock=successor)
        assert destination.read_bytes() == b"move\n"
        assert not deleted.exists()
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/successor-ledger.jsonl",
            b'{"holder":"successor"}\n',
            nonce=successor.nonce,
            lock=successor,
        )
        stager.finalize_recovery_after_audit("TXN-OPERATION-SUCCESSOR", lock=successor)
        assert stager.inspect_recovery().state is RecoveryState.CLEAN
    finally:
        successor.release()


def test_successor_can_rollback_recovery_append_and_finalize(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = context_root / "02_knowledge" / "successor-rollback-source.md"
    destination = context_root / "03_work" / "successor-rollback-destination.md"
    deleted = context_root / "05_outbox" / "successor-rollback-delete.md"
    source.write_bytes(b"move\n")
    deleted.write_bytes(b"delete\n")
    old = _lock(context_root, "rollback-old-holder")
    stager = StagedReplacement(context_root)
    intent = stager.prepare(
        "TXN-SUCCESSOR-ROLLBACK",
        old.nonce,
        [
            StagedMove(source.relative_to(context_root), destination.relative_to(context_root)),
            StagedDelete(deleted.relative_to(context_root)),
        ],
        lock=old,
    )
    stager.apply(intent, lock=old)
    monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
    successor = _lock(context_root, "rollback-successor-holder")
    try:
        stager.rollback_recovery(intent, lock=successor)
        assert source.read_bytes() == b"move\n"
        assert not destination.exists()
        assert deleted.read_bytes() == b"delete\n"
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/successor-rollback-ledger.jsonl",
            b'{"recovery":"rollback"}\n',
            nonce=successor.nonce,
            lock=successor,
        )
        stager.finalize_rollback_after_audit("TXN-SUCCESSOR-ROLLBACK", lock=successor)
        assert stager.inspect_recovery().state is RecoveryState.CLEAN
    finally:
        successor.release()


def test_append_creates_in_boundary_parents_and_appends_complete_lines(
    context_root: Path,
) -> None:
    ledger = context_root / "99_meta" / "audit" / "nested" / "ledger.jsonl"

    with _lock(context_root, "append-create") as holder:
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/nested/ledger.jsonl",
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
        )
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/nested/ledger.jsonl",
            b'{"event":2}\n',
            nonce=holder.nonce,
            lock=holder,
        )

    assert ledger.read_bytes() == b'{"event":1}\n{"event":2}\n'
    assert ledger.parent.is_dir()


def test_append_works_while_intent_remains_active_and_does_not_change_it(
    context_root: Path,
) -> None:
    target = context_root / "02_knowledge" / "append-active-intent.md"
    target.write_bytes(b"old\n")
    intent_path = context_root / "98_state" / "staging" / "intent.json"

    with _lock(context_root, "append-active-intent") as holder:
        stager = StagedReplacement(context_root)
        intent = stager.prepare(
            "TXN-APPEND-ACTIVE",
            holder.nonce,
            [StagedWrite("02_knowledge/append-active-intent.md", b"new\n")],
            lock=holder,
        )
        intent_before = intent_path.read_bytes()

        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/ledger.jsonl",
            b'{"phase":"prepared"}\n',
            nonce=holder.nonce,
            lock=holder,
        )

        assert intent_path.read_bytes() == intent_before
        assert stager.inspect_recovery().state is RecoveryState.PREPARED
        stager.apply(intent, lock=holder)
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/ledger.jsonl",
            b'{"phase":"applied"}\n',
            nonce=holder.nonce,
            lock=holder,
        )
        assert intent_path.read_bytes() == intent_before
        assert stager.inspect_recovery().state is RecoveryState.FULLY_REPLACED_AWAITING_AUDIT
        stager.finalize_after_audit("TXN-APPEND-ACTIVE", lock=holder)
        assert stager.inspect_recovery().state is RecoveryState.CLEAN

    assert (context_root / "99_meta" / "audit" / "ledger.jsonl").read_bytes() == (
        b'{"phase":"prepared"}\n{"phase":"applied"}\n'
    )


def test_append_wrong_nonce_aborts_before_parent_creation(context_root: Path) -> None:
    audit_parent = context_root / "99_meta" / "wrong-nonce"

    with _lock(context_root, "append-wrong-nonce") as holder:
        wrong_nonce = "0" * 32 if holder.nonce != "0" * 32 else "1" * 32
        with pytest.raises(LockFenceError, match="intent nonce"):
            atomic_append_line_bytes(
                context_root,
                "99_meta/wrong-nonce/ledger.jsonl",
                b'{"event":1}\n',
                nonce=wrong_nonce,
                lock=holder,
            )

    assert not audit_parent.exists()


def test_append_rejects_malformed_active_intent_before_parent_creation(
    context_root: Path,
) -> None:
    audit_parent = context_root / "99_meta" / "malformed-intent-audit"

    with _lock(context_root, "append-malformed-intent") as holder:
        intent_path = context_root / "98_state" / "staging" / "intent.json"
        intent_path.write_bytes(b"{malformed")
        with pytest.raises(InvalidIntentError, match="malformed"):
            atomic_append_line_bytes(
                context_root,
                "99_meta/malformed-intent-audit/ledger.jsonl",
                b'{"event":1}\n',
                nonce=holder.nonce,
                lock=holder,
            )

    assert not audit_parent.exists()


def test_append_rejects_dangling_intent_symlink_before_parent_creation(
    context_root: Path,
) -> None:
    audit_parent = context_root / "99_meta" / "dangling-intent-audit"

    with _lock(context_root, "append-dangling-intent") as holder:
        staging = context_root / "98_state" / "staging"
        intent_path = staging / "intent.json"
        try:
            intent_path.symlink_to(staging / "missing-intent.json")
        except OSError as exc:
            pytest.skip(f"File symlink creation is unavailable for this test user: {exc}")
        with pytest.raises(ContextBoundaryError, match="symlink or junction"):
            atomic_append_line_bytes(
                context_root,
                "99_meta/dangling-intent-audit/ledger.jsonl",
                b'{"event":1}\n',
                nonce=holder.nonce,
                lock=holder,
            )

    assert not audit_parent.exists()


@pytest.mark.parametrize(
    "target",
    ["../outside.jsonl", "99_meta/../../outside.jsonl", "C:outside.jsonl", "C:/outside.jsonl"],
)
def test_append_rejects_boundary_escape(context_root: Path, target: str) -> None:
    with _lock(context_root, "append-boundary") as holder, pytest.raises(ContextBoundaryError):
        atomic_append_line_bytes(
            context_root,
            target,
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
        )


def test_append_rejects_parent_symlink_escape(context_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = context_root / "99_meta" / "linked-audit"
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlink creation is unavailable for this test user: {exc}")

    with _lock(context_root, "append-symlink") as holder, pytest.raises(ContextBoundaryError):
        atomic_append_line_bytes(
            context_root,
            "99_meta/linked-audit/ledger.jsonl",
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
        )

    assert not (outside / "ledger.jsonl").exists()


def test_append_rejects_file_parent_and_nested_context(context_root: Path) -> None:
    file_parent = context_root / "99_meta" / "not-a-directory"
    file_parent.write_bytes(b"plain file\n")
    nested = context_root / "99_meta" / "nested-context"
    nested.mkdir()
    (nested / "context.yaml").write_bytes(b"id: nested\n")

    with _lock(context_root, "append-parent-boundaries") as holder:
        with pytest.raises(ContextBoundaryError):
            atomic_append_line_bytes(
                context_root,
                "99_meta/not-a-directory/ledger.jsonl",
                b'{"event":1}\n',
                nonce=holder.nonce,
                lock=holder,
            )
        with pytest.raises(ContextBoundaryError, match="nested context"):
            atomic_append_line_bytes(
                context_root,
                "99_meta/nested-context/ledger.jsonl",
                b'{"event":1}\n',
                nonce=holder.nonce,
                lock=holder,
            )

    assert file_parent.read_bytes() == b"plain file\n"
    assert not (nested / "ledger.jsonl").exists()


@pytest.mark.parametrize("persistent", [False, True])
def test_append_permission_error_retry_policy(context_root: Path, persistent: bool) -> None:
    ledger = context_root / "99_meta" / "audit" / f"retry-{persistent}.jsonl"
    ledger.parent.mkdir()
    ledger.write_bytes(b'{"event":0}\n')
    attempts = 0
    delays: list[float] = []

    def flaky(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if persistent or attempts < 3:
            raise PermissionError("injected append sharing violation")
        os.replace(source, destination)

    policy = ReplaceRetryPolicy(max_attempts=3, initial_delay_seconds=0.01, multiplier=2)
    with _lock(context_root, f"append-retry-{persistent}") as holder:
        if persistent:
            with pytest.raises(RecoverableReplaceError, match="3 attempts"):
                atomic_append_line_bytes(
                    context_root,
                    ledger.relative_to(context_root),
                    b'{"event":1}\n',
                    nonce=holder.nonce,
                    lock=holder,
                    retry_policy=policy,
                    replace_function=flaky,
                    sleep_function=delays.append,
                )
            assert ledger.read_bytes() == b'{"event":0}\n'
        else:
            atomic_append_line_bytes(
                context_root,
                ledger.relative_to(context_root),
                b'{"event":1}\n',
                nonce=holder.nonce,
                lock=holder,
                retry_policy=policy,
                replace_function=flaky,
                sleep_function=delays.append,
            )
            assert ledger.read_bytes() == b'{"event":0}\n{"event":1}\n'

    assert attempts == 3
    assert delays == [0.01, 0.02]
    assert not list((context_root / "98_state" / "staging").glob("append-*.stage"))


def test_append_retry_rejects_old_holder_after_takeover(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = context_root / "99_meta" / "audit" / "append-takeover.jsonl"
    ledger.parent.mkdir()
    ledger.write_bytes(b'{"event":0}\n')
    old = ContextLock.acquire(context_root, session_id="append-old", tool_version="test")
    successor: ContextLock | None = None
    attempts = 0

    def blocked(_source: Path, _destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise PermissionError("injected append takeover retry")

    def take_over(_seconds: float) -> None:
        nonlocal successor
        monkeypatch.setattr(lock_module, "_pid_is_alive", lambda _pid: False)
        successor = ContextLock.acquire(
            context_root,
            session_id="append-successor",
            tool_version="test",
        )

    with pytest.raises(LockFenceError):
        atomic_append_line_bytes(
            context_root,
            ledger.relative_to(context_root),
            b'{"event":1}\n',
            nonce=old.nonce,
            lock=old,
            replace_function=blocked,
            sleep_function=take_over,
        )

    assert successor is not None
    assert attempts == 1
    assert ledger.read_bytes() == b'{"event":0}\n'
    assert not list((context_root / "98_state" / "staging").glob("append-*.stage"))
    successor.release()


def test_append_retry_rejects_same_zone_parent_symlink_substitution(
    context_root: Path,
) -> None:
    ledger_parent = context_root / "99_meta" / "append-link-parent"
    redirect = context_root / "99_meta" / "append-link-redirect"
    ledger = ledger_parent / "ledger.jsonl"
    redirected_ledger = redirect / "ledger.jsonl"
    ledger_parent.mkdir()
    redirect.mkdir()
    ledger.write_bytes(b'{"event":0}\n')
    redirected_ledger.write_bytes(b'{"event":0}\n')
    original_parent: Path | None = None

    def blocked(_source: Path, _destination: Path) -> None:
        raise PermissionError("injected append parent substitution")

    def swap_parent(_seconds: float) -> None:
        nonlocal original_parent
        original_parent = _swap_directory_for_symlink(ledger_parent, redirect)

    with (
        _lock(context_root, "append-link-swap") as holder,
        pytest.raises(ContextBoundaryError, match="symlink or junction"),
    ):
        atomic_append_line_bytes(
            context_root,
            ledger.relative_to(context_root),
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
            replace_function=blocked,
            sleep_function=swap_parent,
        )

    assert original_parent is not None
    assert (original_parent / "ledger.jsonl").read_bytes() == b'{"event":0}\n'
    assert redirected_ledger.read_bytes() == b'{"event":0}\n'


def test_append_target_edit_during_retry_is_not_overwritten(context_root: Path) -> None:
    ledger = context_root / "99_meta" / "audit" / "append-race.jsonl"
    ledger.parent.mkdir()
    ledger.write_bytes(b'{"event":0}\n')
    attempts = 0

    def edit_then_block(_source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        destination.write_bytes(b'{"external":true}\n')
        raise PermissionError("injected append race")

    with (
        _lock(context_root, "append-race") as holder,
        pytest.raises(RecoveryRequiredError, match="changed during atomic replacement"),
    ):
        atomic_append_line_bytes(
            context_root,
            ledger.relative_to(context_root),
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
            replace_function=edit_then_block,
            sleep_function=lambda _seconds: None,
        )

    assert attempts == 1
    assert ledger.read_bytes() == b'{"external":true}\n'


def test_append_torn_staging_write_never_publishes_partial_line(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = context_root / "99_meta" / "audit" / "torn.jsonl"
    ledger.parent.mkdir()
    ledger.write_bytes(b'{"event":0}\n')
    original_write = staging_module._write_fsynced
    injected = False

    def tear_append(path: Path, payload: bytes, *, exclusive: bool) -> None:
        nonlocal injected
        if path.name.startswith("append-") and not injected:
            injected = True
            path.write_bytes(payload[:-2])
            raise OSError("injected torn append staging write")
        original_write(path, payload, exclusive=exclusive)

    monkeypatch.setattr(staging_module, "_write_fsynced", tear_append)
    with (
        _lock(context_root, "append-torn") as holder,
        pytest.raises(OSError, match="torn append"),
    ):
        atomic_append_line_bytes(
            context_root,
            ledger.relative_to(context_root),
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
        )

    assert injected
    assert ledger.read_bytes() == b'{"event":0}\n'
    assert not list((context_root / "98_state" / "staging").glob("append-*.stage"))


def test_append_fsyncs_complete_postimage_before_publication(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fsync = staging_module.os.fsync
    original_directory_fsync = staging_module._fsync_directory
    fsynced: list[int] = []
    fsynced_directories: list[Path] = []

    def record_fsync(descriptor: int) -> None:
        fsynced.append(descriptor)
        original_fsync(descriptor)

    def record_directory_fsync(path: Path) -> None:
        fsynced_directories.append(path)
        original_directory_fsync(path)

    with _lock(context_root, "append-fsync") as holder:
        monkeypatch.setattr(staging_module.os, "fsync", record_fsync)
        monkeypatch.setattr(staging_module, "_fsync_directory", record_directory_fsync)
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/fsync.jsonl",
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
        )

    assert fsynced
    ledger = context_root / "99_meta" / "audit" / "fsync.jsonl"
    assert ledger.parent in fsynced_directories
    assert ledger.read_bytes() == b'{"event":1}\n'


@pytest.mark.parametrize(
    "line",
    [b"", b'{"event":1}', b'{"event":1}\n{"event":2}\n', b'{"event":1}\r\n'],
)
def test_append_rejects_invalid_line_shapes(context_root: Path, line: bytes) -> None:
    with (
        _lock(context_root, "append-line-shape") as holder,
        pytest.raises(ValueError, match="exactly one LF-terminated line"),
    ):
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/invalid.jsonl",
            line,
            nonce=holder.nonce,
            lock=holder,
        )


def test_append_rejects_non_bytes_line(context_root: Path) -> None:
    with (
        _lock(context_root, "append-line-type") as holder,
        pytest.raises(TypeError, match="must be bytes"),
    ):
        atomic_append_line_bytes(
            context_root,
            "99_meta/audit/invalid-type.jsonl",
            "not bytes",  # type: ignore[arg-type]
            nonce=holder.nonce,
            lock=holder,
        )


def test_append_rejects_incomplete_existing_tail(context_root: Path) -> None:
    ledger = context_root / "99_meta" / "audit" / "incomplete.jsonl"
    ledger.parent.mkdir()
    ledger.write_bytes(b'{"incomplete":true}')

    with (
        _lock(context_root, "append-incomplete") as holder,
        pytest.raises(RecoveryRequiredError, match="incomplete final line"),
    ):
        atomic_append_line_bytes(
            context_root,
            ledger.relative_to(context_root),
            b'{"event":1}\n',
            nonce=holder.nonce,
            lock=holder,
        )

    assert ledger.read_bytes() == b'{"incomplete":true}'


def test_append_replaces_in_context_hardlink_without_mutating_external_inode(
    context_root: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-ledger.jsonl"
    outside.write_bytes(b'{"outside":true}\n')
    ledger = context_root / "99_meta" / "audit" / "hardlink.jsonl"
    ledger.parent.mkdir()
    try:
        os.link(outside, ledger)
    except OSError as exc:
        pytest.skip(f"Hard links are unavailable for this test user: {exc}")
    assert os.path.samefile(outside, ledger)

    with _lock(context_root, "append-hardlink") as holder:
        atomic_append_line_bytes(
            context_root,
            ledger.relative_to(context_root),
            b'{"inside":true}\n',
            nonce=holder.nonce,
            lock=holder,
        )

    assert outside.read_bytes() == b'{"outside":true}\n'
    assert ledger.read_bytes() == b'{"outside":true}\n{"inside":true}\n'
    assert not os.path.samefile(outside, ledger)
