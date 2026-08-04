# Brief: WP-660 — Batch registration and staging ceremony cuts

Codex worker, worktree `.worktrees/WP-660`, branch `agent/WP-660-perf-leftovers`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-208 and the WP-610 integration notes in
`.agents/status/` first. WP-610's measured leftovers are your whole scope:

1. Multi-file `inbox add` pays full ceremony per file (lock, projection,
   heartbeats) because `src/workctx/cli.py` loops over `service.register()`.
2. ~187 of 294 high-level path resolutions per operation originate in
   `src/workctx/adapters/filesystem/staging.py`.

## Scope

1. Batch registration API on `IngestionService` (`register_batch` or an
   equivalent shape you justify): one lock acquisition, one heartbeat
   refresher span, one projection refresh for N files; per-file outcomes
   preserved EXACTLY as today (registered/duplicate/quarantined are
   per-file results, never batch-fatal; a hard failure on file K must leave
   files 1..K-1 committed and report the remainder as not-attempted).
   Single-file register keeps its current public signature and behavior.
2. `inbox add` CLI switches to the batch API; envelope shape unchanged
   (same per-file outcomes array — existing CLI tests must pass untouched
   except where they assert internal call counts, and touching them needs a
   stated reason).
3. staging.py resolution cuts: cache resolved roots/paths within one
   prepare/apply cycle where the lock is held; NEVER weaken symlink/junction/
   boundary rejection — every skipped re-resolution needs an individual
   justification in your report tied to what the earlier resolution already
   proved and why it cannot have changed under the held lock.
4. Tighten tests/perf ceremony targets to the new reality (batch of 3 files:
   lock acquisitions == 1, projection refreshes == 1, with 2x headroom on
   counts as before; no wall-clock).

## Do NOT touch

Anything outside: `src/workctx/ingestion/service.py`, `src/workctx/cli.py`
(inbox add section only), `src/workctx/adapters/filesystem/staging.py`,
`tests/perf/**`, `tests/ingestion/**`, `tests/cli/test_inbox_cli.py`,
`docs/reference/inbox.md`. Zero public-API breaks (additive API only). ADR
0006 durability semantics frozen: no fsync removal without individual
justification; boundary checks stay effective.

## Tests required

Batch semantics (mixed outcomes, mid-batch hard failure leaves prefix
committed + remainder reported), single-file path unchanged, ceremony counts
for the batch, staging boundary regressions still green (symlink/junction/
traversal refusals). Full gate; declare sandbox limitations explicitly.
