# Phase 2 — Wave 3 (operator-approved 2026-08-03: "continúa con lo que falte")

Operator deferred hands-on secrets testing and asked to continue. Cut:

| Package | Candidates | Scope | Worker |
| --- | --- | --- | --- |
| WP-650 | C-204, C-205, C-206, C-202 (detection half) | Four generated views: people-directory, glossary, agenda, suggestions | Codex (max) |
| WP-660 | WP-610 leftovers | Batch inbox add + staging resolution ceremony cuts | Codex (max) |

Deliberately NOT cut yet: C-202 adoption machinery (skill overrides surviving
upgrades) and C-212 usage telemetry — both need a design pass first; the
suggestions VIEW ships now because it only reads existing signals.

## Path ownership (disjoint)

- WP-650: `src/workctx/views/**`, `tests/tasks_views/**`,
  `docs/reference/views.md`.
- WP-660: `src/workctx/ingestion/service.py`, `src/workctx/cli.py` (inbox add
  section only), `src/workctx/adapters/filesystem/staging.py`,
  `tests/perf/**`, `tests/ingestion/**`, `tests/cli/test_inbox_cli.py`,
  `docs/reference/inbox.md`.

## Wave-close criteria

1. Full gate + matrix green with both integrated.
2. WP-650: `view rebuild` emits nine views deterministically; the migration
   view-count test keeps passing (it counts the enum); suggestions view names
   only signals derivable from canonical/audit state, no telemetry.
3. WP-660: batch `inbox add` with N files acquires the lock once and
   refreshes the projection once; ceremony targets tightened accordingly in
   tests/perf; ADR 0006 semantics untouched in staging.py (every removed
   resolution individually justified).

## Notes

- Operator will trial secrets + personalization on the real employer
  instance later; feedback lands in the next cut.
- C-202 adoption + C-212 telemetry: lead drafts a design doc for operator
  review during this wave.
