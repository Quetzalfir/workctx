# Leader review addendum: `WP-300-transaction-engine` — Round 3 (final)

## Decision

`accepted`

(Recorded as an addendum; rounds 1-2 with the blocker resolutions live in
`leader-review.md`.)

## Contract compliance

- Base: merged master `5b2c7f4` via `334b649`; delivery `f5e55ff`, reports `5ff9ce5`;
  branch clean.
- Post-merge path audit: every changed file inside `allowed_paths`, including exactly
  the four granted workspace fixtures and nothing else under tests/workspace.
- Only permitted domain file created (`domain/transactions.py`); no adapter, engine, or
  frozen-file edits.

## Diff review

- D-031 implemented precisely: eventless recovery rolls back preimages with no forward
  completion; a verified ledger event gates cleanup-only finalize; mismatched
  transaction selectors rejected; finalizer replay idempotent; projection failure after
  event-gated cleanup preserves the commit and marks stale via invalidate().
- Ledger (ADR 0010): hash chain with zero-genesis, canonical encoding matched to the
  domain contract, tamper detection on middle events, wrong-head/wrong-hash refusal,
  reused-identity refusal, ambiguous-adapter-error recovery after commit.
- Schemas tightened with ADR 0008/0011 fixtures; the granted loose fixtures updated;
  audit provenance modeled inside the worker-owned schema per round-2 correction 3.
- D-025 preflight extended per round-2 correction 4 (dedicated traversal test module);
  recovery crash windows and heartbeat behavior test modules added per correction 5.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| `uv run ruff check .` / format / mypy | pass | 318 files / 64 source files |
| `uv run pytest` | pass | 938 passed on the branch (~4 min) |

## Findings

- Two blocker rounds produced a materially safer engine than the original contract
  would have: commit-point semantics (D-031) are simpler and stronger than
  intent-metadata authentication.

## Integration notes

- Integrated as the Wave 3 critical-path order; WP-310 and WP-330 pin and release on
  this integration.
