"""Acceptance coverage for the Phase 1 CLI-wiring consolidation batch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

import workctx.adapters.agents as agent_adapters
from workctx.adapters.agents import (
    AdapterPlan,
    AdapterState,
    AdapterStatus,
    AgentClient,
    ClientAvailability,
    ClientCapability,
    FileOperation,
    OpenedContext,
    OperationAction,
    OperationResult,
    PlannedChange,
    SemanticVersion,
    SupportedVersionRange,
)
from workctx.adapters.sqlite import SQLiteProjection
from workctx.cli import app
from workctx.domain.transactions import ZERO_REVISION
from workctx.services.contexts import initialize_context
from workctx.transactions import ApplyResult, authenticate_apply_result

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()
TIMESTAMP = "2026-08-02T12:00:00Z"
TIMESTAMP_ID = "20260802T120000Z"


def _envelope(result: Any, *, exit_code: int, command: str) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == command
    assert result.stdout.strip().startswith("{")
    assert result.stdout.strip().endswith("}")
    if exit_code == 0:
        assert result.stderr == ""
        assert payload["ok"] is True
    else:
        assert result.stderr.startswith("Error:")
        assert payload["ok"] is False
    return payload


def _initialize(root: Path, *, with_task: bool = False) -> Path:
    initialize_context(root, name="CLI Wave 3", context_id="cli-wave3")
    if with_task:
        _write_task(root)
    SQLiteProjection(root).rebuild()
    return root


def _write_task(root: Path) -> None:
    frontmatter: dict[str, Any] = {
        "schema_version": 1,
        "id": "TASK-2026-001",
        "entity_type": "task",
        "title": "Review fictional rollout readiness",
        "uri": "workctx://cli-wave3/task/TASK-2026-001",
        "aliases": [],
        "status": "waiting",
        "confidence": "high",
        "tags": ["fictional"],
        "references": [],
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        "task_type": "parent",
        "parent_task": None,
        "root_task": "TASK-2026-001",
        "priority": "P1",
        "owner": "Alex",
        "requester": None,
        "waiting_on": ["Alex"],
        "due_at": None,
        "next_action": "Review the fictional rollout plan.",
        "dependencies": [],
        "blockers": [],
        "source_observations": [],
    }
    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    (root / "03_work" / "TASK-2026-001.md").write_text(
        f"---\n{rendered}---\n\nFictional rollout readiness record.\n",
        encoding="utf-8",
        newline="\n",
    )


def _proposal_payload(
    slug: str,
    *,
    base_revision: str = ZERO_REVISION,
) -> dict[str, Any]:
    identifier = f"PRJ-{slug}"
    return {
        "schema_version": 1,
        "id": f"TXP-{TIMESTAMP_ID}-{slug}",
        "context_id": "cli-wave3",
        "base_revision": base_revision,
        "actor": {
            "type": "human",
            "id": "fictional-operator",
            "agent": None,
            "model": None,
        },
        "created_at": TIMESTAMP,
        "source_refs": [],
        "operations": [
            {
                "op": "create",
                "target": f"02_knowledge/{identifier}.md",
                "payload": {
                    "kind": "entity",
                    "document": {
                        "schema_version": 1,
                        "id": identifier,
                        "entity_type": "project",
                        "title": f"Fictional {slug}",
                        "uri": f"workctx://cli-wave3/project/{identifier}",
                        "aliases": [],
                        "status": "active",
                        "confidence": "high",
                        "tags": ["fictional"],
                        "references": [],
                        "created_at": TIMESTAMP,
                        "updated_at": TIMESTAMP,
                    },
                    "body": "Fictional CLI transaction fixture.\n",
                },
            }
        ],
        "preconditions": [],
        "postconditions": [],
        "expected_views": ["sqlite"],
        "approval": "required",
    }


def _write_proposal(
    directory: Path,
    slug: str,
    *,
    base_revision: str = ZERO_REVISION,
) -> Path:
    path = directory / f"{slug}.json"
    path.write_text(
        json.dumps(_proposal_payload(slug, base_revision=base_revision)),
        encoding="utf-8",
    )
    return path


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("command", "command_id"),
    (
        ("validate", "proposal.validate"),
        ("show", "proposal.show"),
    ),
)
def test_proposal_commands_emit_valid_envelopes(
    tmp_path: Path,
    command: str,
    command_id: str,
) -> None:
    root = _initialize(tmp_path / "ctx")
    proposal = _write_proposal(tmp_path, command)

    payload = _envelope(
        runner.invoke(
            app,
            ["proposal", command, str(proposal), "--context", str(root), "--json"],
        ),
        exit_code=0,
        command=command_id,
    )

    assert payload["context_id"] == "cli-wave3"
    if command == "validate":
        assert payload["result"]["validation"]["valid"] is True
    else:
        assert payload["result"]["dry_run"]["valid"] is True


@pytest.mark.parametrize(
    ("arguments", "command"),
    (
        (["proposal", "validate"], "proposal.validate"),
        (["proposal", "show"], "proposal.show"),
        (["transaction", "apply"], "transaction.apply"),
    ),
)
def test_proposal_file_errors_are_user_correctable_and_stdout_stays_json(
    tmp_path: Path,
    arguments: list[str],
    command: str,
) -> None:
    root = _initialize(tmp_path / "ctx")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")

    _envelope(
        runner.invoke(
            app,
            [*arguments, str(malformed), "--context", str(root), "--json"],
        ),
        exit_code=1,
        command=command,
    )


def test_transaction_apply_without_yes_is_a_non_mutating_dry_run(tmp_path: Path) -> None:
    root = _initialize(tmp_path / "ctx")
    proposal = _write_proposal(tmp_path, "preview")
    before = _tree_hashes(root)

    payload = _envelope(
        runner.invoke(
            app,
            ["transaction", "apply", str(proposal), "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="transaction.apply",
    )

    assert payload["result"]["dry_run"] is True
    assert payload["result"]["confirmation_required"] is True
    assert _tree_hashes(root) == before


def test_transaction_dry_run_flag_overrides_yes(tmp_path: Path) -> None:
    root = _initialize(tmp_path / "ctx")
    proposal = _write_proposal(tmp_path, "forced-preview")
    before = _tree_hashes(root)

    payload = _envelope(
        runner.invoke(
            app,
            [
                "transaction",
                "apply",
                str(proposal),
                "--dry-run",
                "--yes",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="transaction.apply",
    )

    assert payload["result"]["dry_run"] is True
    assert payload["result"]["confirmation_required"] is False
    assert _tree_hashes(root) == before


def test_transaction_apply_with_yes_authenticates_receipt(tmp_path: Path) -> None:
    root = _initialize(tmp_path / "ctx")
    proposal = _write_proposal(tmp_path, "commit")

    payload = _envelope(
        runner.invoke(
            app,
            [
                "transaction",
                "apply",
                str(proposal),
                "--yes",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="transaction.apply",
    )

    assert payload["result"]["dry_run"] is False
    receipt = ApplyResult.model_validate(payload["result"]["receipt"])
    event = authenticate_apply_result(root, receipt)
    assert event.id == receipt.ledger_event_id
    assert (root / "02_knowledge" / "PRJ-commit.md").is_file()


def test_stale_revision_apply_maps_to_conflict_exit_four(tmp_path: Path) -> None:
    root = _initialize(tmp_path / "ctx")
    first = _write_proposal(tmp_path, "first")
    stale = _write_proposal(tmp_path, "stale")
    assert (
        runner.invoke(
            app,
            ["transaction", "apply", str(first), "--yes", "--context", str(root)],
        ).exit_code
        == 0
    )

    payload = _envelope(
        runner.invoke(
            app,
            [
                "transaction",
                "apply",
                str(stale),
                "--yes",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=4,
        command="transaction.apply",
    )

    assert payload["context_id"] == "cli-wave3"
    assert payload["errors"][0]["code"] == "TXN-STALE-REVISION"


def test_transaction_history_and_show_emit_verified_event_envelopes(tmp_path: Path) -> None:
    root = _initialize(tmp_path / "ctx")
    proposal = _write_proposal(tmp_path, "history")
    applied = _envelope(
        runner.invoke(
            app,
            [
                "transaction",
                "apply",
                str(proposal),
                "--yes",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="transaction.apply",
    )
    receipt = applied["result"]["receipt"]

    history = _envelope(
        runner.invoke(
            app,
            ["transaction", "history", "--limit", "1", "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="transaction.history",
    )
    shown = _envelope(
        runner.invoke(
            app,
            [
                "transaction",
                "show",
                receipt["ledger_event_id"],
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="transaction.show",
    )
    shown_by_proposal = _envelope(
        runner.invoke(
            app,
            [
                "transaction",
                "show",
                receipt["proposal_id"],
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="transaction.show",
    )

    assert history["result"]["summary"]["event_count"] == 1
    assert history["result"]["events"][0]["id"] == receipt["ledger_event_id"]
    assert shown["result"]["event"]["proposal_id"] == receipt["proposal_id"]
    assert shown_by_proposal["result"]["event"]["id"] == receipt["ledger_event_id"]


def test_transaction_show_missing_id_is_user_correctable(tmp_path: Path) -> None:
    root = _initialize(tmp_path / "ctx")
    payload = _envelope(
        runner.invoke(
            app,
            [
                "transaction",
                "show",
                f"AUD-{TIMESTAMP_ID}-missing",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=1,
        command="transaction.show",
    )
    assert payload["errors"][0]["code"] == "TRANSACTION_NOT_FOUND"


def test_search_and_task_commands_emit_filtered_envelopes(tmp_path: Path) -> None:
    root = _initialize(tmp_path / "ctx", with_task=True)

    search = _envelope(
        runner.invoke(
            app,
            ["search", "rollout readiness", "--type", "task", "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="search",
    )
    listed = _envelope(
        runner.invoke(
            app,
            [
                "task",
                "list",
                "--status",
                "waiting",
                "--waiting-on",
                "Alex",
                "--context",
                str(root),
                "--json",
            ],
        ),
        exit_code=0,
        command="task.list",
    )
    shown = _envelope(
        runner.invoke(
            app,
            ["task", "show", "TASK-2026-001", "--context", str(root), "--json"],
        ),
        exit_code=0,
        command="task.show",
    )

    assert search["result"]["hits"][0]["id"] == "TASK-2026-001"
    assert listed["result"]["tasks"][0]["status"] == "waiting"
    assert shown["result"]["task"]["next_action"].startswith("Review")


@pytest.mark.parametrize(
    ("arguments", "command"),
    (
        (["search", "..."], "search"),
        (["task", "show", "TASK-2026-999"], "task.show"),
    ),
)
def test_projection_command_failures_are_user_correctable(
    tmp_path: Path,
    arguments: list[str],
    command: str,
) -> None:
    root = _initialize(tmp_path / "ctx", with_task=True)
    _envelope(
        runner.invoke(app, [*arguments, "--context", str(root), "--json"]),
        exit_code=1,
        command=command,
    )


class _FakeAgentService:
    install_approvals: ClassVar[list[tuple[Any, ...]]] = []

    def status(self, root: Path, client: AgentClient) -> AdapterStatus:
        return AdapterStatus(
            client=client,
            state=AdapterState.NOT_INSTALLED,
            manifest_path=f"98_state/agent-adapters/{client.value}/skill-manifest.json",
        )

    def plan_install(self, root: Path, client: AgentClient) -> AdapterPlan:
        return AdapterPlan(
            root=root,
            client=client,
            action=OperationAction.INSTALL,
            changes=(
                PlannedChange(
                    path=f".{client.value}/managed.txt",
                    operation=FileOperation.REPLACE,
                    observed_hash=f"sha256:{'a' * 64}",
                    desired_hash=f"sha256:{'b' * 64}",
                    requires_approval=True,
                ),
            ),
            plan_hash="c" * 64,
            source_fingerprint="d" * 64,
        )

    def install(
        self,
        plan: AdapterPlan,
        *,
        approvals: tuple[Any, ...] = (),
    ) -> OperationResult:
        type(self).install_approvals.append(approvals)
        return OperationResult(
            root=plan.root,
            client=plan.client,
            action=plan.action,
            changed_paths=tuple(change.path for change in plan.changes),
        )


def _fake_capabilities(_root: Path) -> tuple[ClientCapability, ...]:
    return (
        ClientCapability(
            client=AgentClient.CODEX,
            availability=ClientAvailability.AVAILABLE,
            executable="codex",
            version=SemanticVersion(0, 9, 0),
            supported_range=SupportedVersionRange(
                SemanticVersion(0, 1, 0), SemanticVersion(1, 0, 0)
            ),
        ),
    )


def _fake_open(root: Path, client: AgentClient) -> OpenedContext:
    return OpenedContext(client=client, root=root, executable=client.value, pid=4242)


def test_agent_commands_emit_valid_envelopes_and_bind_install_approvals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _initialize(tmp_path / "ctx")
    _FakeAgentService.install_approvals = []
    monkeypatch.setattr(agent_adapters, "detect_clients", _fake_capabilities)
    monkeypatch.setattr(agent_adapters, "AgentAdapterService", _FakeAgentService)
    monkeypatch.setattr(agent_adapters, "open_context", _fake_open)

    commands = (
        (["agent", "detect"], "agent.detect"),
        (["agent", "status", "--agent", "codex"], "agent.status"),
        (["agent", "install", "--agent", "codex"], "agent.install"),
        (["agent", "install", "--agent", "codex", "--yes"], "agent.install"),
        (["agent", "open", "--agent", "codex"], "agent.open"),
    )
    payloads = [
        _envelope(
            runner.invoke(app, [*arguments, "--context", str(root), "--json"]),
            exit_code=0,
            command=command,
        )
        for arguments, command in commands
    ]

    assert payloads[2]["result"]["applied"] is False
    assert payloads[2]["result"]["receipts"] == []
    assert payloads[3]["result"]["applied"] is True
    assert len(_FakeAgentService.install_approvals) == 1
    assert _FakeAgentService.install_approvals[0][0].path == ".codex/managed.txt"
    assert payloads[4]["result"]["session"]["pid"] == 4242


@pytest.mark.parametrize("command", ("status", "install", "open"))
def test_agent_selection_errors_are_user_correctable(
    tmp_path: Path,
    command: str,
) -> None:
    root = _initialize(tmp_path / "ctx")
    payload = _envelope(
        runner.invoke(
            app,
            ["agent", command, "--agent", "unknown", "--context", str(root), "--json"],
        ),
        exit_code=1,
        command=f"agent.{command}",
    )
    assert payload["errors"][0]["code"] == "USER_CORRECTABLE"
