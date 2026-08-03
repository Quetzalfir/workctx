from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

if TYPE_CHECKING:
    from collections.abc import Callable

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
    from workctx.retrieval.records import ResolutionResult
    from workctx.transactions.models import DryRunResult, TransactionDiagnostic

import typer
from pydantic import JsonValue, ValidationError
from rich.table import Table
from rich.text import Text

from workctx import __version__
from workctx.doctor import DoctorCheck, run_doctor
from workctx.domain import EntityType, TaskStatus
from workctx.errors import UnavailableDependencyError, UserCorrectableError
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
from workctx.validation.workspace import ValidationIssue, ValidationReport, validate_workspace

app = typer.Typer(
    name="workctx",
    help="Local-first, model-neutral work memory and operations for AI agents.",
    no_args_is_help=True,
    cls=PresentationTyperGroup,
)
context_app = typer.Typer(help="Create, inspect, and validate isolated contexts.")
app.add_typer(context_app, name="context")
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
agent_app = typer.Typer(help="Detect, install, inspect, and open supported agent clients.")
app.add_typer(agent_app, name="agent")


@app.command()
def version() -> None:
    """Print the installed Work Context OS version."""
    typer.echo(__version__)


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
