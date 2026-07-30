from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast

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


@app.command()
def version() -> None:
    """Print the installed Work Context OS version."""
    typer.echo(__version__)


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
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Validate the initial workspace structure and safety checks."""
    _validate_context(
        path=path,
        context_path=context_path,
        json_output=json_output,
    )


@app.command("validate")
def validate_alias(
    path: Annotated[Path | None, typer.Argument(help="Context root or path inside it.")] = None,
    context_path: Annotated[
        Path | None,
        typer.Option("--context", help="Explicit context path; overrides path discovery."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Alias for `workctx context validate`."""
    _validate_context(
        path=path,
        context_path=context_path,
        json_output=json_output,
    )


def _validate_context(*, path: Path | None, context_path: Path | None, json_output: bool) -> None:
    begin_command("context.validate", json_output=json_output)
    root = resolve_cli_context(explicit_path=context_path, positional_path=path)
    report = validate_workspace(root)
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
