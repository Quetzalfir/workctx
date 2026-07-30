# Contributing

Thank you for helping build Work Context OS.

## Before starting

1. Read `AGENTS.md`.
2. Search existing issues, ADRs, and work orders.
3. For substantial work, create or request a bounded work order.
4. Do not include proprietary evidence or credentials in examples, tests, or bug reports.

## Development

```powershell
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Optional but recommended: install the Git hooks so lint and formatting run on each commit.

```powershell
uv run pre-commit install
```

## Pull requests

A pull request should include:

- the problem and intended behavior;
- scope and non-goals;
- tests added or changed;
- commands executed and exact results;
- security and migration considerations;
- documentation updates;
- any unresolved assumptions.

Keep changes focused. Architectural changes require an ADR under `docs/adr/`.

## Commit guidance

Use clear imperative commit messages. Do not commit generated local state, credentials, raw private evidence, or agent transcripts containing sensitive data.
