from __future__ import annotations

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
        OpenedContext,
        OperationResult,
        TargetApproval,
    )
    from workctx.adapters.sqlite import SearchHit, SQLiteProjection, TaskRecord
    from workctx.domain.transactions import AuditEvent, TransactionProposal
    from workctx.ingestion import ArtifactRecord, IngestionService, RegistrationResult
    from workctx.retrieval.records import ResolutionResult
    from workctx.suggestions import SuggestionDocument, SuggestionMutationResult
    from workctx.transactions.models import DryRunResult, TransactionDiagnostic
    from workctx.views import BriefPayload

import typer
from pydantic import JsonValue, ValidationError
from rich.table import Table
from rich.text import Text

from workctx import __version__
from workctx.doctor import DoctorCheck, run_doctor
from workctx.domain import EntityType, TaskStatus
from workctx.errors import (
    UnavailableDependencyError,
    UsageConfigurationError,
    UserCorrectableError,
)
from workctx.models.context import ContextKind, ContextProfile
from workctx.presentation import (
    CliDiagnostic,
    PresentationTyperGroup,
    begin_command,
    emit_success,
    output_console,
    record_failure,
    resolve_cli_context,
    sanitize_message,
)
from workctx.services.contexts import initialize_context, load_context_config
from workctx.suggestions import SuggestionStatus
from workctx.validation.workspace import ValidationIssue, ValidationReport, validate_workspace

app = typer.Typer(
    name="workctx",
    help="Local-first, model-neutral work memory and operations for AI agents.",
    no_args_is_help=True,
    cls=PresentationTyperGroup,
)
context_app = typer.Typer(help="Create, inspect, and validate isolated contexts.")
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
agent_app = typer.Typer(help="Detect, install, inspect, and open supported agent clients.")
app.add_typer(agent_app, name="agent")
migrate_app = typer.Typer(help="Convert legacy Markdown repositories into isolated contexts.")
app.add_typer(migrate_app, name="migrate")
secret_app = typer.Typer(help="Manage machine-global secret references without printing values.")
app.add_typer(secret_app, name="secret")
connector_app = typer.Typer(help="Synchronize declarative external-source connectors.")
app.add_typer(connector_app, name="connector")


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
    name: Annotated[str, typer.Argument(help="Connector manifest name.")],
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
    """Fetch and register snapshots for one declarative connector."""
    begin_command("connector.sync", json_output=json_output)

    from workctx.connectors import sync

    root = resolve_cli_context(explicit_path=context_path)
    context_id = load_context_config(root).id
    synced = sync(root, name, snapshot_id=snapshot)
    result = cast("dict[str, JsonValue]", synced.model_dump(mode="json"))
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        for item in synced.snapshots:
            output_console.print(
                Text(f"{item.snapshot_id}: {item.disposition.value} ({item.byte_count} bytes)")
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
        output_console.print(Text(f"Inspected {len(statuses)} agent adapter statuses."))


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
    from workctx.adapters.agents import AgentAdapterService

    begin_command("agent.install", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path)
    if scope != "project":
        raise UserCorrectableError("Agent adapter installation supports only project scope.")
    clients = _agent_clients(agent)
    service = AgentAdapterService()
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
    }
    if json_output:
        emit_success(result=result, context_id=context_id)
    else:
        for plan in plans:
            _render_agent_plan(plan)
        if yes:
            output_console.print(Text(f"Installed {len(receipts)} agent adapters."))
        else:
            output_console.print(Text("Re-run with --yes to execute this installation plan."))


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
    }


def _diagnostic_from_issue(issue: ValidationIssue) -> CliDiagnostic:
    return CliDiagnostic(
        code=issue.code,
        message=_safe_issue_message(issue),
        path=sanitize_message(issue.path, fallback="") if issue.path is not None else None,
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
    for issue in issues:
        table.add_row(
            issue["severity"] or "",
            issue["code"] or "",
            Text(issue["path"] or ""),
            Text(issue["message"] or ""),
        )
    output_console.print(table)
