# Worker report: `WP-300-transaction-engine`

## Status

`completed`

## Summary

WP-300 is complete under ADRs 0006, 0010, and decision D-031. The delivery adds the
typed transaction proposal and audit contracts, deterministic validation and dry-run,
an atomic transaction engine built exclusively on public WP-200/WP-201 primitives, an
ADR 0010 hash-chained audit ledger, idempotency and revision conflicts, durable
projection staleness handling, and ledger-event-gated recovery.

All six Round 2 corrections are included. A verified matching ledger event is the
transaction commit point and recovery performs cleanup only; an eventless intent is
rolled back from authenticated preimages and a retry must use the full authenticated
`apply` path. The final repository gate passes with 938 tests.

## Base and final commits

- Contract release base: `c35bff6085a2f9ba4b9c4c97989eeaae6fbb5be0`.
- Required Round 2 master revision: `5b2c7f4840bf5da24b478c45bd19f3c6609e3c73`.
- Effective implementation base after merging master:
  `334b6492b72c7f32f4d9cf3e54dc96792677e29e`.
- Final reviewed implementation commit:
  `f5e55ff674bcbc063fdd2b901bad76b45dbcd10c`.
- The report artifacts are committed after the implementation snapshot, so their
  bookkeeping commit does not change the reported implementation tree.

## Files changed

- `src/workctx/domain/transactions.py`
- `src/workctx/transactions/__init__.py`
- `src/workctx/transactions/engine.py`
- `src/workctx/transactions/errors.py`
- `src/workctx/transactions/ledger.py`
- `src/workctx/transactions/models.py`
- `schemas/transaction-proposal.schema.json`
- `schemas/audit-event.schema.json`
- `tests/workspace/fixtures/positive/transaction-proposal.json`
- `tests/workspace/fixtures/positive/audit-event.json`
- `tests/workspace/fixtures/negative/transaction-proposal-operations.json`
- `tests/workspace/fixtures/negative/audit-event-hash.json`
- `tests/transactions/__init__.py`
- `tests/transactions/support.py`
- `tests/transactions/test_contracts.py`
- `tests/transactions/test_engine.py`
- `tests/transactions/test_failures.py`
- `tests/transactions/test_ledger.py`
- `tests/transactions/test_path_security.py`
- `tests/transactions/test_preflight_traversal.py`
- `tests/transactions/test_recovery_crash_windows.py`
- `tests/transactions/test_recovery_d031.py`
- `tests/transactions/test_recovery_integrity.py`
- `tests/transactions/test_results.py`
- `docs/reference/transactions.md`
- `.agents/work-orders/WP-300-transaction-engine/report.md`
- `.agents/work-orders/WP-300-transaction-engine/report.json`

All changed files are inside the amended `allowed_paths`. No frozen domain file,
forbidden adapter, projection, validation, CLI, or MCP file was modified.

## Behavior implemented

- Closed Pydantic proposal/audit models and matching Draft 2020-12 schemas, including
  strict context paths, durable references, lowercase suffixes, operation kinds,
  preconditions, postconditions, approval, and the four granted fixtures.
- Deterministic `validate` and `dry_run` with D-025 in-memory composition for final
  artifact identities/manifests, evidence and embedded-observation references, task
  dependencies/blockers, body references, collision detection, and an unchanged strict
  post-apply WP-220 whole-workspace gate.
- Atomic `apply` under the context lock using public staging/fenced-append primitives:
  fence checks, write-ahead intent, ordered write/move/delete execution, postcondition
  validation, audit append, intent finalization, and lock-safe cleanup.
- ADR 0010 compact JSONL hash-chain creation and verification at
  `99_meta/audit/ledger.jsonl`, including zero-revision genesis, tamper detection,
  exact-replay idempotency, reused-ID conflicts, and ledger summaries.
- Typed apply receipts containing committed revision, applied targets, audit event
  ID/hash and references, plus projection freshness/staleness details for WP-310 and
  WP-330.
- Post-commit projection rebuild/invalidation handling that preserves a committed
  transaction even when construction, fencing, rebuild, or invalidation fails.
- D-031 recovery: full-ledger verification, exact intent/event matching anywhere in
  the chain, cleanup-only completion after a verified event, eventless preimage
  rollback only, recovery audit provenance through the reserved system actor, and
  idempotent crash-window replay without a second event.
- Periodic lock heartbeats and fence-loss normalization around unbounded apply and
  recovery phases, including stager construction, ledger work, inspection,
  finalization, and projection work.
- Contractual failure injection for mid-sequence failure, takeover, ledger tampering,
  projection failure after commit, recovery finalizer failure, long-running heartbeat,
  move/delete rollback, and post-rollback-event replay.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | Passed | `All checks passed!` |
| `uv run ruff format --check .` | Passed | `318 files already formatted` |
| `uv run mypy src` | Passed | `Success: no issues found in 64 source files` |
| `uv run pytest` | Passed | `938 passed in 246.85s (0:04:06)` |
| `uv run python -c "import json; from pathlib import Path; from jsonschema import Draft202012Validator; root=Path('.agents'); schema=json.loads((root/'plan/initial/agent-report.schema.json').read_text(encoding='utf-8')); report=json.loads((root/'work-orders/WP-300-transaction-engine/report.json').read_text(encoding='utf-8')); Draft202012Validator(schema).validate(report); print('WP-300 report.json validates against agent-report.schema.json')"` | Passed | `WP-300 report.json validates against agent-report.schema.json` |
| `uv run pytest tests/test_plan_contracts.py -q` | Passed | `4 passed` |
| `git diff --check` | Passed | Exit code 0; no output. |

## Assumptions and decisions

- The durable verified audit event is the commit point. Its presence selects cleanup
  only; its absence selects preimage rollback only, regardless of the caller's requested
  recovery strategy.
- `RecoveryResult.strategy` preserves the requested strategy while its outcome reports
  what D-031 actually performed; eventless `complete` therefore reports `rolled_back`.
- A retry after an eventless rollback requires a new proposal/revision and runs through
  the full authenticated `apply` pipeline.
- D-025 preflight composes only transaction-touched state in memory. Untouched global
  graph/cycle consistency remains the responsibility of the strict WP-220 post-apply
  validation gate.
- External durable-reference placeholders receive structural checks during preflight;
  workspace-owned references receive composed existence/type/context checks.

## Contract deviations

None.

## Security and migration considerations

- Canonical writes occur only through WP-200/WP-201 primitives while the fenced context
  lock is held; the engine does not write canonical files directly.
- Ledger and target path validation rejects traversal, control characters, Windows
  device aliases, case/trailing/ADS aliases, and non-regular ledger targets.
- Ledger events contain deterministic metadata and hashes, not raw evidence payloads or
  secret values; diagnostics identify secret locations without echoing values.
- Projection failure is explicitly non-transactional after commit and is durably marked
  stale where possible; it never rolls back or erases the committed canonical result.
- No migration is required. A new ledger starts at revision zero, and D-031 recovers
  WP-201 intents without extending their durable format.
- Tests used only fictional temporary workspaces. No external system or private
  workspace was mutated.

## Unresolved issues

None.

## Recommended next action

Implementation lead: inspect commit `f5e55ff674bcbc063fdd2b901bad76b45dbcd10c`,
independently rerun the required gate, and integrate WP-300. WP-310 and WP-330 can then
consume the exported apply/recovery result interfaces.
