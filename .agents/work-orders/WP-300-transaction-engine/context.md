# Work-order context: WP-300-transaction-engine

## Why this exists

Every product workflow (ingestion, tasks, drafting) mutates canonical state through this
engine — it is the mechanism that makes multi-entity updates transactional (product
invariant) and auditable. WP-310 and WP-330 are blocked on your APIs.

## Required architecture and decisions

- ADR 0006 is your apply-sequence specification: lock with nonce identity, fence before
  the first replace AND before the audit append, write-ahead intent, bounded retries.
  The WP-200 store already implements the primitives and its staging API deliberately
  keeps the intent record until an explicit post-audit finalize — that hook exists for
  YOU (see `docs/reference/canonical-store.md` and `tests/filesystem/test_staging.py`).
- ADR 0010: ledger at `99_meta/audit/ledger.jsonl`, hash chain over ADR 0005 canonical
  JSON serialization, Git-tracked zone. You add `event_hash`/`prev_hash` to the
  audit-event schema (WP-110 deferred its tightening to you explicitly).
- ADR 0011: budget-free here, but hash/arithmetic relations in your schemas follow the
  two-tier fixture rule.
- 02-architecture.md transaction model section lists the ten-step apply algorithm.

## Existing implementation

- WP-200 primitives (integrated): `ContextLock` (acquire/heartbeat/fence/release),
  staged replacement with intent journal and recovery inspection, canonical serializer
  with hand-edit detection, path-boundary enforcement. Consume through their public
  APIs; read their tests to learn intended composition.
- WP-220 engine: `validate_workspace(root, strict=..., freshness_probe=...)` for
  precondition-grade checks; diagnostic codes documented.
- WP-210 projection: `SQLiteProjection(root)` with `rebuild()`, `readiness_trigger()`,
  typed queries — your post-commit staleness handling uses these.
- `schemas/transaction-proposal.schema.json` and `schemas/audit-event.schema.json` are
  deliberately loose (bare objects in places) — Wave 1 deferred semantics to you.
- TXP id grammar already sketched in the schema (`TXP-YYYYMMDDTHHMMSSZ-slug`).

## Dependencies

- WP-200/WP-210/WP-220 integrated on your base. WP-320 runs in parallel and is fully
  disjoint (agent installers). WP-310/WP-330 start after you integrate and consume your
  APIs — your `apply` result object is their interface: include the committed revision,
  applied targets, ledger event id/hash, and projection status in it.

## Known risks and edge cases

- Fencing interplay: a takeover between your fence check and a replace is detectable
  only via the intent journal — reproduce the WP-200 staging test scenario at the
  transaction level.
- Ledger append is itself a file write: append inside the lock via the WP-200 staging
  primitive or an fsynced append with the same durability care; document the choice.
  Chain state (last hash) must be re-read under the lock, never cached across locks.
- Windows: ledger appends can hit sharing violations like any file op — bounded retry.
- The context revision token: simplest sound choice is the ledger head hash (documented
  in ADR 0010's consequences spirit) — if you choose differently, justify in the doc.
- `99_meta/audit/` does not exist in the template; create it on first append (the
  template stays untouched — it is WP-110-owned; record this in your report).
- New test directory `tests/transactions/` needs an `__init__.py`.
