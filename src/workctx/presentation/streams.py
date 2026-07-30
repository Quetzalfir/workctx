from __future__ import annotations

import sys

from rich.console import Console
from rich.text import Text

from workctx.presentation.envelope import CliEnvelope

output_console = Console()
error_console = Console(stderr=True)


def write_envelope(envelope: CliEnvelope) -> None:
    """Write exactly one JSON document to stdout."""

    sys.stdout.write(envelope.model_dump_json(indent=2))
    sys.stdout.write("\n")


def write_error(message: str) -> None:
    """Write a plain, non-interpreted diagnostic to stderr."""

    error_console.print(Text.assemble(("Error: ", "bold red"), Text(message)))
