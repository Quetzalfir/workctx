# Acceptance criteria: WP-300-transaction-engine

## Functional

- [ ] Typed proposal models aligned with the tightened transaction-proposal schema
      (ADR 0008 fixtures, ADR 0011 tiers).
- [ ] validate_proposal, dry_run, apply, audit_summary, verify_ledger APIs.
- [ ] Apply composes WP-200 primitives per the ADR 0006 sequence with fence checks and
      post-audit intent finalize.
- [ ] Hash-chained ledger at 99_meta/audit/ledger.jsonl; chain verification API.
- [ ] Post-commit projection refresh with stale-not-erased semantics on failure.
- [ ] Context revision token defined, documented, and enforced by preconditions.

## Negative and edge cases

- [ ] Invalid proposal → canonical tree byte-identical (hash-compared).
- [ ] Stale base revision → conflict; duplicate proposal id → conflict.
- [ ] Injected mid-sequence failure → intent detected; recovery completes or rolls
      back; ledger consistent.
- [ ] Takeover mid-sequence at transaction level → old holder aborts, successor
      recovery works.
- [ ] Tampered middle ledger event → verification fails; first event uses zero
      prev_hash.
- [ ] Secret-looking proposal payload refused with location-only diagnostic.
- [ ] Projection rebuild failure after commit → transaction preserved, stale reported
      with repair instructions.

## Quality

- [ ] No direct canonical writes outside WP-200 primitives; frozen paths untouched.
- [ ] docs/reference/transactions.md documents proposal lifecycle, revision token,
      ledger format, and recovery.
- [ ] Fictional fixtures only; no new runtime dependencies.

## Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
