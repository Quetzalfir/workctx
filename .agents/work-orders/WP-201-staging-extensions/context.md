# Work-order context: WP-201-staging-extensions

## Why this exists

WP-300 blocked correctly on six gaps; four of them are missing filesystem primitives
(read `.agents/work-orders/WP-300-transaction-engine/report.md` — required reading).
The transaction-proposal schema's operation vocabulary (`create`, `update`, `move`,
`delete_generated`, `append_audit`) was authored in Wave 1, but WP-200's staging only
implements byte-postimage replaces. This order closes exactly that primitive gap; the
engine semantics stay with WP-300.

## Required architecture and decisions

- ADR 0006: your recovery guarantees for moves/deletes must match the existing replace
  rigor (intent journal, preimages, fencing, bounded retries).
- ADR 0010: the append primitive exists so WP-300 can write the ledger after the
  replace sequence and before intent finalize — do not implement chain semantics, only
  the durable write slot.
- D-024 (decision register): this order is the resolution of WP-300's primitive gaps;
  the sqlite mark-stale gap was closed separately by the lead
  (`SQLiteProjection.invalidate()`), and the staged-overlay validation gap was resolved
  as a WP-300 design decision (D-025) — neither is your scope.

## Existing implementation

- You are extending your own Wave 2 delivery (WP-200): staging.py's IntentRecord/
  IntentTarget/RecoveryInspection and the replace pipeline with preimage retention.
  Reuse its patterns — `tests/filesystem/test_staging.py` is the rigor bar (29 tests).
- `_paths.py` boundary enforcement must cover move destinations and append targets.
- The lock's fence helper is the nonce verification primitive for the append.

## Dependencies

- WP-320 runs in parallel (fully disjoint). WP-300 resumes on your integration and
  composes these primitives immediately — API ergonomics matter; keep the new surface
  small and typed.

## Known risks and edge cases

- intent.json compatibility: existing records have no kind field; choose default-kind
  backward reading OR a version bump with a clear error, and document it.
- Move destination may exist (overwrite semantics?) — refuse by default; WP-300's
  vocabulary can request replace-style moves later if needed.
- Delete of a missing target during recovery must be idempotent-safe.
- Append durability on Windows: fsync the file handle; parent dir fsync is best-effort
  POSIX only (ADR 0006).
- Torn-line prevention: single write call per line, buffer assembled first.
