"""Acceptance coverage for failure-isolated registered-context adapter refreshes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

import workctx.adapters.agents as agent_adapters
from workctx.adapters.agents import (
    AdapterPlan,
    AgentAdapterService,
    AgentClient,
    ClientAvailability,
    ClientCapability,
    FileOperation,
    ManagedFileMerge,
    OperationAction,
    OperationResult,
    PlannedChange,
    SemanticVersion,
    SupportedVersionRange,
    TargetApproval,
)
from workctx.adapters.filesystem.registry import ContextRegistry
from workctx.cli import app
from workctx.errors import UserCorrectableError
from workctx.presentation.envelope import CliEnvelope
from workctx.services.contexts import initialize_context

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_adapter_user_config(
    isolated_user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep adapter trust and personalization beside the isolated test registry."""

    from workctx.adapters.agents import _install_records, personalization

    fake_home = isolated_user_config_dir.parent / "fictional-home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))
    monkeypatch.setattr(
        _install_records,
        "user_config_path",
        lambda *_args, **_kwargs: isolated_user_config_dir,
    )
    monkeypatch.setattr(
        personalization,
        "user_personalization_path",
        lambda: isolated_user_config_dir / personalization.PERSONALIZATION_FILENAME,
    )


@pytest.fixture
def fleet_contexts(isolated_user_config_dir: Path) -> dict[str, Path]:
    """Build a fictional machine registry through the globally isolated fixture."""

    parent = isolated_user_config_dir.parent / "fictional-contexts"
    roots = {
        context_id: _context(parent, context_id)
        for context_id in ("zulu-fleet", "middle-fleet", "alpha-fleet")
    }
    assert [item.context_id for item in ContextRegistry().list()] == [
        "alpha-fleet",
        "middle-fleet",
        "zulu-fleet",
    ]
    return roots


def _context(parent: Path, context_id: str) -> Path:
    root = parent / context_id
    initialize_context(root, name=context_id.replace("-", " ").title(), context_id=context_id)
    return root


def _finder(name: str) -> str:
    return f"fake-{name}"


def _probe(executable: str, _root: Path) -> str:
    return {
        "fake-codex": "codex 0.5.0",
        "fake-claude": "Claude Code 2.0.0",
        "fake-gemini": "Gemini CLI 0.5.0",
    }[executable]


def _use_real_service(monkeypatch: pytest.MonkeyPatch) -> AgentAdapterService:
    service = AgentAdapterService(executable_finder=_finder, version_probe=_probe)
    monkeypatch.setattr(agent_adapters, "AgentAdapterService", lambda: service)
    return service


