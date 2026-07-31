# Worker report: `WP-210-sqlite-projections`

## Status

`completed`

## Summary

Implemented the context-bound SQLite/FTS5 projection adapter, full canonical rebuild and
atomic replacement path, projection compatibility checks, typed query records and APIs,
strict context/path isolation, and complete reference documentation. The final repository
gate passes with 392 tests, including 41 projection tests covering equivalence, denial,
concurrency, malformed input, FTS behavior, schema damage, and sidecar/path races.

## Base and final commits

- Base: `cf9ebabf10d565bb0eef0d8f686ebada3cdd34ab`
- Implementation: `c23db02992b4e31eceb5dc8f6f1eaca2eb0e1de7`

## Files changed

- `docs/reference/projections.md`
- `src/workctx/adapters/sqlite/__init__.py`
- `src/workctx/adapters/sqlite/models.py`
- `src/workctx/adapters/sqlite/projection.py`
- `src/workctx/adapters/sqlite/schema.py`
- `tests/projections/__init__.py`
- `tests/projections/support.py`
- `tests/projections/test_isolation_and_swap.py`
- `tests/projections/test_queries.py`
- `tests/projections/test_rebuild.py`
- `.agents/work-orders/WP-210-sqlite-projections/report.md`
- `.agents/work-orders/WP-210-sqlite-projections/report.json`

## Behavior implemented

- Added projection metadata and a versioned schema for entities, ordered aliases and tags,
  typed edges, derived backlinks, observations and derivations, temporal claims and
  supersession, tasks and ordered task fields, and external-content FTS5.
- Added explicit and readiness-triggered full rebuilds for missing, incompatible,
  context-mismatched, projection-version-mismatched, and workspace-version-mismatched
  databases. Required schema objects and FTS usability are checked before a projection is
  treated as ready; tables are never migrated in place.
- Scans only `02_knowledge/**` and `03_work/**` in deterministic order, parses Markdown only
  through `workctx.domain.frontmatter`, validates through the integrated domain models, and
  reports sanitized document-level skips without aborting valid indexing.
- Builds into a unique sibling database, validates FTS and SQLite integrity, flushes and
  closes the build, waits for adapter readers, safely handles live SQLite sidecars, and uses
  bounded `os.replace` retry while preserving the prior database on failure.
- Added one-context typed reads for entity/alias lookup, single-snapshot discriminated
  document lookup, outbound/inbound edges, observations by ID or parent, claims by subject
  and status, task filters, metadata, and ranked literal FTS search.
- Normalizes modeled timestamps to UTC, preserves typed locator JSON and nested JSON claim
  values, round-trips percent-encoded observation IDs, and aligns query token boundaries with
  the documented `unicode61 remove_diacritics 2` tokenizer.
- Enforces fail-closed row context guards, local structured-reference context checks,
  canonical-zone containment, state/database/config revalidation, and non-following sidecar
  checks. Escaped freshly-created temporary files are identity-checked and removed.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | Passed | `All checks passed!` |
| `uv run ruff format --check .` | Passed | `220 files already formatted` |
| `uv run mypy src` | Passed | `Success: no issues found in 35 source files` |
| `uv run pytest` | Passed | `392 passed in 24.56s` |
| `git diff --cached --check` | Passed | Exit code 0; no whitespace errors before the implementation commit. |

## Assumptions and decisions

- Projection schema version 1 is the first adapter schema; future mismatches require a full
  rebuild under ADR 0007.
- `98_state/index.sqlite3` is derived state. Canonical Markdown/YAML remains the only source
  of truth, and rebuild never modifies canonical files.
- SQLite `DELETE` journal mode keeps the built replacement self-contained. Any destination
  WAL is checkpointed only when it is safe; busy, orphaned, malformed, or symbolic-link
  sidecars refuse the swap.
- The adapter uses a process-local reader/swap gate. Cross-process writer serialization is
  intentionally left to the WP-200/WP-300 integration boundary.
- Incremental freshness policy remains deferred to WP-300 as specified by the work order;
  this delivery provides explicit rebuild and compatibility-triggered rebuild behavior.

## Contract deviations

- No implementation scope or path deviation.
- The frozen contract on the assigned base still records `status: proposed` and
  `base_commit: PENDING-WAVE2-BASELINE`. The direct worker assignment identified the branch
  and worktree, and the existing Wave 2 head was
  `cf9ebabf10d565bb0eef0d8f686ebada3cdd34ab`; frozen contract files were not edited.

## Security and migration considerations

- Context rows, structured local references, query inputs, canonical zones, configuration,
  state, database, temporary files, and SQLite sidecars are constrained to the bound context.
  Two-context denial, foreign nested-reference, internal cross-zone symlink, state/config
  replacement, sidecar symlink, and temporary-creation race cases are tested.
- Canonical documents are untrusted input. Invalid UTF-8, frontmatter, domain payloads,
  duplicate identities, foreign references, machine-local references, and invalid task
  hierarchy are skipped with stable diagnostics that do not echo source content.
- Missing FTS5 raises the typed `Fts5UnavailableError` and leaves no partial projection.
- Projection and workspace schema mismatches always rebuild from canonical state; no
  projection migration mutates old tables in place.

## Unresolved issues

- Cross-process rebuild serialization and canonical-write coordination must be supplied by
  the later filesystem/transaction integration. The adapter revalidates immediately before
  sensitive path operations and cleans a detected escaped temporary file by exact identity,
  but a portable path API alone cannot eliminate every hostile concurrent rename interval.
- Incremental projection freshness after canonical mutations remains WP-300 scope.

## Recommended next action

The implementation lead should inspect commit
`c23db02992b4e31eceb5dc8f6f1eaca2eb0e1de7`, validate these reports, rerun the four-command
gate, and integrate WP-210 before opening WP-230 retrieval work.
