# Leader review: `WP-300-transaction-engine`

## Round 2 decision (2026-08-01)

`revision_requested` — blocker accepted; resolved by design decision D-031, no new
cross-package work.

The worker verified correctly that the WP-201 intent record carries no proposal digest,
actor, sources, or condition digests, so an eventless recovery COMPLETE cannot
authenticate what it completes. Lead resolution (D-031): recovery is ledger-event-gated.
The ADR 0010 append — which happens strictly after every replace and before intent
finalize — is the commit point:

- intent + matching verified ledger event => all replaces are necessarily applied =>
  recovery performs cleanup/finalize only (no writes to authenticate);
- intent without a ledger event => the transaction never committed => rollback ONLY,
  from WP-201's hash-verified preimages; a retry goes through the full authenticated
  apply path with fresh validation, actor, and conditions.

Forward-completion of staged replaces never occurs during recovery, eliminating the
authentication, postcondition-bypass, and provenance-misstatement vectors wholesale.

Required corrections (bounded, same branch/worktree):

1. Implement D-031 recovery policy; document it in docs/reference/transactions.md.
2. Reject mismatched transaction_id selectors in active recovery (defect).
3. Recovery provenance: model rollback/cleanup events within YOUR audit-event schema so
   its producer-invariant description stays truthful (you own the schema; fix the
   description, add fixtures).
4. Close the enumerated D-025 preflight traversal omissions (evidence extras, embedded
   observation references/identities, task raw-ID dependencies/blockers, URI blockers,
   body references); where a carrier is only checkable post-apply, document the split
   explicitly in transactions.md.
5. Add the missing focused tests: recovery crash/failure branches per case and
   long-duration heartbeat behavior.
6. Rerun the gate; update report.md/report.json to completed.

Round 2 evidence reviewed: 867 tests passing on the branch including 90 transaction
tests; partial implementation uncommitted at the worker’s discretion.

## Round 1 decision (2026-07-31)

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
