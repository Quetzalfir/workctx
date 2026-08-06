from __future__ import annotations

import json
import multiprocessing
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import workctx.services.contexts as contexts_module
from workctx.adapters.filesystem import registry as registry_module
from workctx.adapters.filesystem.registry import (
    ContextRegistry,
    RegistryConflictError,
    RegistryError,
    get_active_context,
    list_contexts,
    register_context,
    set_active_context,
    unregister_context,
)
from workctx.services.contexts import initialize_context


@pytest.fixture(autouse=True)
def disable_automatic_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contexts_module, "register_context", lambda *_args, **_kwargs: None)


def _context(parent: Path, context_id: str) -> Path:
    root = parent / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    return root


def _register_with_paused_save(
    registry_file: str,
    context_root: str,
    save_started: Any,
    release_save: Any,
) -> None:
    class PausedSaveRegistry(ContextRegistry):
        def _save(self, snapshot: registry_module.RegistrySnapshot) -> None:
            save_started.set()
            if not release_save.wait(10):
                raise TimeoutError("Timed out waiting to finish the first registry mutation")
            super()._save(snapshot)

    PausedSaveRegistry(Path(registry_file)).register("first-context", Path(context_root))


def _register_with_observed_load(
    registry_file: str,
    context_root: str,
    guard_attempted: Any,
    load_observed: Any,
) -> None:
    original_guard = registry_module._registry_mutation_guard

    @contextmanager
    def observed_guard(path: Path) -> Iterator[None]:
        guard_attempted.set()
        with original_guard(path):
            yield

    class ObservedLoadRegistry(ContextRegistry):
        def _load(self) -> registry_module.RegistrySnapshot:
            snapshot = super()._load()
            load_observed.set()
            return snapshot

    registry_module._registry_mutation_guard = observed_guard
    ObservedLoadRegistry(Path(registry_file)).register("second-context", Path(context_root))


def _hold_registry_guard(registry_file: str, acquired: Any) -> None:
    with registry_module._registry_mutation_guard(Path(registry_file)):
        acquired.set()
        time.sleep(30)


