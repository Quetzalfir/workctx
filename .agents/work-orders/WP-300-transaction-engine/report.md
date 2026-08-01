# Worker report: `WP-300-transaction-engine`

## Status

`blocked`

## Summary

WP-300 stopped before implementation because the integrated WP-200 public API cannot
perform the audit-ledger write required by ADRs 0006 and 0010. A successful
`StagedReplacement.apply()` deliberately leaves `98_state/staging/intent.json` durable
until the audit event is written, but the only public arbitrary-byte write primitive,
`atomic_replace_bytes()`, refuses every write while that intent exists. Directly appending
the ledger is forbidden by the work order, and folding the ledger into the original intent
would deviate from the specified replace-then-audit-then-finalize sequence. The required
`99_meta/audit/` parent also cannot be created through a WP-200 public primitive.

No implementation files were changed. The pinned baseline passes the complete validation
gate with 707 tests.

## Base and final commits

- Pinned base: `55bc43ae7b118f15f131ed04df60e8cbf42f3129`
- Final implementation revision: `55bc43ae7b118f15f131ed04df60e8cbf42f3129`
  (no implementation commit; stopped at the contractual primitive-gap condition)

## Files changed

- `.agents/work-orders/WP-300-transaction-engine/report.md`
- `.agents/work-orders/WP-300-transaction-engine/report.json`

## Behavior implemented

None. The worker did not implement a partial transaction engine because every apply path
must obey the same audit and recovery protocol, and the contract explicitly requires a
coordination request rather than an adapter workaround.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | Passed | `All checks passed!` |
| `uv run ruff format --check .` | Passed | `289 files already formatted` |
| `uv run mypy src` | Passed | `Success: no issues found in 58 source files` |
| `uv run pytest` | Passed | `707 passed in 81.51s` |
| `uv run pytest tests/test_plan_contracts.py -q` | Passed | `4 passed in 0.09s`; final rerun: `4 passed in 0.08s` |
| `git diff --cached --check` | Passed | Exit code 0; only the two allowed report paths are staged |

## Assumptions and decisions

- ADR 0010 and `docs/reference/canonical-store.md` are interpreted literally: the audit
  event is written after the canonical replacement sequence, while the intent remains
  durable, and before intent finalization.
- Including `ledger.jsonl` as the last target of the original intent was rejected as an
  unapproved semantic deviation. It also does not provide the specified separate
  post-apply audit step needed by recovery and rollback flows.
- Direct `open`, `os.replace`, or directory creation in the transaction layer was rejected
  because the assignment requires canonical writes to compose WP-200 public primitives.

## Contract deviations

- No implementation scope or allowed-path deviation.
- The worktree was absent at assignment time and was created on the required
  `agent/WP-300-transaction-engine` branch from the pinned base.
- The branch's frozen contract copy predates the lead's pin-only commit and still contains
  `PENDING-WAVE3-BASELINE`; the direct assignment and lead checkout identify the pinned
  base above. The contract file was not edited.

## Security and migration considerations

- No canonical data, fixture data, credentials, or external systems were written.
- Refusing a direct ledger append preserves the fence, durability, and audit-ordering
  guarantees instead of weakening them silently.
- No schema or migration change was made.

## Unresolved issues

1. `StagedReplacement.apply()` leaves the intent for post-audit finalization, while
   `atomic_replace_bytes()` raises `RecoveryRequiredError` whenever that intent exists.
   WP-200 exposes no fenced, fsynced, bounded-retry audit write that is legal in this
   interval (`src/workctx/adapters/filesystem/staging.py:341`, `:394`, and `:907`).
2. `99_meta/audit/` is intentionally absent, but both staged and single-file replacement
   require the target parent to exist. WP-200 exposes no context-bound canonical directory
   creation primitive (`src/workctx/adapters/filesystem/staging.py:718` and `:915`).
3. The existing proposal vocabulary includes `move` and `delete_generated`, but
   `StagedWrite` represents only byte postimages. WP-200 cannot express forward deletion or
   atomic move; the lead must either narrow the WP-300 operation contract or extend WP-200.
4. WP-220's public `validate_workspace()` reads the physical workspace and ignores staging;
   it cannot validate a proposed multi-document overlay before replacement. The lead must
   approve a scratch-context strategy or add a public overlay-validation API.
5. WP-210 exposes no durable `mark_stale` API. After a post-commit rebuild failure, the old
   compatible database can remain readiness-compatible even though WP-300 can report the
   immediate result as stale.
6. WP-300 must require ADR 0010 `prev_hash` and `event_hash` fields, but the always-run
   positive fixture at `tests/workspace/fixtures/positive/audit-event.json` uses the old
   `previous_event_hash: null` shape and lacks both required fields. The corresponding
   transaction fixture also encodes the loose Wave 1 proposal shape. Those fixture paths
   are forbidden, so a compliant schema tightening and a green full gate cannot coexist
   without a lead-owned baseline update or an explicit path grant.

## Recommended next action

Implementation lead: reopen WP-200 (and, if the contract requires durable candidate and
projection state, WP-220/WP-210) for a bounded public-API addition. At minimum, provide one
fenced audit-ledger write primitive that is valid while the matching intent remains active,
supports first-use creation of `99_meta/audit/`, applies the ADR 0006 retry/fsync policy,
and works for original-holder and successor-recovery finalization. Explicitly decide the
`move`/`delete_generated` vocabulary, and update or grant ownership of the frozen Wave 1
transaction/audit fixtures. Then rebase or recreate the WP-300 branch on the integrated
dependency revision and return the work order for implementation.
