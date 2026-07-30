# Worker assignment: `WP-210-sqlite-projections`

You are the worker assigned to `WP-210-sqlite-projections` in the Work Context OS
repository. You are working in the Git worktree `.worktrees/WP-210-sqlite-projections`
on branch `agent/WP-210-sqlite-projections`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-210-sqlite-projections/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`.
5. Parse canonical documents through `workctx.domain.frontmatter` and the integrated
   domain models only; never write a second parser and never modify domain code.
6. stdlib `sqlite3` only — no new dependencies. All SQL stays inside your adapter;
   callers receive typed functions.
7. Your only `98_state/` files are `index.sqlite3` and its wal/shm/temporary siblings;
   WP-200 (parallel) owns `lock.json`, `staging/**`, `backups/`. Your rebuild is
   read-only over canonical files.
8. No CLI or presentation changes.
9. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
10. Add tests for every behavior including the denial and rebuild-equivalence cases; run
    every validation command. A blocker is a valid result.
11. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and results.
    A completion claim without executed command evidence will be rejected.

## Objective

Build the SQLite/FTS projection adapter (`src/workctx/adapters/sqlite/`): schema with
projection metadata and ADR 0007 version-mismatch full rebuild, indexing of entities,
typed edges, derived backlinks, observations, claims, tasks, and aliases, FTS5 search,
temp-build-and-swap rebuild from canonical files with skip-and-report on malformed
documents, typed query APIs for later packages, and strict context isolation.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
