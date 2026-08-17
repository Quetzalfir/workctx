from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from workctx.adapters.agents import (
        AdapterPlan,
        AdapterStatus,
        AgentClient,
        ClientCapability,
        FeatureStatus,
        ManagedFileMerge,
        OpenedContext,
        OperationResult,
        TargetApproval,
    )
    from workctx.adapters.agents.fleet import (
        FleetClientResult,
        FleetContextResult,
        FleetFailure,
        FleetRefreshResult,
    )
    from workctx.adapters.filesystem.registry import ContextInventoryEntry
    from workctx.adapters.sqlite import SearchHit, SQLiteProjection, TaskRecord
    from workctx.connectors import SyncResult
    from workctx.domain.transactions import AuditEvent, TransactionProposal
    from workctx.drafting import SendPreview, SendResult
    from workctx.ingestion import ArtifactRecord, IngestionService, RegistrationResult
    from workctx.retrieval.records import ResolutionResult
    from workctx.suggestions import SuggestionDocument, SuggestionMutationResult
    from workctx.transactions.models import DryRunResult, TransactionDiagnostic
    from workctx.usage import UsageCandidate
    from workctx.usage.suggestions import UsageSuggestionResult
    from workctx.views import BriefPayload

import typer
from pydantic import JsonValue, ValidationError
from rich.table import Table
from rich.text import Text

from workctx import __version__
from workctx.doctor import DoctorCheck, run_doctor
from workctx.domain import EntityType, TaskStatus
from workctx.errors import (
    InvalidContextError,
    StaleDerivedStateError,
    UnavailableDependencyError,
    UsageConfigurationError,
    UserCorrectableError,
    WorkctxError,
)
from workctx.models.context import ContextKind, ContextProfile
from workctx.presentation import (
    CliDiagnostic,
    PresentationTyperGroup,
    begin_command,
    emit_success,
    output_console,
    record_failure,
    sanitize_message,
)
from workctx.presentation import resolve_cli_context as _resolve_cli_context
from workctx.services.contexts import (
    initialize_context,
    load_context_config,
    register_resolved_context,
)
from workctx.suggestions import SuggestionStatus
from workctx.validation.workspace import ValidationIssue, ValidationReport, validate_workspace

app = typer.Typer(
    name="workctx",
    help="Local-first, model-neutral work memory and operations for AI agents.",
    no_args_is_help=True,
    cls=PresentationTyperGroup,
)
context_app = typer.Typer(help="Create, register, list, inspect, and validate isolated contexts.")
app.add_typer(context_app, name="context")
inbox_app = typer.Typer(help="Register and inspect inbox artifacts.")
app.add_typer(inbox_app, name="inbox")
artifact_app = typer.Typer(help="Inspect and verify preserved artifacts.")
app.add_typer(artifact_app, name="artifact")
view_app = typer.Typer(help="Rebuild generated operational views.")
app.add_typer(view_app, name="view")
index_app = typer.Typer(help="Manage rebuildable derived indexes.")
app.add_typer(index_app, name="index")
ref_app = typer.Typer(help="Resolve, traverse, and trace canonical references.")
app.add_typer(ref_app, name="ref")
mcp_app = typer.Typer(help="Serve one isolated context over MCP.")
app.add_typer(mcp_app, name="mcp")
proposal_app = typer.Typer(help="Validate and inspect transaction proposals.")
app.add_typer(proposal_app, name="proposal")
transaction_app = typer.Typer(help="Preview, apply, and inspect canonical transactions.")
app.add_typer(transaction_app, name="transaction")
task_app = typer.Typer(help="Query projected canonical tasks.")
app.add_typer(task_app, name="task")
suggestion_app = typer.Typer(help="Inspect and review canonical suggestion records.")
app.add_typer(suggestion_app, name="suggestion")
usage_app = typer.Typer(help="Inspect opt-in local usage signals and create advisory records.")
app.add_typer(usage_app, name="usage")
agent_app = typer.Typer(help="Detect, install, refresh, inspect, and open supported agent clients.")
app.add_typer(agent_app, name="agent")
migrate_app = typer.Typer(help="Convert legacy Markdown repositories into isolated contexts.")
app.add_typer(migrate_app, name="migrate")
secret_app = typer.Typer(help="Manage machine-global secret references without printing values.")
app.add_typer(secret_app, name="secret")
connector_app = typer.Typer(help="Synchronize declarative external-source connectors.")
app.add_typer(connector_app, name="connector")
outbox_app = typer.Typer(help="Preview and deliver one approval-pinned outbox draft.")
app.add_typer(outbox_app, name="outbox")


def resolve_cli_context(
    *,
    explicit_path: Path | None,
    positional_path: Path | None = None,
    discovery_start: Path | None = None,
) -> Path:
    """Resolve one CLI context, then register it without affecting command success."""

    root = _resolve_cli_context(
        explicit_path=explicit_path,
        positional_path=positional_path,
        discovery_start=discovery_start,
    )
    with suppress(Exception):
        register_resolved_context(root)
    return root