def _envelope(result: Any, *, exit_code: int) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    CliEnvelope.model_validate(payload)
    assert payload["command"] == "agent.refresh"
    assert payload["ok"] is (exit_code == 0)
    return payload


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    entries: list[tuple[str, str, bytes | None]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append((relative, "directory", None))
        elif path.is_file():
            entries.append((relative, "file", path.read_bytes()))
    return tuple(entries)


def test_preview_plans_every_context_in_stable_order_and_writes_nothing(
    fleet_contexts: dict[str, Path],
    isolated_user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_service(monkeypatch)
    sandbox_root = isolated_user_config_dir.parent
    before = _tree_snapshot(sandbox_root)

    payload = _envelope(
        runner.invoke(
            app,
            ["agent", "refresh", "--all", "--agent", "codex", "--json"],
        ),
        exit_code=0,
    )

    assert payload["result"]["apply_requested"] is False
    assert [item["context_id"] for item in payload["result"]["contexts"]] == [
        "alpha-fleet",
        "middle-fleet",
        "zulu-fleet",
    ]
    assert all(
        context["application_state"] == "preview"
        and context["plans"][0]["application_state"] == "preview"
        and context["plans"][0]["applied"] is False
        and context["plans"][0]["receipt"] is None
        for context in payload["result"]["contexts"]
    )
    assert _tree_snapshot(sandbox_root) == before
    assert all(
        not (root / "98_state" / "agent-adapters" / "codex" / "skill-manifest.json").exists()
        for root in fleet_contexts.values()
    )


def test_yes_applies_the_existing_install_path_to_every_context(
    fleet_contexts: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_service(monkeypatch)

    payload = _envelope(
        runner.invoke(
            app,
            ["agent", "refresh", "--all", "--agent", "codex", "--yes", "--json"],
        ),
        exit_code=0,
    )

    assert payload["result"]["apply_requested"] is True
    assert payload["result"]["summary"]["refreshed_clients"] == 3
    assert all(
        context["application_state"] == "applied"
        and context["refreshed"] == ["codex"]
        and context["plans"][0]["application_state"] == "applied"
        and context["plans"][0]["applied"] is True
        and context["plans"][0]["receipt"] is not None
        for context in payload["result"]["contexts"]
    )
    assert all(
        (root / "98_state" / "agent-adapters" / "codex" / "skill-manifest.json").is_file()
        for root in fleet_contexts.values()
    )


def test_missing_and_mismatched_registrations_warn_and_other_contexts_proceed(
    fleet_contexts: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_service(monkeypatch)
    shutil.rmtree(fleet_contexts["middle-fleet"])
    mismatch_path = fleet_contexts["zulu-fleet"] / "context.yaml"
    mismatch = yaml.safe_load(mismatch_path.read_text(encoding="utf-8"))
    mismatch["id"] = "different-fictional-id"
    mismatch_path.write_text(yaml.safe_dump(mismatch, sort_keys=False), encoding="utf-8")

    payload = _envelope(
        runner.invoke(
            app,
            ["agent", "refresh", "--all", "--agent", "codex", "--json"],
        ),
        exit_code=0,
    )

    contexts = {item["context_id"]: item for item in payload["result"]["contexts"]}
    assert contexts["alpha-fleet"]["application_state"] == "preview"
    assert contexts["middle-fleet"]["application_state"] == "skipped"
    assert contexts["middle-fleet"]["skip_reason"]["code"] == "context_root_missing"
    assert contexts["zulu-fleet"]["application_state"] == "skipped"
    assert contexts["zulu-fleet"]["configured_context_id"] == "different-fictional-id"
    assert contexts["zulu-fleet"]["skip_reason"]["code"] == "context_id_mismatch"
    assert {warning["code"] for warning in payload["warnings"]} == {
        "AGENT_REFRESH_CONTEXT_MISSING",
        "AGENT_REFRESH_CONTEXT_ID_MISMATCH",
    }


def test_apply_failure_is_captured_and_later_contexts_are_still_refreshed(
    fleet_contexts: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _use_real_service(monkeypatch)
    original_install = service.install
    attempted: list[Path] = []

    def injected_install(
        plan: AdapterPlan,
        *,
        approvals: tuple[TargetApproval, ...] = (),
    ) -> OperationResult:
        attempted.append(plan.root)
        if plan.root == fleet_contexts["middle-fleet"].resolve():
            raise UserCorrectableError("injected middle-context apply failure")
        return original_install(plan, approvals=approvals)

    monkeypatch.setattr(service, "install", injected_install)

    payload = _envelope(
        runner.invoke(
            app,
            ["agent", "refresh", "--all", "--agent", "codex", "--yes", "--json"],
        ),
        exit_code=6,
    )

    assert attempted == [
        fleet_contexts["alpha-fleet"].resolve(),
        fleet_contexts["middle-fleet"].resolve(),
        fleet_contexts["zulu-fleet"].resolve(),
    ]
    contexts = {item["context_id"]: item for item in payload["result"]["contexts"]}
    assert contexts["alpha-fleet"]["application_state"] == "applied"
    assert contexts["middle-fleet"]["application_state"] == "failed"
    assert contexts["middle-fleet"]["plans"][0]["application_state"] == "failed"
    assert contexts["middle-fleet"]["failures"] == [
        {
            "stage": "apply",
            "client": "codex",
            "reason": "injected middle-context apply failure",
        }
    ]
    assert contexts["zulu-fleet"]["application_state"] == "applied"
    assert payload["result"]["summary"]["failed_contexts"] == 1
    assert payload["errors"][0]["code"] == "AGENT_REFRESH_APPLY_FAILED"
    assert (
        fleet_contexts["zulu-fleet"]
        / "98_state"
        / "agent-adapters"
        / "codex"
        / "skill-manifest.json"
    ).is_file()


def test_agent_narrowing_targets_only_the_selected_available_client(
    fleet_contexts: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_service(monkeypatch)

    payload = _envelope(
        runner.invoke(
            app,
            ["agent", "refresh", "--all", "--agent", "gemini", "--json"],
        ),
        exit_code=0,
    )

    assert payload["result"]["selected_clients"] == ["gemini"]
    assert all(context["clients"] == ["gemini"] for context in payload["result"]["contexts"])
    assert all(
        [plan["client"] for plan in context["plans"]] == ["gemini"]
        for context in payload["result"]["contexts"]
    )


class _MergePreviewService:
    def detect(self, _root: Path) -> tuple[ClientCapability, ...]:
        supported = SupportedVersionRange(
            SemanticVersion(0, 1, 0),
            SemanticVersion(1, 0, 0),
        )
        return tuple(
            ClientCapability(
                client=client,
                availability=(
                    ClientAvailability.AVAILABLE
                    if client is AgentClient.CODEX
                    else ClientAvailability.MISSING
                ),
                executable="codex" if client is AgentClient.CODEX else None,
                version=SemanticVersion(0, 9, 0) if client is AgentClient.CODEX else None,
                supported_range=supported,
            )
            for client in AgentClient
        )

    def plan_install(self, root: Path, client: AgentClient) -> AdapterPlan:
        recorded = f"sha256:{'a' * 64}"
        packaged = f"sha256:{'b' * 64}"
        local = f"sha256:{'c' * 64}"
        candidate = ManagedFileMerge(
            path="AGENTS.md",
            recorded_at_adoption_hash=recorded,
            packaged_now_hash=packaged,
            local_hash=local,
        )
        return AdapterPlan(
            root=root,
            client=client,
            action=OperationAction.INSTALL,
            changes=(
                PlannedChange(
                    path=candidate.path,
                    operation=FileOperation.PRESERVE,
                    observed_hash=local,
                    desired_hash=packaged,
                    reason="Operator-edited managed file preserved",
                ),
            ),
            plan_hash="d" * 64,
            source_fingerprint="e" * 64,
            merge_candidates=(candidate,),
        )


def test_json_shape_merge_passthrough_default_all_and_human_table(
    isolated_user_config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _context(isolated_user_config_dir.parent / "fictional-contexts", "merge-fleet")
    monkeypatch.setattr(agent_adapters, "AgentAdapterService", _MergePreviewService)

    payload = _envelope(
        runner.invoke(app, ["agent", "refresh", "--all", "--json"]),
        exit_code=0,
    )

    assert set(payload["result"]) == {
        "apply_requested",
        "selected_clients",
        "count",
        "contexts",
        "summary",
    }
    assert payload["result"]["selected_clients"] == ["codex", "claude", "gemini"]
    context = payload["result"]["contexts"][0]
    assert set(context) == {
        "context_id",
        "root",
        "configured_context_id",
        "clients",
        "skipped_clients",
        "application_state",
        "plans",
        "refreshed",
        "preserved_edits",
        "merge_candidates",
        "skip_reason",
        "failures",
    }
    assert set(context["plans"][0]) == {
        "client",
        "application_state",
        "applied",
        "plan",
        "receipt",
        "failure",
    }
    assert context["clients"] == ["codex"]
    assert context["skipped_clients"] == ["claude", "gemini"]
    assert context["merge_candidates"] == [
        {
            "client": "codex",
            "path": "AGENTS.md",
            "recorded_at_adoption_hash": f"sha256:{'a' * 64}",
            "packaged_now_hash": f"sha256:{'b' * 64}",
            "local_hash": f"sha256:{'c' * 64}",
        }
    ]
    assert context["plans"][0]["plan"]["merge_candidates"] == [
        {key: value for key, value in context["merge_candidates"][0].items() if key != "client"}
    ]
    assert {warning["code"] for warning in payload["warnings"]} == {"AGENT_CLIENT_UNAVAILABLE"}

    human = runner.invoke(app, ["agent", "refresh", "--all"])
    assert human.exit_code == 0, human.output
    assert "Agent fleet refresh (preview)" in human.stdout
    assert "merge-fleet" in human.stdout
    assert "Refreshed" in human.stdout
    assert "Preserved" in human.stdout
    assert "Merge" in human.stdout
    assert "Skipped" in human.stdout
    assert "Failed" in human.stdout


def test_refresh_without_all_errors_with_single_context_guidance() -> None:
    payload = _envelope(
        runner.invoke(app, ["agent", "refresh", "--agent", "codex", "--json"]),
        exit_code=2,
    )

    assert payload["errors"] == [
        {
            "code": "AGENT_REFRESH_ALL_REQUIRED",
            "message": (
                "Agent refresh currently requires --all; use 'workctx agent install' "
                "to refresh one context."
            ),
            "path": "$.selection",
            "repair_action": None,
        }
    ]
