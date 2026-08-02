from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from workctx.adapters.sqlite import SQLiteProjection
    from workctx.retrieval.records import ResolutionResult

import typer
from pydantic import JsonValue, ValidationError
from rich.table import Table
from rich.text import Text

from workctx import __version__
from workctx.doctor import DoctorCheck, run_doctor
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