def test_register_and_list_are_sorted_and_same_registration_is_byte_idempotent(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "user-config"
    registry_file = config_dir / "contexts.json"
    first = _context(tmp_path, "z-context")
    second = _context(tmp_path, "a-context")
    registry = ContextRegistry(registry_file)

    registry.register("z-context", first)
    registry.register("a-context", second)
    before = registry_file.read_bytes()
    before_mtime = registry_file.stat().st_mtime_ns
    registry.register("a-context", second)

    assert [item.context_id for item in registry.list()] == ["a-context", "z-context"]
    assert registry_file.read_bytes() == before
    assert registry_file.stat().st_mtime_ns == before_mtime
    payload = json.loads(before)
    assert list(payload["contexts"]) == ["a-context", "z-context"]
    assert payload["active_context_id"] is None


def test_suite_default_registry_canary_is_inside_the_test_temp_tree(
    isolated_user_config_dir: Path,
) -> None:
    registry = ContextRegistry()

    assert registry.path == isolated_user_config_dir / "contexts.json"
    assert isolated_user_config_dir.parent.name == "user-home"


def test_default_registry_path_honors_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_file = tmp_path / "subprocess-fence" / "contexts.json"
    monkeypatch.setenv(registry_module.CONTEXT_REGISTRY_ENV, str(registry_file))

    def forbidden_fallback(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("environment override fell through to platformdirs")

    monkeypatch.setattr(registry_module, "user_config_path", forbidden_fallback)

    assert ContextRegistry().path == registry_file.absolute()


def test_environment_registry_override_inside_context_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _context(tmp_path, "environment-override-context")
    registry_file = workspace / "98_state" / "contexts.json"
    monkeypatch.setenv(registry_module.CONTEXT_REGISTRY_ENV, str(registry_file))

    with pytest.raises(RegistryError, match="outside every context root"):
        ContextRegistry()

    assert not registry_file.exists()


def test_register_if_changed_exact_match_avoids_the_mutation_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    root = _context(tmp_path, "fast-context")
    registry = ContextRegistry(registry_file)
    registry.register("fast-context", root)
    before = registry_file.read_bytes()

    def forbidden_guard(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an unchanged registration entered the mutation guard")

    monkeypatch.setattr(registry_module, "_registry_mutation_guard", forbidden_guard)

    registered = registry.register_if_changed("fast-context", root, replace=True)

    assert registered.root == root.resolve()
    assert registry_file.read_bytes() == before


def test_active_selection_is_explicit_and_unregister_clears_it(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    root = _context(tmp_path, "active-context")

    register_context("active-context", root, registry_file=registry_file)
    assert get_active_context(registry_file=registry_file) is None
    set_active_context("active-context", registry_file=registry_file)
    assert get_active_context(registry_file=registry_file) == root.resolve()

    assert unregister_context("active-context", registry_file=registry_file)
    assert not unregister_context("active-context", registry_file=registry_file)
    assert get_active_context(registry_file=registry_file) is None
    assert list_contexts(registry_file=registry_file) == ()


def test_registration_can_explicitly_make_context_active(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    root = _context(tmp_path, "selected-context")

    registered = register_context(
        "selected-context",
        root,
        make_active=True,
        registry_file=registry_file,
    )

    assert registered.active
    assert get_active_context(registry_file=registry_file) == root.resolve()


def test_conflicting_rebind_fails_closed_unless_replace_is_explicit(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    first_parent = tmp_path / "first"
    second_parent = tmp_path / "second"
    first_parent.mkdir()
    second_parent.mkdir()
    first = _context(first_parent, "same-context")
    second = _context(second_parent, "same-context")
    registry = ContextRegistry(registry_file)
    registry.register("same-context", first)

    with pytest.raises(RegistryConflictError, match="another root"):
        registry.register("same-context", second)

    assert registry.get("same-context") == first.resolve()
    registry.register("same-context", second, replace=True)
    assert registry.get("same-context") == second.resolve()


def test_registry_rejects_id_that_disagrees_with_context_config(tmp_path: Path) -> None:
    root = _context(tmp_path, "actual-context")

    with pytest.raises(RegistryError, match="does not match"):
        ContextRegistry(tmp_path / "registry.json").register("claimed-context", root)


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json\n",
        b"[]\n",
        b'{"schema_version": 2, "active_context_id": null, "contexts": {}}\n',
        b'{"schema_version": 1, "active_context_id": "missing", "contexts": {}}\n',
        b'{"schema_version": 1, "active_context_id": null, "contexts": {"x": "relative"}}\n',
    ],
)
def test_malformed_registry_fails_closed(tmp_path: Path, payload: bytes) -> None:
    registry_file = tmp_path / "contexts.json"
    registry_file.write_bytes(payload)

    with pytest.raises(RegistryError, match="malformed"):
        ContextRegistry(registry_file).list()


@pytest.mark.parametrize(
    "payload",
    [
        (b'{"schema_version":1,"schema_version":1,"active_context_id":null,"contexts":{}}\n'),
        (
            b'{"schema_version":1,"active_context_id":null,'
            b'"contexts":{"duplicate":"C:/first","duplicate":"C:/second"}}\n'
        ),
    ],
)
def test_registry_rejects_duplicate_json_object_keys(tmp_path: Path, payload: bytes) -> None:
    registry_file = tmp_path / "contexts.json"
    registry_file.write_bytes(payload)

    with pytest.raises(RegistryError, match="malformed"):
        ContextRegistry(registry_file).list()


def test_concurrent_registry_mutations_are_serialized_without_lost_updates(
    tmp_path: Path,
) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    first = _context(tmp_path, "first-context")
    second = _context(tmp_path, "second-context")
    process_context = multiprocessing.get_context("spawn")
    first_save_started = process_context.Event()
    release_first_save = process_context.Event()
    second_guard_attempted = process_context.Event()
    second_load_observed = process_context.Event()
    first_process = process_context.Process(
        target=_register_with_paused_save,
        args=(str(registry_file), str(first), first_save_started, release_first_save),
    )
    second_process = process_context.Process(
        target=_register_with_observed_load,
        args=(
            str(registry_file),
            str(second),
            second_guard_attempted,
            second_load_observed,
        ),
    )
    processes = (first_process, second_process)

    try:
        first_process.start()
        assert first_save_started.wait(10)
        second_process.start()
        assert second_guard_attempted.wait(10)
        assert not second_load_observed.wait(0.5)
        release_first_save.set()
        for process in processes:
            process.join(10)
    finally:
        release_first_save.set()
        for process in processes:
            if process.pid is None:
                continue
            if process.is_alive():
                process.terminate()
            process.join(5)

    assert first_process.exitcode == 0
    assert second_process.exitcode == 0
    assert second_load_observed.is_set()
    assert [item.context_id for item in ContextRegistry(registry_file).list()] == [
        "first-context",
        "second-context",
    ]
    guard = registry_file.with_name(f".{registry_file.name}.lock")
    assert guard.is_file()
    assert not guard.is_symlink()


def test_registry_guard_is_released_when_owner_process_terminates(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    context_root = _context(tmp_path, "after-crash-context")
    process_context = multiprocessing.get_context("spawn")
    acquired = process_context.Event()
    process = process_context.Process(
        target=_hold_registry_guard,
        args=(str(registry_file), acquired),
    )

    process.start()
    try:
        assert acquired.wait(10)
        process.terminate()
        process.join(10)
        assert not process.is_alive()

        ContextRegistry(registry_file).register("after-crash-context", context_root)
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)

    assert [item.context_id for item in ContextRegistry(registry_file).list()] == [
        "after-crash-context"
    ]
    assert registry_file.with_name(f".{registry_file.name}.lock").is_file()


def test_registry_mutation_guard_symlink_fails_closed(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    registry_file.parent.mkdir()
    context_root = _context(tmp_path, "guard-context")
    outside = tmp_path / "outside-guard"
    outside.write_bytes(b"sentinel\n")
    guard = registry_file.with_name(f".{registry_file.name}.lock")
    try:
        guard.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable for this test user: {exc}")

    with pytest.raises(RegistryError, match="mutation guard"):
        ContextRegistry(registry_file).register("guard-context", context_root)

    assert outside.read_bytes() == b"sentinel\n"
    assert guard.is_symlink()
    assert not registry_file.exists()


def test_stale_active_registration_fails_closed(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    root = _context(tmp_path, "stale-context")
    registry = ContextRegistry(registry_file)
    registry.register("stale-context", root, make_active=True)
    (root / "context.yaml").unlink()

    with pytest.raises(RegistryError, match="unavailable"):
        registry.get_active()


def test_failed_atomic_registry_replace_preserves_previous_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    first = _context(tmp_path, "first-context")
    second = _context(tmp_path, "second-context")
    registry = ContextRegistry(registry_file)
    registry.register("first-context", first)
    before = registry_file.read_bytes()

    monkeypatch.setattr(
        registry_module.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(PermissionError("injected")),
    )
    with pytest.raises(PermissionError, match="injected"):
        registry.register("second-context", second)

    assert registry_file.read_bytes() == before
    assert not list(registry_file.parent.glob("*.tmp"))


def test_default_registry_path_is_user_config_not_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _context(tmp_path, "workspace-context")
    user_config = tmp_path / "outside-user-config"
    monkeypatch.delenv(registry_module.CONTEXT_REGISTRY_ENV, raising=False)
    monkeypatch.setattr(registry_module, "user_config_path", lambda *_args, **_kwargs: user_config)

    registry = ContextRegistry()
    registry.register("workspace-context", workspace)

    assert registry.path == user_config / "contexts.json"
    assert registry.path.is_file()
    assert not (workspace / "contexts.json").exists()
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert payload["contexts"]["workspace-context"] == str(workspace.resolve())


def test_custom_registry_path_inside_context_is_rejected_without_runtime_write(
    tmp_path: Path,
) -> None:
    workspace = _context(tmp_path, "workspace-context")
    registry_file = workspace / "98_state" / "contexts.json"

    with pytest.raises(RegistryError, match="outside every context root"):
        ContextRegistry(registry_file)

    assert not registry_file.exists()
    assert not registry_file.with_name(f".{registry_file.name}.lock").exists()


def test_registration_rechecks_registry_path_after_context_is_created(tmp_path: Path) -> None:
    future_root = tmp_path / "future-context"
    future_root.mkdir()
    registry_file = future_root / "user-config" / "contexts.json"
    registry = ContextRegistry(registry_file)
    initialize_context(future_root, name="Future Context", context_id="future-context")

    with pytest.raises(RegistryError, match="outside every context root"):
        registry.register("future-context", future_root)

    assert not registry_file.exists()


def test_registry_rejects_snapshot_that_contains_its_own_parent(tmp_path: Path) -> None:
    registry_file = tmp_path / "user-config" / "contexts.json"
    registry_file.parent.mkdir()
    registry_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_context_id": None,
                "contexts": {"parent-context": str(tmp_path.resolve())},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="outside every context root"):
        ContextRegistry(registry_file).list()


def test_registry_file_is_utf8_lf_and_restrictive_on_posix(tmp_path: Path) -> None:
    registry_file = tmp_path / "config" / "contexts.json"
    root = _context(tmp_path, "mode-context")
    ContextRegistry(registry_file).register("mode-context", root)
    payload = registry_file.read_bytes()

    assert not payload.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    if os.name != "nt":
        assert registry_file.stat().st_mode & 0o077 == 0
