# Leader review: `WP-300-transaction-engine`

## Decision

`revision_requested` (blocker accepted; contract amended; resumes after WP-201)

The worker stopped at the contractual stop condition without implementing a partial
engine and without touching any implementation path — exactly right. All six reported
gaps verified real by the lead against the actual APIs and the ops vocabulary already
present in the Wave 1 schema.

## Gap resolutions (D-024, D-025)

1. Fenced ledger append while intent active → WP-201-staging-extensions (new bounded
   order on the filesystem adapter).
2. Audit-directory creation under lock → folded into the WP-201 append primitive
   (in-boundary parent creation).
3. move/delete_generated staging → WP-201 intent-target kinds with full recovery
   coverage.
4. Staged-overlay validation → D-025 design decision: WP-300 composes preconditions
   (domain models + projection checks, in memory); validate_workspace is the
   post-apply postcondition gate; WP-220 stays frozen.
5. Durable mark-stale → lead-added `SQLiteProjection.invalidate()` (tested; ADR 0007
   rebuild-not-migrate path).
6. Frozen loose fixtures → WP-300 contract now explicitly grants the four
   transaction/audit fixture files; the tests/workspace blanket freeze was narrowed
   accordingly.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| gap verification vs staging.py / projection.py / schema ops enum / fixture tests | confirmed | all six real |
| `uv run pytest tests/projections` (invalidate addition) | pass | 43 passed |

## Required next steps

- WP-201 runs now (parallel with WP-320); on its acceptance the lead pins WP-300's
  base to the post-WP-201 master and flips it back to ready; the same worker session
  may resume with its existing context.

## Integration notes

- WP-310/WP-330 remain gated on WP-300 as planned; the wave's critical path grew by
  one bounded order — better than weakening the trust core.
