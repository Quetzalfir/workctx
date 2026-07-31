# Worker assignment: `WP-300-transaction-engine`

You are the worker assigned to `WP-300-transaction-engine` in the Work Context OS
repository. You are working in the Git worktree `.worktrees/WP-300-transaction-engine`
on branch `agent/WP-300-transaction-engine`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-300-transaction-engine/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` — ADRs 0006 and 0010 are your
   implementation specification; deviations are blockers, not judgment calls.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`. You own exactly one new domain file
   (`src/workctx/domain/transactions.py`) — every other domain file is frozen.
5. All canonical writes go through the WP-200 filesystem primitives inside the context
   lock — no direct file writes. A missing primitive capability is a coordination
   request to the lead, not a license to modify the adapter.
6. Consume WP-220 validation and WP-210 projection through their public APIs only.
7. Your `apply` result is the interface WP-310 and WP-330 will consume: include the
   committed revision, applied targets, ledger event id/hash, and projection status.
8. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
9. Failure-injection tests are contractual (mid-sequence failure, takeover, tampered
   ledger, projection failure after commit). A blocker is a valid result.
10. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and
    results. A completion claim without executed command evidence will be rejected.

## Objective

Build the transaction engine and audit ledger: typed TXP proposal models with the
tightened transaction-proposal and audit-event schemas, deterministic validate/dry-run,
atomic apply composing the WP-200 lock/staging primitives per ADR 0006 (fence checks,
write-ahead intent, post-audit finalize), the ADR 0010 hash-chained ledger at
`99_meta/audit/ledger.jsonl` with verification, idempotency and stale-revision conflict
semantics, and post-commit projection staleness handling that never erases a committed
transaction.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
