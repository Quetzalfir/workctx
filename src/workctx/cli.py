from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from workctx import __version__
from workctx.doctor import run_doctor
from workctx.errors import ContextAlreadyExistsError, ContextNotFoundError, InvalidContextError
from workctx.models.context import ContextKind, ContextProfile
from workctx.services.contexts import initialize_context, load_context_config, resolve_context_root
from workctx.validation.workspace import Severity, validate_workspace

app = typer.Typer(
    name="workctx",
    help="Local-first, model-neutral work memory and operations for AI agents.",
    no_args_is_help=True,
)
context_app = typer.Typer(help="Create, inspect, and validate isolated contexts.")
app.add_typer(context_app, name="context")
console = Console()


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
    checks = run_doctor()
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "ok": not any(check.required and check.status != "ok" for check in checks),
                    "command": "doctor",
                    "result": [asdict(check) for check in checks],
                },
                indent=2,
            )
        )
    else:
        table = Table(title="Work Context OS doctor")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        for check in checks:
            table.add_row(check.name, check.status, check.detail)
        console.print(table)

    if any(check.required and check.status != "ok" for check in checks):
        raise typer.Exit(code=1)


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
) -> None:
    """Create a context from the versioned workspace template."""
    try:
        config = initialize_context(
            path,
            name=name,
            context_id=context_id,
            kind=kind,
            profile=profile,
            user_language=user_language,
            timezone=timezone,
        )
    except (ContextAlreadyExistsError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Created context [bold]{config.id}[/bold] at {path.expanduser().resolve()}")


@context_app.command("inspect")
def context_inspect(
    path: Annotated[Path | None, typer.Argument(help="Path inside a context.")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show resolved context configuration."""
    try:
        root = resolve_context_root(path or Path.cwd())
        config = load_context_config(root)
    except (ContextNotFoundError, InvalidContextError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    payload = {"root": str(root), **config.model_dump(mode="json")}
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        console.print(f"[bold]Context:[/bold] {config.name} ({config.id})")
        console.print(f"[bold]Root:[/bold] {root}")
        console.print(f"[bold]Profile:[/bold] {config.profile.value}")
        console.print(f"[bold]Classification:[/bold] {config.classification.value}")
        console.print(f"[bold]User language:[/bold] {config.languages.user_interaction}")


@context_app.command("validate")
def context_validate(
    path: Annotated[Path | None, typer.Argument(help="Context root or path inside it.")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate the initial workspace structure and safety checks."""
    try:
        root = resolve_context_root(path or Path.cwd())
    except ContextNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    report = validate_workspace(root)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "ok": report.ok,
                    "command": "context.validate",
                    "context_id": report.context_id,
                    "root": str(report.context_root),
                    "issues": [
                        {
                            "severity": issue.severity.value,
                            "code": issue.code,
                            "message": issue.message,
                            "path": issue.path,
                        }
                        for issue in report.issues
                    ],
                },
                indent=2,
            )
        )
    else:
        if not report.issues:
            console.print(f"[green]Valid context:[/green] {report.context_id} ({root})")
        else:
            table = Table(title=f"Context validation: {report.context_id or root.name}")
            table.add_column("Severity")
            table.add_column("Code")
            table.add_column("Path")
            table.add_column("Message")
            for issue in report.issues:
                table.add_row(issue.severity.value, issue.code, issue.path or "", issue.message)
            console.print(table)
    if any(issue.severity is Severity.ERROR for issue in report.issues):
        raise typer.Exit(code=1)


@app.command("validate")
def validate_alias(
    path: Annotated[Path | None, typer.Argument(help="Context root or path inside it.")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Alias for `workctx context validate`."""
    context_validate(path=path, json_output=json_output)
