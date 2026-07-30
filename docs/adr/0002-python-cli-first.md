# ADR 0002: Python CLI-first implementation

- Status: accepted
- Date: 2026-07-30

## Context

Phase 1 needs cross-platform local tooling, schemas, filesystem operations, SQLite, and MCP without a UI dependency.

## Decision

Use Python 3.12+, `uv`, Typer, Rich, and Pydantic. Defer a custom UI to Phase 2.

## Consequences

- installation can target `uv tool install`;
- Windows behavior must be tested explicitly;
- application/domain layers must remain independent so a future UI can reuse them;
- optional integrations should not inflate the minimal runtime dependency set.
