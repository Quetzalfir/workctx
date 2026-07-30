# ADR 0007: Workspace schema migrations

- Status: accepted
- Date: 2026-07-30

## Context

Released workspace schemas require backward-compatible migration support (NFR-008). The
architecture plan asks for an early decision on the migration framework. Canonical data is
plain files; SQLite is a rebuildable projection.

## Decision

- The context declares a single integer `schema_version` in `context.yaml` covering the
  whole workspace layout and entity frontmatter contracts.
- Migrations are forward-only, ordered, pure Python functions registered in a migration
  table inside the core package (`N -> N+1`); no external migration framework.
- `workctx context migrate --dry-run` reports the migration path and affected files without
  writing; applying first acquires the context write lock (ADR 0006), **then** creates a
  timestamped backup archive of canonical files under `98_state/backups/` (backing up
  outside the lock could capture a torn mid-transaction state), then runs steps inside the
  same lock as one transaction with an audit event.
- Downgrade is restore-from-backup, not reverse migration.
- SQLite projections are never migrated in place: a projection schema-version mismatch
  triggers a full rebuild from canonical files, which must remain a supported, tested path
  (ADR 0001).
- Pre-1.0, the workspace schema version may advance with minor releases; every released
  schema version must have a registered migration to the next.
- JSON Schema coupling (ADR 0008): the hand-maintained schemas pin the workspace version
  they describe (e.g. `context.schema.json` `schema_version: const N`). A migration that
  bumps the workspace version updates the affected schemas' version constraints in the same
  change set; `schemas/` always describes the current workspace version only, and historical
  shapes live in Git history plus the migration code.

## Consequences

- one linear version line keeps reasoning and testing simple; per-entity versions are
  avoided until proven necessary;
- migration steps are unit-testable as pure functions over fixture workspaces;
- backups make failed migrations recoverable without Git;
- rebuild-instead-of-migrate keeps SQLite code free of historical schema branches.
