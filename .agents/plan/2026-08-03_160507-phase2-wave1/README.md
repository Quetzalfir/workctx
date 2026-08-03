# Phase 2 — Wave 1 (operator-approved 2026-08-03)

Operator approved the first Phase 2 package set: C-208 + C-203 + C-207 from
`.agents/status/phase2-candidates.md`. Baseline: the `v0.1.0-alpha` release
commit. Two disjoint work packages run in parallel.

## Packages

| Package | Candidates | Scope | Worker |
| --- | --- | --- | --- |
| WP-600 | C-203, C-207 | Two new generated views: resource directory and status report | Codex (max effort) |
| WP-610 | C-208 | Mutation-path performance pass, ceremony counts down, behavior identical | Codex (max effort) |

## Path ownership (disjoint)

- WP-600: `src/workctx/views/**`, `tests/tasks_views/**`,
  `docs/reference/views.md`. Projection and canonical-store APIs consumed
  as-is; query gaps are blockers, not edits.
- WP-610: `src/workctx/adapters/filesystem/lock.py`,
  `src/workctx/adapters/sqlite/projection.py`,
  `src/workctx/transactions/engine.py`, `src/workctx/ingestion/service.py`,
  `tests/perf/**` (new). NOTHING else; public APIs and ADR 0006 semantics
  frozen.

## Wave-close criteria

1. Both packages green on the full gate and the CI matrix.
2. WP-600: `workctx view rebuild` emits the two new views deterministically;
   docs updated; acceptance assertions on content.
3. WP-610: measured ceremony counts drop to the C-208 targets (register
   opens < 100, resolves < 300, one SQLite schema init per operation,
   heartbeat writes <= 6 per apply) with counts asserted deterministically —
   no wall-clock assertions in CI; full 1456-test suite still green,
   zero public-API changes.

## Reserved decisions

- None open. D-018 vocabulary frozen (views read existing entities);
  ADR 0006/0010 semantics untouched by WP-610 (piggyback heartbeats must
  keep the lease within the configured freshness bound — worker must show
  the bound argument in the report).