@outbox_app.command("send")
def outbox_send(
    draft_id: Annotated[str, typer.Argument(help="Draft ID or canonical draft URI.")],
    via: Annotated[str, typer.Option("--via", help="Delivery channel; v1 supports github.")],
    target: Annotated[
        str,
        typer.Option("--target", help="One GitHub issue or PR as owner/repo#number."),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve this one preview-pinned external write."),
    ] = False,
    fingerprint: Annotated[
        str | None,
        typer.Option(
            "--fingerprint",
            help="Exact fingerprint returned by preview; required with --yes --json.",
        ),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Preview by default; explicitly approve one pinned GitHub comment send."""

    from workctx.drafting import SendError, preview_send
    from workctx.drafting import send as deliver_draft

    begin_command("outbox.send", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    try:
        preview = preview_send(root, draft_id, via, target)
        if not yes:
            if json_output:
                emit_success(
                    result=_outbox_preview_payload(preview),
                    context_id=context_id,
                )
            else:
                _render_outbox_preview(preview)
                output_console.print(Text("Re-run with --yes to review and confirm this send."))
            return

        approved_fingerprint: str
        if json_output:
            if not fingerprint:
                error = CliDiagnostic(
                    code="OUTBOX_FINGERPRINT_REQUIRED",
                    message="JSON send approval requires the exact preview fingerprint.",
                    path="$.fingerprint",
                )
                record_failure(
                    result=_outbox_preview_payload(preview),
                    context_id=context_id,
                    errors=[error],
                )
                raise UsageConfigurationError(error.message)
            approved_fingerprint = fingerprint
        else:
            _render_outbox_preview(preview)
            if not typer.confirm("Send this exact body to this exact GitHub target?"):
                output_console.print(Text("Send cancelled; no external write occurred."))
                return
            approved_fingerprint = preview.fingerprint

        delivered = deliver_draft(
            root,
            draft_id,
            via,
            target,
            approved=True,
            fingerprint=approved_fingerprint,
        )
    except SendError as exc:
        _record_outbox_failure(
            exc,
            context_id=context_id,
            draft_id=draft_id,
            channel=via,
            target=target,
        )
        raise

    if json_output:
        emit_success(
            result=_outbox_send_payload(delivered),
            context_id=context_id,
        )
    else:
        output_console.print(
            Text(
                f"Sent {delivered.draft.id}: {delivered.delivery.remote_comment_url}; "
                f"ledger event {delivered.receipt.ledger_event_id}."
            )
        )


@connector_app.command("list")
def connector_list(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List declarative connector manifests configured in the context."""
    begin_command("connector.list", json_output=json_output)

    from workctx.connectors import load_manifests

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    manifests = load_manifests(root)
    connectors: list[JsonValue] = [
        {
            "name": manifest.name,
            "base_url": str(manifest.base_url),
            "secret_ref": manifest.secret_ref,
            "snapshots": [snapshot.id for snapshot in manifest.snapshots],
        }
        for manifest in manifests
    ]
    result: dict[str, JsonValue] = {"count": len(connectors), "connectors": connectors}
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        if not manifests:
            output_console.print(Text("No connector manifests are configured."))
        for manifest in manifests:
            snapshot_ids = ", ".join(snapshot.id for snapshot in manifest.snapshots)
            output_console.print(Text(f"{manifest.name}: {snapshot_ids}"))


@connector_app.command("sync")
def connector_sync(
    name: Annotated[
        str | None,
        typer.Argument(help="Connector manifest name; omit when using --all."),
    ] = None,
    all_connectors: Annotated[
        bool,
        typer.Option("--all", help="Synchronize every configured connector."),
    ] = False,
    due: Annotated[
        bool,
        typer.Option("--due", help="With --all, synchronize only due scheduled snapshots."),
    ] = False,
    snapshot: Annotated[
        str | None,
        typer.Option("--snapshot", help="Synchronize one named snapshot only."),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Fetch and register one connector or a failure-isolated connector batch."""
    begin_command("connector.sync", json_output=json_output)

    _validate_connector_sync_selection(
        name=name,
        all_connectors=all_connectors,
        due=due,
        snapshot=snapshot,
    )

    from workctx.connectors import sync, sync_all

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    if not all_connectors:
        assert name is not None
        synced = sync(root, name, snapshot_id=snapshot)
        result = cast("dict[str, JsonValue]", synced.model_dump(mode="json"))
        if json_output:
            emit_success(result=result, context_id=context_id)
        else:
            _render_connector_sync_result(synced)
        return

    batch = sync_all(root, due_only=due)
    result = cast("dict[str, JsonValue]", batch.model_dump(mode="json"))
    failures = tuple(
        (index, outcome)
        for index, outcome in enumerate(batch.outcomes)
        if outcome.error is not None
    )
    if not json_output:
        for outcome in batch.outcomes:
            if not outcome.attempted:
                output_console.print(Text(f"{outcome.connector_name}: not due"))
            elif outcome.error is not None:
                output_console.print(
                    Text(f"{outcome.connector_name}: failed ({outcome.error.message})")
                )
            elif outcome.result is not None:
                _render_connector_sync_result(outcome.result)
    if failures:
        errors = [
            CliDiagnostic(
                code=f"CONNECTOR_{outcome.error.kind.value.upper()}",
                message=sanitize_message(outcome.error.message),
                path=f"$.outcomes[{index}].error",
            )
            for index, outcome in failures
            if outcome.error is not None
        ]
        record_failure(result=result, context_id=context_id, errors=errors)
        raise UserCorrectableError(
            f"{len(failures)} connector synchronization(s) failed; partial results are available."
        )
    if json_output:
        emit_success(result=result, context_id=context_id)


@connector_app.command("status")
def connector_status_command(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Report schedule, last success, and due state for every snapshot."""
    begin_command("connector.status", json_output=json_output)

    from workctx.connectors import status

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    reported = status(root)
    result = cast("dict[str, JsonValue]", reported.model_dump(mode="json"))
    result["count"] = len(reported.snapshots)
    if json_output:
        emit_success(result=result, context_id=context_id)
        return
    if not reported.snapshots:
        output_console.print(Text("No connector manifests are configured."))
    for item in reported.snapshots:
        schedule = item.schedule.value if item.schedule is not None else "manual"
        last_success = (
            item.last_success.isoformat().replace("+00:00", "Z")
            if item.last_success is not None
            else "never"
        )
        due_label = "due" if item.due_now else "not due"
        output_console.print(
            Text(
                f"{item.connector_name}/{item.snapshot_id}: {schedule}; "
                f"last success {last_success}; {due_label}"
            )
        )


def _validate_connector_sync_selection(
    *,
    name: str | None,
    all_connectors: bool,
    due: bool,
    snapshot: str | None,
) -> None:
    message: str | None = None
    if name is not None and all_connectors:
        message = "A connector name and --all are mutually exclusive."
    elif name is None and not all_connectors:
        message = "Provide a connector name or --all."
    elif due and not all_connectors:
        message = "--due can only be used with --all."
    elif snapshot is not None and all_connectors:
        message = "--snapshot cannot be used with --all."
    if message is None:
        return
    diagnostic = CliDiagnostic(
        code="CONNECTOR_SYNC_SELECTION",
        message=message,
        path="$.selection",
    )
    record_failure(result={}, errors=[diagnostic])
    raise UsageConfigurationError(message)


def _render_connector_sync_result(synced: SyncResult) -> None:
    for item in synced.snapshots:
        output_console.print(
            Text(
                f"{synced.connector_name}/{item.snapshot_id}: "
                f"{item.disposition.value} ({item.byte_count} bytes)"
            )
        )


@app.command()
def version() -> None:
    """Print the installed Work Context OS version."""
    typer.echo(__version__)


@secret_app.command(
    "set",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def secret_set(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Lowercase kebab-case secret name.")],
    from_env: Annotated[
        str | None,
        typer.Option("--from-env", help="Read the value from this environment variable."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Prompt for a value, or read one from an environment variable, then store it."""
    import os

    from workctx.secrets import SecretBackendUnavailableError, SecretRef, store

    begin_command("secret.set", json_output=json_output)
    if ctx.args:
        error = CliDiagnostic(
            code="SECRET_VALUE_ON_ARGV",
            message=(
                "Secret values cannot be passed on argv; use the masked prompt or --from-env."
            ),
        )
        record_failure(result={}, errors=[error])
        raise UsageConfigurationError(error.message)

    ref = SecretRef(name)
    if from_env is None:
        value = cast(
            "str",
            typer.prompt("Secret value", hide_input=True, err=json_output, type=str),
        )
    else:
        if from_env not in os.environ:
            error = CliDiagnostic(
                code="SECRET_SOURCE_ENV_NOT_FOUND",
                message="The source environment variable is not set.",
            )
            record_failure(result={"name": ref.name}, errors=[error])
            raise UserCorrectableError(error.message)
        value = os.environ[from_env]

    result: dict[str, JsonValue] = {
        "name": ref.name,
        "stored": True,
        "backend": "os-store",
    }
    try:
        store(ref, value)
    except SecretBackendUnavailableError:
        _record_secret_backend_failure(result={"name": ref.name})
        raise
    finally:
        del value

    if json_output:
        emit_success(result=result)
    else:
        output_console.print(Text(f"Stored secret reference '{ref.name}' in the OS store."))


@secret_app.command("check")
def secret_check(
    name: Annotated[str, typer.Argument(help="Lowercase kebab-case secret name.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Check whether a reference resolves and identify the winning layer."""
    from workctx.secrets import (
        SecretBackendUnavailableError,
        SecretNotFoundError,
        SecretRef,
        resolution_layer,
    )

    begin_command("secret.check", json_output=json_output)
    ref = SecretRef(name)
    try:
        layer = resolution_layer(ref)
    except SecretNotFoundError as exc:
        result: dict[str, JsonValue] = {
            "name": ref.name,
            "resolvable": False,
            "layer": None,
        }
        record_failure(
            result=result,
            errors=[CliDiagnostic(code="SECRET_NOT_FOUND", message=str(exc))],
        )
        raise
    except SecretBackendUnavailableError:
        _record_secret_backend_failure(result={"name": ref.name})
        raise

    result = {"name": ref.name, "resolvable": True, "layer": layer.value}
    if json_output:
        emit_success(result=result)
    else:
        output_console.print(Text(f"{ref.name}: resolvable via {layer.value}."))


@secret_app.command("list")
def secret_list(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List known names and resolver-layer presence without reading values into output."""
    from workctx.secrets import (
        SecretBackendUnavailableError,
        environment_contains,
        inspect_presence,
        list_names,
        os_store_available,
    )

    begin_command("secret.list", json_output=json_output)
    names = list_names()
    backend_is_available = os_store_available()
    warnings: list[CliDiagnostic] = []
    items: list[dict[str, JsonValue]] = []

    if backend_is_available:
        try:
            for secret_name in names:
                presence = inspect_presence(secret_name)
                resolved = presence.resolved_layer
                items.append(
                    {
                        "name": presence.name,
                        "environment": presence.environment,
                        "os_store": presence.os_store,
                        "resolved_layer": resolved.value if resolved is not None else None,
                    }
                )
        except SecretBackendUnavailableError:
            backend_is_available = False

    if not backend_is_available:
        items = []
        for secret_name in names:
            environment = environment_contains(secret_name)
            items.append(
                {
                    "name": secret_name,
                    "environment": environment,
                    "os_store": None,
                    "resolved_layer": "env" if environment else None,
                }
            )
        warnings.append(
            CliDiagnostic(
                code="SECRET_BACKEND_UNAVAILABLE",
                message=(
                    "The OS credential store is unavailable; only environment presence is known."
                ),
            )
        )

    result: dict[str, JsonValue] = {
        "count": len(items),
        "secrets": cast("list[JsonValue]", items),
        "os_store_available": backend_is_available,
    }
    if json_output:
        emit_success(result=result, warnings=warnings)
    else:
        table = Table(title="Secret references")
        table.add_column("Name")
        table.add_column("Environment")
        table.add_column("OS store")
        for item in items:
            os_presence = item["os_store"]
            table.add_row(
                cast("str", item["name"]),
                "yes" if item["environment"] else "no",
                "unknown" if os_presence is None else ("yes" if os_presence else "no"),
            )
        output_console.print(table)
        for warning in warnings:
            output_console.print(Text(f"Warning: {warning.message}"))


@secret_app.command("unset")
def secret_unset(
    name: Annotated[str, typer.Argument(help="Lowercase kebab-case secret name.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Remove a named OS-store entry; environment variables are unchanged."""
    from workctx.secrets import (
        SecretBackendUnavailableError,
        SecretRef,
        delete,
        environment_contains,
    )

    begin_command("secret.unset", json_output=json_output)
    ref = SecretRef(name)
    try:
        deleted = delete(ref)
    except SecretBackendUnavailableError:
        _record_secret_backend_failure(result={"name": ref.name})
        raise

    environment_present = environment_contains(ref)
    warnings: list[CliDiagnostic] = []
    if environment_present:
        warnings.append(
            CliDiagnostic(
                code="SECRET_ENV_OVERRIDE_PRESENT",
                message="The environment layer still satisfies this reference.",
            )
        )
    result: dict[str, JsonValue] = {
        "name": ref.name,
        "deleted": deleted,
        "environment_present": environment_present,
    }
    if json_output:
        emit_success(result=result, warnings=warnings)
    else:
        status = "Removed" if deleted else "No OS-store entry existed for"
        output_console.print(Text(f"{status} secret reference '{ref.name}'."))
        for warning in warnings:
            output_console.print(Text(f"Warning: {warning.message}"))


@secret_app.command("import")
def secret_import(
    source_path: Annotated[
        Path,
        typer.Argument(help="Dotenv file to import directly into the OS credential store."),
    ],
    shred: Annotated[
        bool | None,
        typer.Option(
            "--shred/--keep",
            help="Securely remove or explicitly retain the source after import.",
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Import a dotenv file, then securely remove it when explicitly approved."""
    from workctx.secrets import (
        DotenvParseError,
        SecretBackendUnavailableError,
        SecretImportError,
        parse_dotenv,
        shred_dotenv,
        store,
    )

    begin_command("secret.import", json_output=json_output)
    if json_output and shred is None:
        error = CliDiagnostic(
            code="SECRET_IMPORT_DISPOSITION_REQUIRED",
            message="JSON mode requires an explicit --shred or --keep flag.",
        )
        record_failure(result={"count": 0, "names": []}, errors=[error])
        raise UsageConfigurationError(error.message)

    try:
        entries = parse_dotenv(source_path)
    except DotenvParseError as exc:
        record_failure(
            result={"count": 0, "names": []},
            errors=[CliDiagnostic(code="DOTENV_MALFORMED", message=str(exc))],
        )
        raise

    stored_names: list[str] = []
    try:
        for entry in entries:
            store(entry.ref, entry.value)
            stored_names.append(entry.ref.name)
    except SecretBackendUnavailableError:
        _record_secret_backend_failure(
            result={
                "count": len(stored_names),
                "names": cast("list[JsonValue]", sorted(stored_names)),
            },
        )
        raise

    should_shred = (
        typer.confirm("Securely delete the imported dotenv file now?") if shred is None else shred
    )
    source_deleted = False
    if should_shred:
        try:
            shred_dotenv(source_path)
            source_deleted = True
        except SecretImportError as exc:
            failure_result: dict[str, JsonValue] = {
                "count": len(stored_names),
                "names": cast("list[JsonValue]", sorted(stored_names)),
                "source_deleted": False,
            }
            record_failure(
                result=failure_result,
                errors=[CliDiagnostic(code="SECRET_SOURCE_DELETE_FAILED", message=str(exc))],
            )
            raise

    result: dict[str, JsonValue] = {
        "count": len(stored_names),
        "names": cast("list[JsonValue]", sorted(stored_names)),
        "source_deleted": source_deleted,
    }
    if json_output:
        emit_success(result=result)
    else:
        names = ", ".join(sorted(stored_names)) or "none"
        output_console.print(Text(f"Imported {len(stored_names)} secret references: {names}."))
        disposition = "securely removed" if source_deleted else "kept"
        output_console.print(Text(f"The dotenv source was {disposition}."))


def _record_secret_backend_failure(*, result: dict[str, JsonValue]) -> None:
    record_failure(
        result=result,
        errors=[
            CliDiagnostic(
                code="SECRET_BACKEND_UNAVAILABLE",
                message=(
                    "The OS credential store is unavailable; configure keyring or use a "
                    "WORKCTX_SECRET_* environment variable."
                ),
            )
        ],
    )


@migrate_app.command("legacy")
def migrate_legacy_command(
    source_path: Annotated[
        Path,
        typer.Argument(help="Legacy Markdown repository to inspect without modifying."),
    ],
    target_context_path: Annotated[
        Path,
        typer.Argument(help="New or empty destination context directory."),
    ],
    dry_run_only: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview findings and mappings without writing."),
    ] = False,
    apply_changes: Annotated[
        bool,
        typer.Option("--apply", help="Build and publish the validated destination context."),
    ] = False,
    allow_findings: Annotated[
        bool,
        typer.Option(
            "--allow-findings",
            help="Allow apply despite blocking findings; unsafe source bytes stay excluded.",
        ),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Preview by default, or apply a deterministic legacy migration."""
    from workctx.migration import (
        MigrationBlockedError,
        MigrationValidationError,
        migrate_legacy,
        render_migration_markdown,
    )

    begin_command("migrate.legacy", json_output=json_output)
    should_apply = apply_changes and not dry_run_only
    try:
        report = migrate_legacy(
            source_path,
            target_context_path,
            apply_changes=should_apply,
            allow_findings=allow_findings,
        )
    except (MigrationBlockedError, MigrationValidationError) as exc:
        report = exc.report
        result: dict[str, JsonValue] = {
            "mode": report.mode.value,
            "applied": report.applied,
            "report": cast(
                "dict[str, JsonValue]",
                report.model_dump(mode="json", by_alias=True),
            ),
        }
        code = (
            "MIGRATION_FINDINGS_BLOCK_APPLY"
            if isinstance(exc, MigrationBlockedError)
            else "MIGRATION_VALIDATION_FAILED"
        )
        message = (
            "Migration apply is blocked by report findings."
            if isinstance(exc, MigrationBlockedError)
            else "The staged migration context did not validate cleanly."
        )
        record_failure(
            result=result,
            context_id=report.target_context_id,
            errors=[CliDiagnostic(code=code, message=message)],
        )
        if not json_output:
            output_console.print(Text(render_migration_markdown(report)))
        raise

    result = {
        "mode": report.mode.value,
        "applied": report.applied,
        "report": cast(
            "dict[str, JsonValue]",
            report.model_dump(mode="json", by_alias=True),
        ),
    }
    warnings: list[CliDiagnostic] = []
    if dry_run_only and apply_changes:
        warnings.append(
            CliDiagnostic(
                code="MIGRATION_DRY_RUN_OVERRIDES_APPLY",
                message="The explicit dry-run flag prevented migration apply.",
            )
        )
    if report.blocked:
        warnings.append(
            CliDiagnostic(
                code="MIGRATION_APPLY_BLOCKED",
                message="The preview contains findings that block apply by default.",
            )
        )
    if json_output:
        emit_success(
            result=result,
            context_id=report.target_context_id,
            warnings=warnings,
        )
    elif report.applied:
        output_console.print(
            Text(
                f"Migrated context {report.target_context_id} to "
                f"{target_context_path.expanduser().resolve()}."
            )
        )
        output_console.print(Text("Reports: 99_meta/migration/report.json and report.md."))
    else:
        output_console.print(Text(render_migration_markdown(report)))


@mcp_app.command("serve")
def mcp_serve(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path."),
    ] = None,
) -> None:
    """Serve the resolved context over MCP stdio."""
    begin_command("mcp.serve", json_output=False)
    root = resolve_cli_context(explicit_path=context_path)
    from workctx.mcp import serve_stdio

    serve_stdio(root)


@app.command("guide")
def guide_command(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show deterministic file-placement and ownership guidance."""

    from workctx.guide import guide_payload, render_guide

    begin_command("guide", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    result = guide_payload(root)
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(render_guide(context_id=context_id), crop=False)


@app.command()
def doctor(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Check the local development and agent environment."""
    begin_command("doctor", json_output=json_output)
    checks = run_doctor()
    result: dict[str, JsonValue] = {"checks": [_serialize_check(check) for check in checks]}
    failed_required = [check for check in checks if check.required and check.status != "ok"]

    if failed_required:
        if not json_output:
            _render_doctor(checks)
        error = CliDiagnostic(
            code="DEPENDENCY_UNAVAILABLE",
            message="One or more required doctor checks failed.",
        )
        record_failure(result=result, errors=[error])
        raise UnavailableDependencyError(error.message)

    if json_output:
        emit_success(result=result)
    else:
        _render_doctor(checks)


@context_app.command("init")
def context_init(
    path: Annotated[Path, typer.Argument(help="Directory for the new isolated context.")],
    name: Annotated[str, typer.Option("--name", help="Human-readable context name.")],
    context_id: Annotated[
        str | None, typer.Option("--id", help="Stable lowercase context ID.")
    ] = None,
    kind: Annotated[ContextKind, typer.Option("--kind")] = ContextKind.PROJECT,
    profile: Annotated[ContextProfile, typer.Option("--profile")] = ContextProfile.HYBRID,
    user_language: Annotated[str, typer.Option("--user-language")] = "en",
    timezone: Annotated[str, typer.Option("--timezone")] = "UTC",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Create a context from the versioned workspace template."""
    begin_command("context.init", json_output=json_output)
    target = path.expanduser().resolve()
    try:
        config = initialize_context(
            target,
            name=name,
            context_id=context_id,
            kind=kind,
            profile=profile,
            user_language=user_language,
            timezone=timezone,
        )
    except ValidationError as exc:
        raise UserCorrectableError("Context options are invalid.") from exc
    except ValueError as exc:
        raise UserCorrectableError(str(exc)) from exc

    result: dict[str, JsonValue] = {
        "root": str(target),
        "context": config.model_dump(mode="json"),
    }
    if json_output:
        emit_success(result=result, context_id=config.id)
    else:
        output_console.print(
            Text.assemble("Created context ", (config.id, "bold"), " at ", str(target)),
            soft_wrap=True,
        )


@context_app.command("register")
def context_register(
    path: Annotated[
        Path | None, typer.Argument(help="Path inside the context to register.")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Add or update one context in the advisory user registry."""
    begin_command("context.register", json_output=json_output)

    from workctx.adapters.filesystem.registry import register_context

    root = resolve_cli_context(explicit_path=None, positional_path=path)
    config = load_context_config(root)
    registered = register_context(config.id, root, replace=True)
    result: dict[str, JsonValue] = {
        "id": registered.context_id,
        "path": str(registered.root),
        "active": registered.active,
    }
    if json_output:
        emit_success(result=result, context_id=config.id)
    else:
        output_console.print(
            Text.assemble("Registered context ", (config.id, "bold"), " at ", str(root)),
            soft_wrap=True,
        )


@context_app.command("list")
def context_list(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List advisory registrations and cheap canonical context statistics."""
    begin_command("context.list", json_output=json_output)

    from workctx.adapters.filesystem.registry import list_context_inventory

    warnings: list[CliDiagnostic] = []
    registry_unavailable = False
    try:
        entries = list_context_inventory()
    except Exception:
        entries = ()
        registry_unavailable = True
        warnings.append(
            CliDiagnostic(
                code="CONTEXT_REGISTRY_UNAVAILABLE",
                message=(
                    "The advisory user registry could not be read; no registrations are shown."
                ),
            )
        )

    contexts = [_context_inventory_payload(entry) for entry in entries]
    result: dict[str, JsonValue] = {
        "count": len(contexts),
        "contexts": cast(JsonValue, contexts),
    }
    if json_output:
        emit_success(result=result, warnings=warnings)
    else:
        _render_context_inventory(entries, registry_unavailable=registry_unavailable)


@context_app.command("unregister")
def context_unregister(
    context_id: Annotated[
        str,
        typer.Argument(help="Registered context ID to remove from the user registry."),
    ],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Remove one advisory registration without touching its context directory."""
    begin_command("context.unregister", json_output=json_output)

    from workctx.adapters.filesystem.registry import unregister_context

    removed = unregister_context(context_id)
    result: dict[str, JsonValue] = {"id": context_id, "removed": removed}
    if json_output:
        emit_success(result=result)
    elif removed:
        output_console.print(Text.assemble("Unregistered context ", (context_id, "bold"), "."))
    else:
        output_console.print(
            Text.assemble("Context ", (context_id, "bold"), " was not registered.")
        )


def _context_inventory_payload(entry: ContextInventoryEntry) -> dict[str, JsonValue]:
    stats: JsonValue = None
    if entry.stats is not None:
        stats = {
            "tasks": entry.stats.tasks,
            "entities": entry.stats.entities,
            "evidence_notes": entry.stats.evidence_notes,
            "pending_inbox_artifacts": entry.stats.pending_inbox_artifacts,
            "ledger_events": entry.stats.ledger_events,
            "last_ledger_activity": _context_inventory_timestamp(entry.stats.last_ledger_activity),
        }
    return {
        "id": entry.context_id,
        "configured_id": entry.configured_context_id,
        "name": entry.name,
        "kind": entry.kind,
        "profile": entry.profile,
        "language": entry.language,
        "path": str(entry.root),
        "active": entry.active,
        "missing": entry.missing,
        "mismatched": entry.mismatched,
        "stats": stats,
        "error": entry.error,
    }


def _context_inventory_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _render_context_inventory(
    entries: tuple[ContextInventoryEntry, ...],
    *,
    registry_unavailable: bool,
) -> None:
    caption = (
        "The advisory user registry could not be read; no registrations are shown."
        if registry_unavailable
        else None
    )
    table = Table(title="Registered contexts", caption=caption, show_lines=True)
    table.add_column("Context", min_width=14, max_width=26)
    table.add_column("Type", min_width=7, max_width=16)
    table.add_column("Path", min_width=8, max_width=24)
    table.add_column("Stats", min_width=8, max_width=17)
    table.add_column("Activity / state", min_width=16, max_width=24)
    for entry in entries:
        stats = entry.stats
        state: list[str] = []
        if entry.active:
            state.append("active")
        if entry.missing:
            state.append("missing")
        if entry.mismatched:
            state.append(f"mismatched ({entry.configured_context_id or 'unknown'})")
        if entry.error is not None:
            state.append(f"error: {entry.error}")
        counts = (
            (
                f"T {stats.tasks}  E {stats.entities}\n"
                f"Ev {stats.evidence_notes}  In {stats.pending_inbox_artifacts}  "
                f"L {stats.ledger_events}"
            )
            if stats is not None
            else "-"
        )
        display_name = entry.name or "-"
        identity = (
            entry.context_id
            if display_name.casefold() == entry.context_id.casefold()
            else f"{entry.context_id} ({display_name})"
        )
        table.add_row(
            identity,
            f"{entry.kind or '-'}\n{entry.profile or '-'} / {entry.language or '-'}",
            str(entry.root),
            counts,
            (
                f"{_context_inventory_timestamp(stats.last_ledger_activity) or '-'}\n"
                f"{', '.join(state) if state else 'ok'}"
                if stats is not None
                else f"-\n{', '.join(state) if state else 'ok'}"
            ),
        )
    output_console.print(table, crop=False)


@context_app.command("inspect")
def context_inspect(
    path: Annotated[Path | None, typer.Argument(help="Path inside a context.")] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show resolved context configuration."""
    begin_command("context.inspect", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path, positional_path=path)
    config = load_context_config(root)
    result: dict[str, JsonValue] = {
        "root": str(root),
        "context": config.model_dump(mode="json"),
    }

    if json_output:
        emit_success(result=result, context_id=config.id)
    else:
        output_console.print(f"[bold]Context:[/bold] {config.name} ({config.id})")
        output_console.print(f"[bold]Root:[/bold] {root}")
        output_console.print(f"[bold]Profile:[/bold] {config.profile.value}")
        output_console.print(f"[bold]Classification:[/bold] {config.classification.value}")
        output_console.print(f"[bold]User language:[/bold] {config.languages.user_interaction}")


@context_app.command("validate")
def context_validate(
    path: Annotated[Path | None, typer.Argument(help="Context root or path inside it.")] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    strict: Annotated[bool, typer.Option("--strict", help="Escalate warnings to errors.")] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Validate workspace integrity, references, and safety checks."""
    _validate_context(
        path=path,
        context_path=context_path,
        strict=strict,
        json_output=json_output,
    )


@app.command("validate")
def validate_alias(
    path: Annotated[Path | None, typer.Argument(help="Context root or path inside it.")] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    strict: Annotated[bool, typer.Option("--strict", help="Escalate warnings to errors.")] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Alias for `workctx context validate`."""
    _validate_context(
        path=path,
        context_path=context_path,
        strict=strict,
        json_output=json_output,
    )


@proposal_app.command("validate")
def proposal_validate(
    file: Annotated[Path, typer.Argument(help="JSON transaction proposal file.")],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Validate a typed proposal without changing context state."""
    from workctx.transactions import validate_proposal

    begin_command("proposal.validate", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    proposal = _load_transaction_proposal(file)
    validation = validate_proposal(root, proposal)
    result: dict[str, JsonValue] = {
        "validation": cast("dict[str, JsonValue]", validation.model_dump(mode="json"))
    }
    warnings, errors = _transaction_cli_diagnostics(validation.diagnostics)
    if not validation.valid:
        record_failure(
            result=result,
            context_id=validation.context_id,
            warnings=warnings,
            errors=errors,
        )
        raise UserCorrectableError("Transaction proposal validation failed.")
    if json_output:
        emit_success(result=result, context_id=validation.context_id, warnings=warnings)
    else:
        output_console.print(
            Text(f"Proposal {validation.proposal_id} is valid for {validation.context_id}.")
        )


@proposal_app.command("show")
def proposal_show(
    file: Annotated[Path, typer.Argument(help="JSON transaction proposal file.")],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show exact proposal effects through a non-mutating dry run."""
    from workctx.transactions import dry_run

    begin_command("proposal.show", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    proposal = _load_transaction_proposal(file)
    preview = dry_run(root, proposal)
    _complete_transaction_preview(preview, json_output=json_output)


@transaction_app.command("apply")
def transaction_apply(
    file: Annotated[Path, typer.Argument(help="JSON transaction proposal file.")],
    dry_run_only: Annotated[
        bool,
        typer.Option("--dry-run", help="Show exact effects without applying them."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve and apply the reviewed local transaction."),
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Preview by default, or atomically apply an explicitly approved proposal."""
    from workctx.transactions import (
        ProposalValidationError,
        TransactionConflictError,
        apply,
        dry_run,
    )

    begin_command("transaction.apply", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    proposal = _load_transaction_proposal(file)
    if dry_run_only or not yes:
        preview = dry_run(root, proposal)
        confirmation = None if yes else "Re-run with --yes to apply these intended changes."
        _complete_transaction_preview(
            preview,
            json_output=json_output,
            confirmation=confirmation,
            include_mode=True,
        )
        return

    try:
        receipt = apply(root, proposal, approved=bool(yes))
    except ProposalValidationError as exc:
        validation = exc.result
        warnings, errors = _transaction_cli_diagnostics(validation.diagnostics)
        result: dict[str, JsonValue] = {
            "dry_run": False,
            "validation": cast("dict[str, JsonValue]", validation.model_dump(mode="json")),
        }
        record_failure(
            result=result,
            context_id=validation.context_id,
            warnings=warnings,
            errors=errors,
        )
        raise
    except TransactionConflictError as exc:
        record_failure(
            result={"dry_run": False, "proposal_id": proposal.id},
            context_id=proposal.context_id,
            errors=[
                CliDiagnostic(
                    code=exc.code,
                    message="Transaction conflicts with the current context revision.",
                )
            ],
        )
        raise

    result = {
        "dry_run": False,
        "receipt": cast("dict[str, JsonValue]", receipt.model_dump(mode="json")),
    }
    receipt_warnings: list[CliDiagnostic] = []
    if receipt.projection.state.value == "stale":
        receipt_warnings.append(
            CliDiagnostic(
                code=receipt.projection.diagnostic_code or "TXN-PROJECTION-STALE",
                message="The transaction committed but its derived projection is stale.",
            )
        )
    if json_output:
        emit_success(result=result, context_id=receipt.context_id, warnings=receipt_warnings)
    else:
        output_console.print(
            Text(
                f"Applied {receipt.proposal_id}: {len(receipt.applied_targets)} targets; "
                f"ledger event {receipt.ledger_event_id}."
            )
        )


@transaction_app.command("history")
def transaction_history(
    limit: Annotated[
        int, typer.Option("--limit", min=1, max=1000, help="Maximum recent events.")
    ] = 20,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show a verified audit summary and the recent ledger-event tail."""
    from workctx.transactions import audit_summary

    begin_command("transaction.history", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    summary = audit_summary(root)
    events = _ledger_event_tail(root, limit=limit)
    result: dict[str, JsonValue] = {
        "summary": {
            "event_count": summary.event_count,
            "head_hash": summary.head_hash,
            "first_event_id": summary.first_event_id,
            "last_event_id": summary.last_event_id,
            "last_proposal_id": summary.last_proposal_id,
            "last_timestamp": (
                summary.last_timestamp.isoformat() if summary.last_timestamp is not None else None
            ),
        },
        "events": [cast("dict[str, JsonValue]", event.model_dump(mode="json")) for event in events],
    }
    if json_output:
        emit_success(result=result, context_id=summary.context_id)
    else:
        output_console.print(
            Text(f"Showing {len(events)} of {summary.event_count} verified transaction events.")
        )


@transaction_app.command("show")
def transaction_show(
    identifier: Annotated[str, typer.Argument(help="Transaction proposal ID or audit event ID.")],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Resolve one verified ledger event by event or proposal identity."""
    from workctx.transactions.ledger import find_event_by_id, find_event_by_proposal_id

    begin_command("transaction.show", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    if identifier.startswith("AUD-"):
        event = find_event_by_id(root, identifier)
    elif identifier.startswith("TXP-"):
        event = find_event_by_proposal_id(root, identifier)
    else:
        raise UserCorrectableError(
            "Transaction identity must start with TXP- or audit identity with AUD-."
        )
    context_id = load_context_config(root).id
    if event is None:
        result: dict[str, JsonValue] = {"identifier": identifier}
        record_failure(
            result=result,
            context_id=context_id,
            errors=[
                CliDiagnostic(
                    code="TRANSACTION_NOT_FOUND",
                    message="Transaction or audit event did not resolve.",
                )
            ],
        )
        raise UserCorrectableError("Transaction or audit event did not resolve.")
    result = {"event": cast("dict[str, JsonValue]", event.model_dump(mode="json"))}
    if json_output:
        emit_success(result=result, context_id=event.context_id)
    else:
        output_console.print(
            Text(f"{event.id}: proposal {event.proposal_id} {event.result} ({event.event_hash})")
        )


@inbox_app.command("add")
def inbox_add(
    files: Annotated[
        list[Path],
        typer.Argument(help="Files already present below the selected context's 00_inbox/raw."),
    ],
    source: Annotated[
        str | None,
        typer.Option("--source", help="Source type or origin metadata."),
    ] = None,
    event_date: Annotated[
        str | None,
        typer.Option("--event-date", help="ISO 8601 source event date or timestamp."),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Register one or more existing raw artifacts."""
    begin_command("inbox.add", json_output=json_output)

    from workctx.domain import ArtifactSourceType
    from workctx.ingestion import (
        IngestionService,
        RegisterRequest,
    )

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    service = IngestionService(root)
    event_at, event_at_inferred = _parse_event_date(event_date)
    normalized_source = source.strip() if source is not None else None
    if normalized_source == "":
        raise UserCorrectableError("Inbox source metadata must not be empty.")
    source_type = ArtifactSourceType.OTHER
    if normalized_source is not None:
        source_type = next(
            (
                candidate
                for candidate in ArtifactSourceType
                if candidate.value == normalized_source.lower()
            ),
            ArtifactSourceType.OTHER,
        )

    requests: list[RegisterRequest] = []
    input_failure: tuple[int, str, Exception] | None = None
    for index, file in enumerate(files):
        try:
            relative_path = _raw_artifact_path(root, file)
            if not relative_path.startswith("00_inbox/raw/"):
                raise UserCorrectableError(
                    f"Inbox artifacts must live under 00_inbox/raw/ inside the context, "
                    f"not at: {relative_path}"
                )
        except Exception as exc:
            input_failure = (index, file.as_posix(), exc)
            break
        try:
            request = RegisterRequest(
                path=relative_path,
                source_type=source_type,
                source_origin=normalized_source,
                event_at=event_at,
                event_at_inferred=event_at_inferred,
            )
        except ValidationError:
            input_failure = (
                index,
                relative_path,
                UserCorrectableError("Inbox artifact metadata is invalid."),
            )
            break
        requests.append(request)

    batch = service.register_batch(requests)
    outcomes: list[dict[str, JsonValue]] = []
    failure: tuple[str, Exception] | None = None
    for outcome in batch.outcomes:
        if outcome.registration is not None:
            outcomes.append(
                _registration_outcome_payload(outcome.request.path, outcome.registration)
            )
            continue
        if outcome.duplicate is not None:
            duplicate = outcome.duplicate
            outcomes.append(
                {
                    "path": outcome.request.path,
                    "outcome": "duplicate",
                    "disposition": "duplicate_refused",
                    "artifact_id": duplicate.manifest.id,
                    "reference": duplicate.reference,
                    "status": duplicate.manifest.status.value,
                    "duplicate_of": duplicate.manifest.id,
                    "diagnostics": [],
                }
            )
            continue
        item_outcome = "failed" if outcome.attempted else "not_attempted"
        outcomes.append(
            {
                "path": outcome.request.path,
                "outcome": item_outcome,
                "disposition": None,
                "artifact_id": None,
                "reference": None,
                "status": None,
                "duplicate_of": None,
                "diagnostics": [],
            }
        )
        if outcome.error is not None:
            failure = (outcome.request.path, outcome.error)

    if input_failure is not None:
        failure_index, failure_path, failure_error = input_failure
        outcomes.append(
            {
                "path": failure_path,
                "outcome": "failed",
                "disposition": None,
                "artifact_id": None,
                "reference": None,
                "status": None,
                "duplicate_of": None,
                "diagnostics": [],
            }
        )
        outcomes.extend(
            {
                "path": remaining.as_posix(),
                "outcome": "not_attempted",
                "disposition": None,
                "artifact_id": None,
                "reference": None,
                "status": None,
                "duplicate_of": None,
                "diagnostics": [],
            }
            for remaining in files[failure_index + 1 :]
        )
        failure = (failure_path, failure_error)

    result: dict[str, JsonValue] = {
        "count": len(outcomes),
        "outcomes": cast(JsonValue, outcomes),
    }
    if failure is not None:
        failure_path, failure_error = failure
        record_failure(
            result=result,
            context_id=context_id,
            errors=[
                CliDiagnostic(
                    code="INBOX_REGISTRATION_FAILED",
                    message=sanitize_message(failure_error),
                    path=failure_path,
                )
            ],
        )
        if not json_output:
            for payload_outcome in outcomes:
                output_console.print(
                    Text(f"{payload_outcome['path']}: {payload_outcome['outcome']}")
                )
        raise failure_error
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        for payload_outcome in outcomes:
            output_console.print(
                Text(
                    f"{payload_outcome['path']}: {payload_outcome['outcome']} "
                    f"({payload_outcome['artifact_id']})"
                )
            )


@inbox_app.command("list")
def inbox_list(
    status: Annotated[
        str | None,
        typer.Option("--status", help="Restrict to one artifact status."),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List registered artifact manifests in deterministic ID order."""
    begin_command("inbox.list", json_output=json_output)

    from workctx.domain import ArtifactStatus
    from workctx.ingestion import IngestionService

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    statuses: frozenset[ArtifactStatus] | None = None
    if status is not None:
        try:
            statuses = frozenset({ArtifactStatus(status)})
        except ValueError as exc:
            raise UserCorrectableError("Inbox status is not supported.") from exc
    listing = IngestionService(root).list_inbox(statuses=statuses)
    artifacts = [_artifact_payload(artifact) for artifact in listing.artifacts]
    result: dict[str, JsonValue] = {
        "count": len(artifacts),
        "artifacts": cast(JsonValue, artifacts),
    }
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(Text(f"Found {len(artifacts)} registered artifacts."))


@artifact_app.command("show")
def artifact_show(
    identifier: Annotated[
        str,
        typer.Argument(help="Artifact ID or canonical artifact:// URI."),
    ],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show one registered artifact manifest."""
    begin_command("artifact.show", json_output=json_output)

    from workctx.ingestion import ArtifactNotFoundError, IngestionService

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    try:
        artifact = _resolve_artifact_record(IngestionService(root), identifier)
    except ArtifactNotFoundError:
        message = "Artifact did not resolve in the selected context."
        record_failure(
            result={"identifier": identifier},
            context_id=context_id,
            errors=[CliDiagnostic(code="ARTIFACT_NOT_FOUND", message=message)],
        )
        raise UserCorrectableError(message) from None
    result: dict[str, JsonValue] = {"artifact": _artifact_payload(artifact)}
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(
            Text(
                f"{artifact.manifest.id} [{artifact.manifest.status.value}]: "
                f"{artifact.manifest.original_name}"
            )
        )


@artifact_app.command("verify")
def artifact_verify(
    identifier: Annotated[
        str,
        typer.Argument(help="Artifact ID or canonical artifact:// URI."),
    ],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Stream the preserved artifact and compare its SHA-256 with the manifest."""
    begin_command("artifact.verify", json_output=json_output)

    from workctx.ingestion import ArtifactNotFoundError, IngestionService

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    try:
        artifact = _resolve_artifact_record(IngestionService(root), identifier)
    except ArtifactNotFoundError:
        message = "Artifact did not resolve in the selected context."
        record_failure(
            result={"identifier": identifier},
            context_id=context_id,
            errors=[CliDiagnostic(code="ARTIFACT_NOT_FOUND", message=message)],
        )
        raise UserCorrectableError(message) from None
    actual_hash = _stream_artifact_hash(root, artifact)
    expected_hash = artifact.manifest.content_hash
    matches = actual_hash == expected_hash
    result: dict[str, JsonValue] = {
        "verification": {
            "artifact_id": artifact.manifest.id,
            "reference": artifact.reference,
            "path": artifact.manifest.preserved_path,
            "expected_hash": expected_hash,
            "actual_hash": actual_hash,
            "matches": matches,
        }
    }
    if not matches:
        message = "Artifact content hash does not match its manifest."
        record_failure(
            result=result,
            context_id=context_id,
            errors=[CliDiagnostic(code="ARTIFACT_HASH_MISMATCH", message=message)],
        )
        raise UserCorrectableError(message)
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(Text(f"{artifact.manifest.id}: content hash matches."))


@app.command("brief")
def brief_command(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Build a read-only structured operational brief."""
    begin_command("brief", json_output=json_output)

    from workctx.views import brief

    root = resolve_cli_context(explicit_path=context_path)
    payload = brief(root)
    result = cast("dict[str, JsonValue]", payload.model_dump(mode="json"))
    if json_output:
        emit_success(result=result, context_id=payload.context_id)
    else:
        _render_brief_payload(payload)


@view_app.command("rebuild")
def view_rebuild(
    only: Annotated[
        str | None,
        typer.Option("--only", help="Rebuild one named operational view."),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Rebuild all generated operational views or one selected view."""
    begin_command("view.rebuild", json_output=json_output)

    from workctx.views import ViewName, rebuild_view, rebuild_views

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    if only is None:
        rebuilt = rebuild_views(root)
    else:
        try:
            selected = ViewName(only)
        except ValueError as exc:
            choices = ", ".join(view.value for view in ViewName)
            message = f"View name must be one of: {choices}."
            record_failure(
                result={"only": only},
                context_id=context_id,
                errors=[CliDiagnostic(code="VIEW_NAME_INVALID", message=message)],
            )
            raise UserCorrectableError(message) from exc
        rebuilt = rebuild_view(root, selected)

    result = cast("dict[str, JsonValue]", rebuilt.model_dump(mode="json"))
    if json_output:
        emit_success(result=result, context_id=rebuilt.context_id)
    else:
        names = ", ".join(view.name.value for view in rebuilt.views)
        output_console.print(
            Text(
                f"Rebuilt {len(rebuilt.views)} operational view(s) at "
                f"revision {rebuilt.source_revision}: {names}."
            ),
            soft_wrap=True,
        )


@index_app.command("rebuild")
def index_rebuild(
    path: Annotated[Path | None, typer.Argument(help="Context root or path inside it.")] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Rebuild the SQLite/FTS projection from canonical files."""
    from workctx.adapters.sqlite import SQLiteProjection

    begin_command("index.rebuild", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path, positional_path=path)
    report = SQLiteProjection(root).rebuild()
    counts = report.counts
    result: dict[str, JsonValue] = {
        "root": str(root),
        "trigger": report.trigger.value,
        "counts": {
            "entities": counts.entities,
            "edges": counts.edges,
            "observations": counts.observations,
            "claims": counts.claims,
            "tasks": counts.tasks,
        },
        "skipped": [
            {
                "path": sanitize_message(skipped.path),
                "reason": skipped.reason.value,
            }
            for skipped in report.skipped_documents
        ],
    }
    if json_output:
        emit_success(result=result)
    else:
        output_console.print(
            Text.assemble(
                "Rebuilt projection at ",
                str(root),
                f": {counts.entities} entities, {counts.edges} edges, "
                f"{counts.tasks} tasks, {len(report.skipped_documents)} skipped",
            ),
            soft_wrap=True,
        )


def _validate_context(
    *, path: Path | None, context_path: Path | None, json_output: bool, strict: bool = False
) -> None:
    from workctx.adapters.sqlite.freshness import SqliteFreshnessProbe
    from workctx.adapters.sqlite.projection import projection_database_path

    begin_command("context.validate", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path, positional_path=path)
    # Freshness is only checked once a projection has been built: a workspace
    # that never ran `index rebuild` should not warn about derived state.
    probe = SqliteFreshnessProbe() if projection_database_path(root).is_file() else None
    report = validate_workspace(root, strict=strict, freshness_probe=probe)
    serialized_issues = [_serialize_issue(issue) for issue in report.issues]
    warnings = [_diagnostic_from_issue(issue) for issue in report.warnings]
    errors = [_diagnostic_from_issue(issue) for issue in report.errors]
    result: dict[str, JsonValue] = {
        "root": str(report.context_root),
        "issues": cast(JsonValue, serialized_issues),
    }

    if not report.ok:
        if not json_output:
            _render_validation(report, serialized_issues)
        record_failure(
            result=result,
            context_id=report.context_id,
            warnings=warnings,
            errors=errors,
        )
        raise UserCorrectableError("Context validation failed.")

    if json_output:
        emit_success(result=result, context_id=report.context_id, warnings=warnings)
    else:
        _render_validation(report, serialized_issues)


@ref_app.command("show")
def ref_show(
    uri: Annotated[str, typer.Argument(help="Canonical workctx://, artifact://, or repo:// URI.")],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Resolve a canonical reference to its projected record."""
    from workctx.retrieval import resolve

    begin_command("ref.show", json_output=json_output)
    reader = _projection_reader(context_path)
    resolution = _retrieval_call(lambda: resolve(reader, uri))
    result: dict[str, JsonValue] = {"resolution": _resolution_payload(resolution)}
    if not resolution.found:
        record_failure(
            result=result,
            context_id=reader.context_id,
            errors=[CliDiagnostic(code="REF-NOT-FOUND", message="Reference did not resolve.")],
        )
        raise UserCorrectableError("Reference did not resolve.")
    if json_output:
        emit_success(result=result, context_id=reader.context_id)
    else:
        output_console.print(Text(f"{resolution.reference}: {resolution.status.value}"))


@ref_app.command("related")
def ref_related(
    uri: Annotated[str, typer.Argument(help="Canonical workctx:// URI.")],
    depth: Annotated[int, typer.Option("--depth", min=0, help="Traversal depth.")] = 1,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Traverse typed relations around a reference."""
    from workctx.retrieval import related

    begin_command("ref.related", json_output=json_output)
    reader = _projection_reader(context_path)
    outcome = _retrieval_call(lambda: related(reader, uri, depth=depth))
    result: dict[str, JsonValue] = {
        "focal": _resolution_payload(outcome.focal),
        "depth": outcome.max_depth,
        "direction": outcome.direction.value,
        "nodes": [
            {
                "depth": node.depth,
                "reference": node.reference,
            }
            for node in outcome.nodes
        ],
        "edges": [
            {
                "depth": edge.depth,
                "direction": edge.direction.value,
                "source": str(edge.edge.source_uri),
                "relation": edge.edge.relation.value,
                "target": edge.edge.target,
            }
            for edge in outcome.edges
        ],
    }
    if json_output:
        emit_success(result=result, context_id=reader.context_id)
    else:
        output_console.print(
            Text(f"{len(outcome.nodes)} related nodes, {len(outcome.edges)} edges")
        )


@ref_app.command("trace")
def ref_trace(
    uri: Annotated[str, typer.Argument(help="Canonical workctx:// URI.")],
    include_history: Annotated[
        bool, typer.Option("--history", help="Include superseded claims.")
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Trace a reference through claims and observations to source locators."""
    from workctx.retrieval import trace

    begin_command("ref.trace", json_output=json_output)
    reader = _projection_reader(context_path)
    outcome = _retrieval_call(lambda: trace(reader, uri, include_history=include_history))
    result: dict[str, JsonValue] = {
        "focal": _resolution_payload(outcome.focal),
        "claims": [
            {
                "id": claim.id,
                "subject": str(claim.subject),
                "predicate": claim.predicate,
                "status": claim.status.value,
            }
            for claim in outcome.claims
        ],
        "observations": [
            {
                "id": traced.observation.id,
                "source_ref": str(traced.source_ref),
                "locator_type": traced.locator.type,
                "referenced_by": list(traced.referenced_by),
            }
            for traced in outcome.observations
        ],
        "missing_observations": [
            {
                "reference": missing.reference,
                "reason": missing.reason.value,
                "referenced_by": list(missing.referenced_by),
            }
            for missing in outcome.missing_observations
        ],
    }
    if json_output:
        emit_success(result=result, context_id=reader.context_id)
    else:
        output_console.print(
            Text(
                f"{len(outcome.claims)} claims, {len(outcome.observations)} observations, "
                f"{len(outcome.missing_observations)} missing"
            )
        )


@app.command("context-pack")
def context_pack(
    uri: Annotated[str, typer.Argument(help="Focal canonical workctx:// URI.")],
    budget: Annotated[int, typer.Option("--budget", min=0, help="Pack budget in units.")] = 12000,
    query: Annotated[str | None, typer.Option("--query", help="Ranking query hint.")] = None,
    include_history: Annotated[
        bool, typer.Option("--history", help="Include superseded claim history.")
    ] = False,
    include_architecture: Annotated[
        bool, typer.Option("--architecture", help="Include one-hop architecture entities.")
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Build a bounded, traceable context pack for a focal entity."""
    import json as json_module

    from workctx.retrieval import build_pack, serialize_context_pack

    begin_command("context-pack", json_output=json_output)
    reader = _projection_reader(context_path)
    outcome = _retrieval_call(
        lambda: build_pack(
            reader,
            uri,
            budget=budget,
            query=query,
            include_history=include_history,
            include_architecture=include_architecture,
        )
    )
    if not outcome.built or outcome.pack is None:
        message = sanitize_message(outcome.message or "Context pack could not be built.")
        record_failure(
            result={"reference": outcome.reference, "status": outcome.status.value},
            context_id=reader.context_id,
            errors=[CliDiagnostic(code="PACK-NOT-BUILT", message=message)],
        )
        raise UserCorrectableError(message)

    pack_payload = cast(
        "dict[str, JsonValue]", json_module.loads(serialize_context_pack(outcome.pack))
    )
    result: dict[str, JsonValue] = {"pack": pack_payload}
    if json_output:
        emit_success(result=result, context_id=reader.context_id)
    else:
        truncation = outcome.pack.sections.budget_and_truncation
        output_console.print(
            Text(
                f"Pack for {outcome.reference}: {truncation.used_units}/"
                f"{truncation.requested_units} units"
            )
        )


@app.command("search")
def search_command(
    query: Annotated[str, typer.Argument(help="Literal full-text search query.")],
    entity_types: Annotated[
        list[EntityType] | None,
        typer.Option("--type", help="Restrict to an entity type; repeatable."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", min=1, max=1000, help="Maximum result count.")
    ] = 20,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Search the isolated SQLite/FTS projection."""
    from workctx.adapters.sqlite import SQLiteProjection

    begin_command("search", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    projection = SQLiteProjection(root)
    try:
        hits = projection.search(
            query,
            entity_types=(None if entity_types is None else frozenset(entity_types)),
            limit=limit,
        )
    except ValueError as exc:
        raise UserCorrectableError(sanitize_message(str(exc))) from exc
    result: dict[str, JsonValue] = {
        "query": query,
        "count": len(hits),
        "hits": [_search_hit_payload(hit) for hit in hits],
    }
    if json_output:
        emit_success(result=result, context_id=projection.context_id)
    else:
        output_console.print(Text(f"Found {len(hits)} results for {query!r}."))


@task_app.command("list")
def task_list(
    statuses: Annotated[
        list[TaskStatus] | None,
        typer.Option("--status", help="Restrict to a task status; repeatable."),
    ] = None,
    waiting_on: Annotated[
        str | None, typer.Option("--waiting-on", help="Restrict to one waiting-on value.")
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List projected tasks matching operational filters."""
    from workctx.adapters.sqlite import SQLiteProjection, TaskQuery

    begin_command("task.list", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    projection = SQLiteProjection(root)
    query = TaskQuery(
        statuses=None if statuses is None else frozenset(statuses),
        waiting_on=waiting_on,
    )
    try:
        tasks = projection.query_tasks(query)
    except ValueError as exc:
        raise UserCorrectableError(sanitize_message(str(exc))) from exc
    result: dict[str, JsonValue] = {
        "count": len(tasks),
        "tasks": [_task_summary_payload(task) for task in tasks],
    }
    if json_output:
        emit_success(result=result, context_id=projection.context_id)
    else:
        output_console.print(Text(f"Found {len(tasks)} matching tasks."))


@task_app.command("show")
def task_show(
    task_id: Annotated[str, typer.Argument(help="Task ID or canonical task URI.")],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show one projected canonical task."""
    from workctx.adapters.sqlite import SQLiteProjection

    begin_command("task.show", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    projection = SQLiteProjection(root)
    try:
        task = projection.get_task(task_id)
    except ValueError as exc:
        raise UserCorrectableError(sanitize_message(str(exc))) from exc
    if task is None:
        result: dict[str, JsonValue] = {"task_id": task_id}
        record_failure(
            result=result,
            context_id=projection.context_id,
            errors=[
                CliDiagnostic(
                    code="TASK_NOT_FOUND",
                    message="Task did not resolve in the selected context.",
                )
            ],
        )
        raise UserCorrectableError("Task did not resolve in the selected context.")
    result = {"task": _task_payload(task)}
    if json_output:
        emit_success(result=result, context_id=projection.context_id)
    else:
        output_console.print(Text(f"{task.id} [{task.status.value}]: {task.title}"))


@suggestion_app.command("list")
def suggestion_list(
    statuses: Annotated[
        list[SuggestionStatus] | None,
        typer.Option("--status", help="Restrict to a suggestion status; repeatable."),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List canonical suggestion records in deterministic order."""

    from workctx.suggestions import list_suggestions

    begin_command("suggestion.list", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    documents = list_suggestions(
        root,
        statuses=None if statuses is None else frozenset(statuses),
    )
    result: dict[str, JsonValue] = {
        "count": len(documents),
        "suggestions": [_suggestion_summary_payload(document) for document in documents],
    }
    context_id = load_context_config(root).id
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(Text(f"Found {len(documents)} suggestion records."))


@suggestion_app.command("show")
def suggestion_show(
    suggestion_id: Annotated[
        str,
        typer.Argument(help="Suggestion ID or canonical investigation URI."),
    ],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show one canonical suggestion record and its Markdown body."""

    from workctx.suggestions import get_suggestion

    begin_command("suggestion.show", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    document = get_suggestion(root, suggestion_id)
    result: dict[str, JsonValue] = {"suggestion": _suggestion_document_payload(document)}
    context_id = load_context_config(root).id
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(
            Text(
                f"{document.record.id} [{document.record.status.value}] "
                f"{document.record.type.value}: {document.record.rationale}"
            )
        )


@suggestion_app.command("adopt")
def suggestion_adopt(
    suggestion_id: Annotated[str, typer.Argument(help="Suggestion ID or local URI.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve and atomically adopt the reviewed suggestion."),
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Adopt one reviewed suggestion; explicit --yes is mandatory."""

    from workctx.suggestions import adopt_suggestion

    begin_command("suggestion.adopt", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    _require_suggestion_yes(root, suggestion_id=suggestion_id, yes=yes)
    mutation = adopt_suggestion(root, suggestion_id, approved=yes)
    _complete_suggestion_mutation(mutation, json_output=json_output)


@suggestion_app.command("reject")
def suggestion_reject(
    suggestion_id: Annotated[str, typer.Argument(help="Suggestion ID or local URI.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve rejection of the reviewed suggestion."),
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Reject one reviewed suggestion; explicit --yes is mandatory."""

    from workctx.suggestions import reject_suggestion

    begin_command("suggestion.reject", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    _require_suggestion_yes(root, suggestion_id=suggestion_id, yes=yes)
    mutation = reject_suggestion(root, suggestion_id, approved=yes)
    _complete_suggestion_mutation(mutation, json_output=json_output)


@usage_app.command("status")
def usage_status_command(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show opt-in state, retained bytes, and rolling URI-use windows."""

    from workctx.usage import usage_status

    begin_command("usage.status", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    status = usage_status(root)
    result = cast("dict[str, JsonValue]", status.model_dump(mode="json"))
    context_id = load_context_config(root).id
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        state = "enabled" if status.enabled else "disabled"
        output_console.print(
            Text(
                f"Usage telemetry is {state}; {status.file_size_bytes} bytes in the "
                f"current file; {len(status.summary.targets)} URI targets summarized."
            )
        )


@usage_app.command("evaluate")
def usage_evaluate_command(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Evaluate advisory promotion and decay candidates without writing records."""

    from workctx.usage import evaluate_usage

    begin_command("usage.evaluate", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    candidates = evaluate_usage(root)
    result: dict[str, JsonValue] = {
        "count": len(candidates),
        "candidates": [_usage_candidate_payload(candidate) for candidate in candidates],
    }
    context_id = load_context_config(root).id
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(Text(f"Found {len(candidates)} advisory usage candidates."))


@usage_app.command("suggest")
def usage_suggest_command(
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve creation of canonical suggestion records."),
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Create approved WP-680 records for candidates not already open."""

    from workctx.usage import suggest_usage

    begin_command("usage.suggest", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    _require_usage_yes(root, yes=yes)
    outcome = suggest_usage(root, approved=yes)
    result = _usage_suggestion_payload(outcome)
    context_id = load_context_config(root).id
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(
            Text(
                f"Created {len(outcome.created)} suggestion records; "
                f"skipped {len(outcome.skipped)} already-open candidates."
            )
        )


@agent_app.command("detect")
def agent_detect(
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Detect supported agent clients without reading credentials."""
    from workctx.adapters.agents import detect_clients

    begin_command("agent.detect", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    capabilities = detect_clients(root)
    context_id = load_context_config(root).id
    result: dict[str, JsonValue] = {
        "clients": [_client_capability_payload(item) for item in capabilities]
    }
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(Text(f"Detected {len(capabilities)} supported client definitions."))


@agent_app.command("status")
def agent_status(
    agent: Annotated[
        str | None,
        typer.Option("--agent", help="codex, claude, gemini, or all (default)."),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show project adapter status for one or all supported clients."""
    from workctx.adapters.agents import AgentAdapterService

    begin_command("agent.status", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    clients = _agent_clients(agent)
    service = AgentAdapterService()
    statuses = tuple(service.status(root, client) for client in clients)
    context_id = load_context_config(root).id
    result: dict[str, JsonValue] = {
        "statuses": [_adapter_status_payload(status) for status in statuses]
    }
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        for status in statuses:
            _render_agent_status(status)
        output_console.print(Text(f"Inspected {len(statuses)} agent adapter statuses."))


@agent_app.command("forget")
def agent_forget(
    path: Annotated[
        Path | None,
        typer.Argument(help="Path inside the context whose adapter trust should be forgotten."),
    ] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides the positional path."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Forget machine-local adapter trust without changing any context file."""

    from workctx.adapters.agents import AgentAdapterService

    begin_command("agent.forget", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path, positional_path=path)
    forgotten = AgentAdapterService().forget(root)
    context_id = load_context_config(root).id
    treatment = "A subsequent agent install will treat existing adapter state as untracked."
    result: dict[str, JsonValue] = {
        "root": str(root),
        "removed": bool(forgotten),
        "adapters": [client.value for client in forgotten],
        "install_treatment": "untracked",
        "message": treatment,
    }
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        if forgotten:
            names = ", ".join(client.value for client in forgotten)
            output_console.print(Text(f"Forgot trusted adapter records for {names} at {root}."))
        else:
            output_console.print(Text(f"No trusted adapter records existed for {root}."))
        output_console.print(Text(treatment))


@agent_app.command("install")
def agent_install(
    agent: Annotated[str, typer.Option("--agent", help="codex, claude, gemini, or all.")],
    scope: Annotated[
        str, typer.Option("--scope", help="Installation scope (project only).")
    ] = "project",
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve and execute the complete reviewed plan."),
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Plan by default, or install project-scoped agent adapters with approval."""
    from workctx.adapters.agents import AgentAdapterService, ClientAvailability

    begin_command("agent.install", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    if scope != "project":
        raise UserCorrectableError("Agent adapter installation supports only project scope.")
    clients = _agent_clients(agent)
    service = AgentAdapterService()
    skipped_clients: tuple[str, ...] = ()
    if agent.strip().lower() == "all":
        availability = {item.client: item.availability for item in service.detect(root)}
        installable = tuple(
            client for client in clients if availability.get(client) is ClientAvailability.AVAILABLE
        )
        skipped_clients = tuple(client.value for client in clients if client not in installable)
        if not installable:
            raise UserCorrectableError(
                "No supported agent client is available on this machine; "
                "install one or select a specific --agent."
            )
        clients = installable
    plans = tuple(service.plan_install(root, client) for client in clients)
    receipts: tuple[OperationResult, ...] = ()
    if yes:
        receipts = tuple(
            service.install(plan, approvals=_agent_plan_approvals(plan)) for plan in plans
        )
    context_id = load_context_config(root).id
    result: dict[str, JsonValue] = {
        "applied": yes,
        "plans": [_adapter_plan_payload(plan) for plan in plans],
        "receipts": [_agent_operation_payload(receipt) for receipt in receipts],
        "skipped_clients": list(skipped_clients),
    }
    warnings = tuple(
        CliDiagnostic(
            code="AGENT_CLIENT_UNAVAILABLE",
            message=f"The {name} client is not available on this machine; skipped.",
        )
        for name in skipped_clients
    )
    if json_output:
        emit_success(result=result, context_id=context_id, warnings=warnings)
    else:
        for plan in plans:
            _render_agent_plan(plan)
        for diagnostic in warnings:
            output_console.print(Text(diagnostic.message))
        if yes:
            output_console.print(Text(f"Installed {len(receipts)} agent adapters."))
        else:
            output_console.print(Text("Re-run with --yes to execute this installation plan."))


@agent_app.command("refresh")
def agent_refresh(
    all_contexts: Annotated[
        bool,
        typer.Option("--all", help="Refresh every context in the machine registry (required)."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve and apply every successfully prepared context plan."),
    ] = False,
    agent: Annotated[
        str,
        typer.Option("--agent", help="codex, claude, gemini, or all (default)."),
    ] = "all",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Preview or apply agent adapter refreshes across every registered context."""

    from workctx.adapters.agents import AgentAdapterService
    from workctx.adapters.agents.fleet import refresh_registered_contexts

    begin_command("agent.refresh", json_output=json_output)
    if not all_contexts:
        message = (
            "Agent refresh currently requires --all; use 'workctx agent install' "
            "to refresh one context."
        )
        diagnostic = CliDiagnostic(
            code="AGENT_REFRESH_ALL_REQUIRED",
            message=message,
            path="$.selection",
        )
        record_failure(result={}, errors=[diagnostic])
        raise UsageConfigurationError(message)

    clients = _agent_clients(agent)
    batch = refresh_registered_contexts(
        service=AgentAdapterService(),
        clients=clients,
        apply=yes,
        approvals_for=_agent_plan_approvals,
    )
    result = _fleet_refresh_payload(batch)
    warnings = _fleet_refresh_warnings(batch)
    errors = _fleet_refresh_errors(batch)

    if not json_output:
        _render_fleet_diagnostics(warnings, errors)
        if yes:
            output_console.print(Text("Applied the approved fleet refresh where plans succeeded."))
        else:
            output_console.print(
                Text("Preview only; re-run with --all --yes to apply these plans.")
            )
        _render_fleet_refresh(batch)

    if errors:
        record_failure(result=result, warnings=warnings, errors=errors)
        raise StaleDerivedStateError(
            f"{batch.failure_count} agent refresh operation(s) failed; "
            "every registered context was processed."
        )
    if json_output:
        emit_success(result=result, warnings=warnings)


@agent_app.command("repair")
def agent_repair(
    agent: Annotated[str, typer.Option("--agent", help="codex, claude, gemini, or all.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve and execute the complete reviewed repair plan."),
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Plan by default, or repair drifted and untrusted agent adapters with approval."""
    from workctx.adapters.agents import AgentAdapterService

    begin_command("agent.repair", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    clients = _agent_clients(agent)
    service = AgentAdapterService()
    plans = tuple(service.plan_repair(root, client) for client in clients)
    receipts: tuple[OperationResult, ...] = ()
    if yes:
        receipts = tuple(
            service.repair(plan, approvals=_agent_plan_approvals(plan)) for plan in plans
        )
    context_id = load_context_config(root).id
    result: dict[str, JsonValue] = {
        "applied": yes,
        "plans": [_adapter_plan_payload(plan) for plan in plans],
        "receipts": [_agent_operation_payload(receipt) for receipt in receipts],
    }
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        for plan in plans:
            _render_agent_plan(plan)
        if yes:
            output_console.print(Text(f"Repaired {len(receipts)} agent adapters."))
        else:
            output_console.print(Text("Re-run with --yes to execute this repair plan."))


@agent_app.command("uninstall")
def agent_uninstall(
    agent: Annotated[str, typer.Option("--agent", help="codex, claude, gemini, or all.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Approve and execute the complete reviewed removal plan."),
    ] = False,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Plan by default, or remove manifest-owned adapter files with approval."""
    from workctx.adapters.agents import AgentAdapterService

    begin_command("agent.uninstall", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    clients = _agent_clients(agent)
    service = AgentAdapterService()
    plans = tuple(service.plan_uninstall(root, client) for client in clients)
    receipts: tuple[OperationResult, ...] = ()
    if yes:
        receipts = tuple(
            service.uninstall(plan, approvals=_agent_plan_approvals(plan)) for plan in plans
        )
    context_id = load_context_config(root).id
    result: dict[str, JsonValue] = {
        "applied": yes,
        "plans": [_adapter_plan_payload(plan) for plan in plans],
        "receipts": [_agent_operation_payload(receipt) for receipt in receipts],
    }
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        for plan in plans:
            _render_agent_plan(plan)
        if yes:
            output_console.print(Text(f"Removed {len(receipts)} agent adapters."))
        else:
            output_console.print(Text("Re-run with --yes to execute this removal plan."))


@agent_app.command("open")
def agent_open(
    agent: Annotated[str, typer.Option("--agent", help="codex, claude, or gemini.")],
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Open the selected context in one supported agent client."""
    from workctx.adapters.agents import open_context

    begin_command("agent.open", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    clients = _agent_clients(agent, allow_all=False)
    opened = open_context(root, clients[0])
    context_id = load_context_config(root).id
    result: dict[str, JsonValue] = {"session": _opened_context_payload(opened)}
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        output_console.print(
            Text(f"Opened {opened.client.value} in {opened.root} (process {opened.pid}).")
        )


def _projection_reader(context_path: Path | None) -> SQLiteProjection:
    from workctx.adapters.sqlite import SQLiteProjection

    root = resolve_cli_context(explicit_path=context_path)
    return SQLiteProjection(root)


def _render_brief_payload(payload: BriefPayload) -> None:
    output_console.print(Text(f"Daily brief — {payload.context_id}", style="bold"))
    output_console.print(Text("Today focus", style="bold"))
    if payload.today_focus:
        for task in payload.today_focus:
            output_console.print(
                Text(
                    f"  {task.id} [{task.priority.value}/{task.status.value}] "
                    f"{task.title} — {task.next_action}"
                ),
                soft_wrap=True,
            )
    else:
        output_console.print(Text("  None"))

    output_console.print(Text("Blockers", style="bold"))
    if payload.blockers:
        for task in payload.blockers:
            details = ", ".join(task.blockers) or task.next_action
            output_console.print(Text(f"  {task.id} — {details}"), soft_wrap=True)
    else:
        output_console.print(Text("  None"))

    output_console.print(Text("Waiting on", style="bold"))
    if payload.waiting_on:
        for group in payload.waiting_on:
            task_ids = ", ".join(task.id for task in group.tasks)
            output_console.print(
                Text(f"  {group.display_name}: {task_ids}"),
                soft_wrap=True,
            )
    else:
        output_console.print(Text("  None"))

    output_console.print(Text("Stale claims", style="bold"))
    if payload.stale_claims:
        for claim in payload.stale_claims:
            output_console.print(
                Text(f"  {claim.id} — {claim.predicate} ({claim.age_days} days old)"),
                soft_wrap=True,
            )
    else:
        output_console.print(Text("  None"))

    activity = payload.recent_ledger_activity
    output_console.print(
        Text(f"Recent ledger: {activity.event_count} event(s); revision {activity.head_revision}."),
        soft_wrap=True,
    )


def _parse_event_date(value: str | None) -> tuple[datetime | None, bool]:
    if value is None:
        return None, False

    from datetime import UTC, date, datetime, time

    normalized = value.strip()
    if not normalized:
        raise UserCorrectableError("Event date must not be empty.")
    try:
        if len(normalized) == 10:
            event_date = date.fromisoformat(normalized)
            return datetime.combine(event_date, time.min, tzinfo=UTC), True
        timestamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UserCorrectableError("Event date must use ISO 8601 format.") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise UserCorrectableError("Event timestamps must include a timezone offset.")
    return timestamp, False


def _raw_artifact_path(root: Path, path: Path) -> str:
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise UserCorrectableError(
            "Inbox artifact paths must stay inside the selected context."
        ) from exc


def _resolve_artifact_record(service: IngestionService, identifier: str) -> ArtifactRecord:
    from workctx.domain import ArtifactId, ArtifactReference
    from workctx.ingestion import ArtifactNotFoundError

    try:
        if identifier.startswith("artifact://"):
            reference = str(ArtifactReference.parse(identifier))
            matches = tuple(
                artifact
                for artifact in service.list_inbox().artifacts
                if artifact.reference == reference
            )
        else:
            artifact_id = str(ArtifactId.parse(identifier))
            matches = tuple(
                artifact
                for artifact in service.list_inbox().artifacts
                if artifact.manifest.id == artifact_id
            )
    except ValueError as exc:
        raise UserCorrectableError(
            "Artifact identity must be an ART ID or canonical artifact:// URI."
        ) from exc
    if not matches:
        raise ArtifactNotFoundError("The requested artifact manifest was not found.")
    return matches[0]


def _artifact_payload(artifact: ArtifactRecord) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", artifact.model_dump(mode="json"))


def _registration_outcome_payload(
    path: str,
    registration: RegistrationResult,
) -> dict[str, JsonValue]:
    from workctx.ingestion import RegistrationDisposition

    outcome = (
        "quarantined"
        if registration.disposition is RegistrationDisposition.QUARANTINED
        else (
            "duplicate"
            if registration.disposition
            in {
                RegistrationDisposition.ALREADY_REGISTERED,
                RegistrationDisposition.DUPLICATE_LINKED,
            }
            else "registered"
        )
    )
    manifest = registration.artifact.manifest
    return {
        "path": path,
        "outcome": outcome,
        "disposition": registration.disposition.value,
        "artifact_id": manifest.id,
        "reference": registration.artifact.reference,
        "status": manifest.status.value,
        "duplicate_of": manifest.duplicate_of,
        "diagnostics": [
            cast("dict[str, JsonValue]", diagnostic.model_dump(mode="json"))
            for diagnostic in registration.diagnostics
        ],
    }


def _stream_artifact_hash(root: Path, artifact: ArtifactRecord) -> str:
    import hashlib
    from pathlib import PurePosixPath

    from workctx.adapters.filesystem import CanonicalStore, ContextZone
    from workctx.ingestion import ArtifactReadError

    preserved_path = PurePosixPath(artifact.manifest.preserved_path).as_posix()
    if not any(
        preserved_path.startswith(f"{prefix}/")
        for prefix in ("00_inbox/raw", "00_inbox/quarantine", "01_processed")
    ):
        raise ArtifactReadError()
    path = CanonicalStore(root).resolve_path(
        preserved_path,
        zones=(ContextZone.INBOX, ContextZone.PROCESSED),
    )
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ArtifactReadError()
    if not path.is_file():
        raise ArtifactReadError()

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ArtifactReadError() from exc
    return f"sha256:{digest.hexdigest()}"


def _retrieval_call[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except ValueError as exc:
        raise UserCorrectableError(sanitize_message(str(exc))) from exc


def _resolution_payload(resolution: ResolutionResult) -> dict[str, JsonValue]:
    record = resolution.record
    payload: dict[str, JsonValue] = {
        "reference": resolution.reference,
        "status": resolution.status.value,
    }
    if record is not None:
        payload["record"] = {
            "id": record.id,
            "uri": str(record.uri),
            "kind": type(record).__name__.removesuffix("Record").lower(),
        }
    return payload


def _load_transaction_proposal(path: Path) -> TransactionProposal:
    import json

    from workctx.domain.transactions import TransactionProposal

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise UserCorrectableError("Transaction proposal file could not be read.") from exc
    except json.JSONDecodeError as exc:
        raise UserCorrectableError("Transaction proposal file is not valid JSON.") from exc
    try:
        return TransactionProposal.model_validate(payload)
    except ValidationError as exc:
        raise UserCorrectableError("Transaction proposal does not match the schema.") from exc


def _transaction_cli_diagnostics(
    diagnostics: tuple[TransactionDiagnostic, ...],
) -> tuple[list[CliDiagnostic], list[CliDiagnostic]]:
    warnings: list[CliDiagnostic] = []
    errors: list[CliDiagnostic] = []
    for diagnostic in diagnostics:
        item = CliDiagnostic(
            code=diagnostic.code,
            message=sanitize_message(diagnostic.message),
            path=(
                sanitize_message(diagnostic.path, fallback="")
                if diagnostic.path is not None
                else None
            ),
        )
        if diagnostic.severity.value == "error":
            errors.append(item)
        else:
            warnings.append(item)
    return warnings, errors


def _complete_transaction_preview(
    preview: DryRunResult,
    *,
    json_output: bool,
    confirmation: str | None = None,
    include_mode: bool = False,
) -> None:
    preview_payload = cast("dict[str, JsonValue]", preview.model_dump(mode="json"))
    result: dict[str, JsonValue]
    if include_mode:
        result = {
            "dry_run": True,
            "preview": preview_payload,
            "confirmation_required": confirmation is not None,
        }
        if confirmation is not None:
            result["next_action"] = confirmation
    else:
        result = {"dry_run": preview_payload}
    warnings, errors = _transaction_cli_diagnostics(preview.diagnostics)
    if not preview.valid:
        if not json_output:
            _render_transaction_preview(preview)
        record_failure(
            result=result,
            context_id=preview.context_id,
            warnings=warnings,
            errors=errors,
        )
        raise UserCorrectableError("Transaction proposal validation failed.")
    if json_output:
        emit_success(result=result, context_id=preview.context_id, warnings=warnings)
        return
    _render_transaction_preview(preview)
    if confirmation is not None:
        output_console.print(Text(confirmation))


def _render_transaction_preview(preview: DryRunResult) -> None:
    output_console.print(
        Text(f"Proposal {preview.proposal_id}: {len(preview.effects)} intended changes.")
    )
    for effect in preview.effects:
        destination = f" -> {effect.destination}" if effect.destination is not None else ""
        output_console.print(Text(f"  {effect.order}. {effect.op} {effect.target}{destination}"))


def _ledger_event_tail(root: Path, *, limit: int) -> tuple[AuditEvent, ...]:
    from collections import deque

    from workctx.domain.transactions import AuditEvent
    from workctx.transactions.ledger import LEDGER_RELATIVE_PATH

    path = root / Path(LEDGER_RELATIVE_PATH)
    if not path.is_file():
        return ()
    try:
        with path.open("r", encoding="utf-8") as ledger:
            lines = deque((line for line in ledger if line.strip()), maxlen=limit)
    except (OSError, UnicodeError) as exc:
        raise UserCorrectableError("Transaction audit ledger could not be read.") from exc
    try:
        return tuple(AuditEvent.model_validate_json(line) for line in lines)
    except ValidationError as exc:
        raise UserCorrectableError("Transaction audit ledger is invalid.") from exc


def _search_hit_payload(hit: SearchHit) -> dict[str, JsonValue]:
    return {
        "id": hit.id,
        "uri": str(hit.uri),
        "record_kind": hit.record_kind.value,
        "entity_type": hit.entity_type.value,
        "title": hit.title,
        "source_path": hit.source_path,
        "score": hit.score,
    }


def _task_summary_payload(task: TaskRecord) -> dict[str, JsonValue]:
    return {
        "id": task.id,
        "uri": str(task.uri),
        "title": task.title,
        "task_type": task.task_type.value,
        "status": task.status.value,
        "priority": task.priority.value,
        "owner": task.owner,
        "waiting_on": list(task.waiting_on),
        "due_at": task.due_at.isoformat() if task.due_at is not None else None,
        "next_action": task.next_action,
        "parent_task": task.parent_task,
        "root_task": task.root_task,
    }


def _task_payload(task: TaskRecord) -> dict[str, JsonValue]:
    return {
        **_task_summary_payload(task),
        "entity_type": task.entity_type.value,
        "aliases": list(task.aliases),
        "tags": list(task.tags),
        "confidence": task.confidence.value if task.confidence is not None else None,
        "requester": task.requester,
        "dependencies": list(task.dependencies),
        "blockers": list(task.blockers),
        "source_observations": list(task.source_observations),
        "body": task.body,
        "source_path": task.source_path,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _outbox_preview_payload(preview: SendPreview) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", preview.model_dump(mode="json"))


def _outbox_send_payload(delivered: SendResult) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", delivered.model_dump(mode="json"))


def _render_outbox_preview(preview: SendPreview) -> None:
    output_console.print(Text(f"Draft: {preview.draft_id}"))
    output_console.print(Text(f"Channel: {preview.channel}"))
    output_console.print(Text(f"Recipient: {preview.recipient_display}"))
    output_console.print(Text(f"Draft hash: {preview.draft_content_hash}"))
    output_console.print(Text(f"Send fingerprint: {preview.fingerprint}"))
    output_console.print(Text("Body:"))
    output_console.print(Text(preview.body))


def _record_outbox_failure(
    error: Exception,
    *,
    context_id: str,
    draft_id: str,
    channel: str,
    target: str,
) -> None:
    from workctx.drafting import (
        SendApprovalRequiredError,
        SendAuditCommitError,
        SendAuthenticationError,
        SendDeliveryError,
        SendFingerprintMismatchError,
        SendInputError,
        SendSecretError,
        SendStateError,
    )

    code = "OUTBOX_SEND_FAILED"
    path: str | None = None
    if isinstance(error, SendApprovalRequiredError):
        code = "OUTBOX_APPROVAL_REQUIRED"
        path = "$.yes"
    elif isinstance(error, SendFingerprintMismatchError):
        code = "OUTBOX_FINGERPRINT_MISMATCH"
        path = "$.fingerprint"
    elif isinstance(error, SendStateError):
        code = "OUTBOX_RESEND_REFUSED"
    elif isinstance(error, SendSecretError):
        code = "OUTBOX_SECRET_REFUSED"
        path = "$.body"
    elif isinstance(error, SendAuthenticationError):
        code = "OUTBOX_AUTH_UNAVAILABLE"
    elif isinstance(error, SendAuditCommitError):
        code = "OUTBOX_AUDIT_COMMIT_FAILED"
    elif isinstance(error, SendDeliveryError):
        code = "OUTBOX_DELIVERY_FAILED"
    elif isinstance(error, SendInputError):
        code = "OUTBOX_INPUT_INVALID"

    result: dict[str, JsonValue] = {"draft_id": draft_id}
    if not isinstance(error, (SendInputError, SendSecretError)):
        result.update({"channel": channel, "target": target})
    if isinstance(error, SendAuditCommitError):
        result["remote_comment_id"] = error.remote_comment_id
        result["remote_comment_url"] = error.remote_comment_url
    record_failure(
        result=result,
        context_id=context_id,
        errors=[
            CliDiagnostic(
                code=code,
                message=sanitize_message(error),
                path=path,
            )
        ],
    )


def _suggestion_summary_payload(document: SuggestionDocument) -> dict[str, JsonValue]:
    record = document.record
    return {
        "id": record.id,
        "uri": record.uri,
        "type": record.type.value,
        "status": record.status.value,
        "rationale": record.rationale,
        "signal": record.signal,
        "source_refs": list(record.source_refs),
        "supersedes": record.supersedes,
        "superseded_by": record.superseded_by,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "path": document.path,
    }


def _suggestion_document_payload(document: SuggestionDocument) -> dict[str, JsonValue]:
    return {
        **cast("dict[str, JsonValue]", document.record.model_dump(mode="json")),
        "body": document.body,
        "path": document.path,
    }


def _require_suggestion_yes(root: Path, *, suggestion_id: str, yes: bool) -> None:
    if yes:
        return
    context_id = load_context_config(root).id
    error = CliDiagnostic(
        code="SUGGESTION_APPROVAL_REQUIRED",
        message="Suggestion adoption and rejection require explicit --yes approval.",
        path="$.yes",
    )
    record_failure(
        result={"suggestion_id": suggestion_id},
        context_id=context_id,
        errors=[error],
    )
    raise UsageConfigurationError(error.message)


def _complete_suggestion_mutation(
    mutation: SuggestionMutationResult,
    *,
    json_output: bool,
) -> None:
    receipt = mutation.receipt
    result: dict[str, JsonValue] = {
        "operation": mutation.operation,
        "suggestion": _suggestion_document_payload(mutation.suggestion),
        "receipt": cast("dict[str, JsonValue]", receipt.model_dump(mode="json")),
    }
    warnings: list[CliDiagnostic] = []
    if receipt.projection.state.value == "stale":
        warnings.append(
            CliDiagnostic(
                code=receipt.projection.diagnostic_code or "TXN-PROJECTION-STALE",
                message="The suggestion committed but its derived projection is stale.",
            )
        )
    if json_output:
        emit_success(result=result, context_id=receipt.context_id, warnings=warnings)
    else:
        output_console.print(
            Text(
                f"{mutation.operation.title()} {mutation.suggestion.record.id}; "
                f"ledger event {receipt.ledger_event_id}."
            )
        )


def _usage_candidate_payload(candidate: UsageCandidate) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", candidate.model_dump(mode="json"))


def _require_usage_yes(root: Path, *, yes: bool) -> None:
    if yes:
        return
    context_id = load_context_config(root).id
    error = CliDiagnostic(
        code="USAGE_APPROVAL_REQUIRED",
        message="Usage suggestion creation requires explicit --yes approval.",
        path="$.yes",
    )
    record_failure(
        result={"operation": "suggest"},
        context_id=context_id,
        errors=[error],
    )
    raise UsageConfigurationError(error.message)


def _usage_suggestion_payload(outcome: UsageSuggestionResult) -> dict[str, JsonValue]:
    created: list[dict[str, JsonValue]] = []
    for mutation in outcome.created:
        created.append(
            {
                "suggestion": _suggestion_document_payload(mutation.suggestion),
                "receipt": cast(
                    "dict[str, JsonValue]",
                    mutation.receipt.model_dump(mode="json"),
                ),
            }
        )
    return {
        "candidate_count": len(outcome.candidates),
        "created_count": len(outcome.created),
        "skipped_count": len(outcome.skipped),
        "created": cast(JsonValue, created),
        "skipped": [_usage_candidate_payload(candidate) for candidate in outcome.skipped],
    }


def _agent_clients(selection: str | None, *, allow_all: bool = True) -> tuple[AgentClient, ...]:
    from workctx.adapters.agents import AgentClient

    normalized = "all" if selection is None else selection.strip().casefold()
    if normalized == "all":
        if allow_all:
            return tuple(AgentClient)
        raise UserCorrectableError("Select one agent for this command, not all.")
    try:
        return (AgentClient(normalized),)
    except ValueError as exc:
        raise UserCorrectableError("Agent must be codex, claude, gemini, or all.") from exc


def _client_capability_payload(capability: ClientCapability) -> dict[str, JsonValue]:
    return {
        "client": capability.client.value,
        "availability": capability.availability.value,
        "executable": capability.executable,
        "version": str(capability.version) if capability.version is not None else None,
        "supported_range": str(capability.supported_range),
        "project_markers": list(capability.project_markers),
        "detail": (sanitize_message(capability.detail) if capability.detail is not None else None),
    }


def _feature_status_payload(feature: FeatureStatus) -> dict[str, JsonValue]:
    return {
        "state": feature.state.value,
        "path": feature.path,
        "detail": sanitize_message(feature.detail) if feature.detail is not None else None,
    }


def _adapter_status_payload(status: AdapterStatus) -> dict[str, JsonValue]:
    return {
        "client": status.client.value,
        "state": status.state.value,
        "manifest_path": status.manifest_path,
        "drift": [
            {
                "reason": drift.reason.value,
                "path": drift.path,
                "skill": drift.skill,
                "expected_hash": drift.expected_hash,
                "actual_hash": drift.actual_hash,
                "detail": sanitize_message(drift.detail) if drift.detail is not None else None,
            }
            for drift in status.drift
        ],
        "instruction_bridge": _feature_status_payload(status.instruction_bridge),
        "mcp_configuration": _feature_status_payload(status.mcp_configuration),
        "warnings": [sanitize_message(warning) for warning in status.warnings],
        "repair_blocked": status.repair_blocked,
        "merge_candidates": [
            _managed_file_merge_payload(candidate) for candidate in status.merge_candidates
        ],
    }


def _adapter_plan_payload(plan: AdapterPlan) -> dict[str, JsonValue]:
    return {
        "client": plan.client.value,
        "action": plan.action.value,
        "plan_hash": plan.plan_hash,
        "source_fingerprint": plan.source_fingerprint,
        "blocked_reason": (
            sanitize_message(plan.blocked_reason) if plan.blocked_reason is not None else None
        ),
        "requires_approval": plan.requires_approval,
        "no_op": plan.is_noop,
        "adopts_trust": plan.adopts_trust,
        "merge_candidates": [
            _managed_file_merge_payload(candidate) for candidate in plan.merge_candidates
        ],
        "changes": [
            {
                "path": change.path,
                "operation": change.operation.value,
                "observed_hash": change.observed_hash,
                "desired_hash": change.desired_hash,
                "requires_approval": change.requires_approval,
                "reason": sanitize_message(change.reason) if change.reason is not None else None,
            }
            for change in plan.changes
        ],
    }


def _managed_file_merge_payload(candidate: ManagedFileMerge) -> dict[str, JsonValue]:
    return {
        "path": candidate.path,
        "recorded_at_adoption_hash": candidate.recorded_at_adoption_hash,
        "packaged_now_hash": candidate.packaged_now_hash,
        "local_hash": candidate.local_hash,
    }


def _agent_plan_approvals(plan: AdapterPlan) -> tuple[TargetApproval, ...]:
    from workctx.adapters.agents import TargetApproval

    approvals: list[TargetApproval] = []
    for change in plan.changes:
        if not change.requires_approval:
            continue
        if change.observed_hash is None:
            raise UserCorrectableError(
                "Agent install plan requires approval without an observed content hash."
            )
        approvals.append(
            TargetApproval(
                path=change.path,
                operation=change.operation,
                observed_hash=change.observed_hash,
                desired_hash=change.desired_hash,
            )
        )
    return tuple(approvals)


def _agent_operation_payload(operation: OperationResult) -> dict[str, JsonValue]:
    return {
        "client": operation.client.value,
        "action": operation.action.value,
        "changed_paths": list(operation.changed_paths),
        "backups": list(operation.backups),
        "no_op": operation.no_op,
    }


def _fleet_refresh_payload(batch: FleetRefreshResult) -> dict[str, JsonValue]:
    contexts = [_fleet_context_payload(context) for context in batch.contexts]
    summary: dict[str, JsonValue] = {
        "refreshed_clients": sum(len(context.refreshed_clients) for context in batch.contexts),
        "preserved_edits": sum(len(context.preserved_edits) for context in batch.contexts),
        "merge_pending": sum(len(context.merge_candidates) for context in batch.contexts),
        "skipped_contexts": sum(context.skip is not None for context in batch.contexts),
        "skipped_clients": sum(len(context.skipped_clients) for context in batch.contexts),
        "failed_contexts": sum(bool(context.failures) for context in batch.contexts),
        "failed_operations": batch.failure_count,
    }
    return {
        "apply_requested": batch.apply_requested,
        "selected_clients": [client.value for client in batch.selected_clients],
        "count": len(contexts),
        "contexts": cast(JsonValue, contexts),
        "summary": summary,
    }


def _fleet_context_payload(context: FleetContextResult) -> dict[str, JsonValue]:
    skip_reason: JsonValue = None
    if context.skip is not None:
        skip_reason = {
            "code": context.skip.code.value,
            "message": sanitize_message(context.skip.message),
        }
    preserved_edits = [
        {
            "client": item.client.value,
            "path": item.change.path,
            "reason": (
                sanitize_message(item.change.reason) if item.change.reason is not None else None
            ),
        }
        for item in context.preserved_edits
    ]
    merge_candidates = [
        {
            "client": item.client.value,
            **_managed_file_merge_payload(item.candidate),
        }
        for item in context.merge_candidates
    ]
    return {
        "context_id": context.context_id,
        "root": str(context.root),
        "configured_context_id": context.configured_context_id,
        "clients": [client.value for client in context.clients],
        "skipped_clients": [client.value for client in context.skipped_clients],
        "application_state": context.application_state.value,
        "plans": cast(
            JsonValue,
            [_fleet_client_result_payload(result) for result in context.client_results],
        ),
        "refreshed": [client.value for client in context.refreshed_clients],
        "preserved_edits": cast(JsonValue, preserved_edits),
        "merge_candidates": cast(JsonValue, merge_candidates),
        "skip_reason": skip_reason,
        "failures": cast(
            JsonValue,
            [_fleet_failure_payload(failure) for failure in context.failures],
        ),
    }


def _fleet_client_result_payload(result: FleetClientResult) -> dict[str, JsonValue]:
    plan: JsonValue = None
    if result.plan is not None:
        plan = _adapter_plan_payload(result.plan)
    receipt: JsonValue = None
    if result.receipt is not None:
        receipt = _agent_operation_payload(result.receipt)
    failure: JsonValue = None
    if result.failure is not None:
        failure = _fleet_failure_payload(result.failure)
    return {
        "client": result.client.value,
        "application_state": result.application_state.value,
        "applied": result.receipt is not None,
        "plan": plan,
        "receipt": receipt,
        "failure": failure,
    }


def _fleet_failure_payload(failure: FleetFailure) -> dict[str, JsonValue]:
    return {
        "stage": failure.stage.value,
        "client": failure.client.value if failure.client is not None else None,
        "reason": _fleet_failure_reason(failure),
    }


def _fleet_failure_reason(failure: FleetFailure) -> str:
    if isinstance(failure.error, InvalidContextError):
        return "Context configuration is invalid."
    if isinstance(failure.error, (WorkctxError, OSError)):
        return sanitize_message(failure.error)
    stage = failure.stage.value.replace("_", " ")
    return f"Unexpected agent refresh {stage} failure."


def _fleet_refresh_warnings(batch: FleetRefreshResult) -> tuple[CliDiagnostic, ...]:
    warnings: list[CliDiagnostic] = []
    skip_codes = {
        "context_root_missing": "AGENT_REFRESH_CONTEXT_MISSING",
        "context_id_mismatch": "AGENT_REFRESH_CONTEXT_ID_MISMATCH",
    }
    for context in batch.contexts:
        if context.skip is not None and context.skip.code.value in skip_codes:
            warnings.append(
                CliDiagnostic(
                    code=skip_codes[context.skip.code.value],
                    message=sanitize_message(context.skip.message),
                    path=str(context.root),
                )
            )
        warnings.extend(
            CliDiagnostic(
                code="AGENT_CLIENT_UNAVAILABLE",
                message=(f"The {client.value} client is not available on this machine; skipped."),
                path=str(context.root),
            )
            for client in context.skipped_clients
        )
    return tuple(warnings)


def _fleet_refresh_errors(batch: FleetRefreshResult) -> tuple[CliDiagnostic, ...]:
    error_codes = {
        "context_validation": "AGENT_REFRESH_CONTEXT_FAILED",
        "detect": "AGENT_REFRESH_DETECTION_FAILED",
        "plan": "AGENT_REFRESH_PLAN_FAILED",
        "apply": "AGENT_REFRESH_APPLY_FAILED",
    }
    errors: list[CliDiagnostic] = []
    for context_index, context in enumerate(batch.contexts):
        for failure_index, failure in enumerate(context.failures):
            client = f" for {failure.client.value}" if failure.client is not None else ""
            stage = failure.stage.value.replace("_", " ")
            errors.append(
                CliDiagnostic(
                    code=error_codes[failure.stage.value],
                    message=sanitize_message(
                        f"Context '{context.context_id}' {stage} failed{client}: "
                        f"{_fleet_failure_reason(failure)}"
                    ),
                    path=f"$.contexts[{context_index}].failures[{failure_index}]",
                )
            )
    return tuple(errors)


def _opened_context_payload(opened: OpenedContext) -> dict[str, JsonValue]:
    return {
        "client": opened.client.value,
        "root": str(opened.root),
        "executable": opened.executable,
        "pid": opened.pid,
    }


def _render_agent_plan(plan: AdapterPlan) -> None:
    output_console.print(
        Text(f"{plan.client.value}: {len(plan.changes)} planned changes ({plan.plan_hash}).")
    )
    for change in plan.changes:
        output_console.print(Text(f"  {change.operation.value}: {change.path}"))
    if plan.adopts_trust:
        output_console.print(Text("  trust: adopt exact untracked state; project files unchanged"))
    for candidate in plan.merge_candidates:
        output_console.print(
            Text(
                "  merge: "
                f"{candidate.path}; recorded-at-adoption="
                f"{candidate.recorded_at_adoption_hash}; packaged-now="
                f"{candidate.packaged_now_hash}; local={candidate.local_hash}"
            )
        )


def _render_fleet_diagnostics(
    warnings: tuple[CliDiagnostic, ...],
    errors: tuple[CliDiagnostic, ...],
) -> None:
    for warning in warnings:
        location = f" ({warning.path})" if warning.path is not None else ""
        output_console.print(Text(f"Warning: {warning.message}{location}"))
    for error in errors:
        output_console.print(Text(f"Failed: {error.message}"))


def _render_fleet_refresh(batch: FleetRefreshResult) -> None:
    mode = "apply" if batch.apply_requested else "preview"
    table = Table(
        title=f"Agent fleet refresh ({mode})",
        show_lines=True,
        padding=(0, 0),
    )
    table.add_column("Context")
    table.add_column("Clients")
    table.add_column("Refreshed")
    table.add_column("Preserved edits")
    table.add_column("Merge pending")
    table.add_column("Skipped")
    table.add_column("Failed")
    for context in batch.contexts:
        clients = ", ".join(client.value for client in context.clients) or "-"
        refreshed = ", ".join(client.value for client in context.refreshed_clients) or "-"
        if context.skip is not None:
            skipped = context.skip.code.value
        else:
            skipped = ", ".join(client.value for client in context.skipped_clients) or "-"
        failed = (
            ", ".join(
                (
                    f"{failure.client.value}/{failure.stage.value}"
                    if failure.client is not None
                    else failure.stage.value
                )
                for failure in context.failures
            )
            or "-"
        )
        table.add_row(
            context.context_id,
            clients,
            refreshed,
            str(len(context.preserved_edits)),
            str(len(context.merge_candidates)),
            skipped,
            failed,
        )
    output_console.print(table)


def _render_agent_status(status: AdapterStatus) -> None:
    output_console.print(Text(f"{status.client.value}: {status.state.value}"))
    for candidate in status.merge_candidates:
        output_console.print(
            Text(
                "  merge: "
                f"{candidate.path}; recorded-at-adoption="
                f"{candidate.recorded_at_adoption_hash}; packaged-now="
                f"{candidate.packaged_now_hash}; local={candidate.local_hash}"
            )
        )


def _render_doctor(checks: list[DoctorCheck]) -> None:
    table = Table(title="Work Context OS doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for check in checks:
        table.add_row(check.name, check.status, check.detail)
    output_console.print(table)


def _serialize_check(check: DoctorCheck) -> dict[str, JsonValue]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": sanitize_message(check.detail),
        "required": check.required,
    }


def _serialize_issue(issue: ValidationIssue) -> dict[str, str | None]:
    return {
        "severity": issue.severity.value,
        "code": issue.code,
        "message": _safe_issue_message(issue),
        "path": sanitize_message(issue.path, fallback="") if issue.path is not None else None,
        "repair_action": sanitize_message(issue.repair_action, fallback="") or None,
    }


def _diagnostic_from_issue(issue: ValidationIssue) -> CliDiagnostic:
    return CliDiagnostic(
        code=issue.code,
        message=_safe_issue_message(issue),
        path=sanitize_message(issue.path, fallback="") if issue.path is not None else None,
        repair_action=sanitize_message(issue.repair_action, fallback="") or None,
    )


def _safe_issue_message(issue: ValidationIssue) -> str:
    if issue.code == "CTX-CONFIG":
        return "Context configuration is invalid."
    return sanitize_message(issue.message)


def _render_validation(report: ValidationReport, issues: list[dict[str, str | None]]) -> None:
    if not issues:
        output_console.print(
            Text.assemble(
                ("Valid context: ", "green"),
                report.context_id or report.context_root.name,
                f" ({report.context_root})",
            )
        )
        return

    table = Table(title=f"Context validation: {report.context_id or report.context_root.name}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Path")
    table.add_column("Message")
    table.add_column("Repair")
    for issue in issues:
        table.add_row(
            issue["severity"] or "",
            issue["code"] or "",
            Text(issue["path"] or ""),
            Text(issue["message"] or ""),
            Text(issue["repair_action"] or ""),
        )
    output_console.print(table)
