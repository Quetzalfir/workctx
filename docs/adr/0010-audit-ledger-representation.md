# ADR 0010: Audit ledger representation and tamper evidence

- Status: accepted
- Date: 2026-07-30

## Context

The architecture plan classifies the audit ledger as canonical data and lists its
representation and tamper-evidence level as a decision to confirm early (open decision
D-019). ADR 0006's intent journal references transaction IDs the ledger must record, and
the security plan relies on comparing canonical Git history as an audit-tampering control.
The context template currently gitignores everything under `98_state/`, so placing the
ledger there would silently lose the Git backstop. WP-300 (transaction engine) cannot be
contracted until this is decided.

## Decision

- The audit ledger is canonical and lives at `99_meta/audit/ledger.jsonl` inside the
  context root — a zone that is Git-tracked in context workspaces, requiring no change to
  the REQUIRED_DIRECTORIES contract.
- Format: JSON Lines, append-only, one event per line, validated by
  `schemas/audit-event.schema.json` (semantic tightening lands with WP-300 as contracted).
- Tamper evidence: hash chain. Each event carries `prev_hash` (the previous event's
  `event_hash`, or 64 zero hex chars for the first event) and `event_hash` = SHA-256 over
  the canonical serialization (ADR 0005 rules applied to JSON) of the event with its
  `event_hash` field empty. Verification replays the chain.
- Git history over the tracked ledger file is the second, independent tamper control
  (08-security-and-privacy.md); the hash chain works even in non-Git workspaces.
- The SQLite projection may mirror audit events for querying, but the mirror is
  rebuildable and never authoritative (ADR 0001; the plan forbids relying solely on
  mutable SQLite audit rows).
- Appends happen inside the context write lock after the `os.replace` sequence, as the
  final step before removing the intent record (ADR 0006).
- Rotation is deferred: a single `ledger.jsonl` until size becomes a measured problem;
  a rotation design must preserve the chain across files and gets its own ADR revision.
- Signing/notarization is out of scope for Phase 1.

## Consequences

- WP-300 contracts can now specify exact ledger semantics; `event_hash`/`prev_hash`
  fields must be added to `schemas/audit-event.schema.json` by WP-300 with ADR 0008
  fixtures.
- Ledger writes are human-inspectable, diffable, and survive projection deletion.
- An attacker with filesystem access can rewrite the whole chain; Git history and
  (later) external anchoring mitigate — documented residual risk, consistent with the
  Phase 1 threat model.
- Evidence-preservation policies (raw evidence retention) are unaffected: the ledger
  records operations, not evidence content, and must not embed secret values (NFR-006).
