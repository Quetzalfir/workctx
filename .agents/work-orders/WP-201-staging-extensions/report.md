# Worker report: `WP-201-staging-extensions`

## Status

`completed`

## Summary

Extended the canonical filesystem staging protocol with typed, preimage-preserving move and
delete operations; kind-aware inspection, completion, rollback, and audit finalization; and a
fenced copy-on-write line append primitive that works while a valid intent remains active. The
delivery preserves legacy schema-version-1 replacement intent bytes, rejects retry-time path
redirection and stale holders, documents the extended protocol, and adds 68 focused tests while
leaving every pre-existing filesystem test file unchanged.

## Base and final commits

- Actual checked-out base: `0f99073e926bcbe5b8405895bac0bc3a80edc409`
- Contract-declared base: `PENDING-WP201-BASELINE`
- Reviewed implementation: `abbbb0c007f553f88ce6afa72f378ffb2fc04a7c`
- The report artifacts are committed after the reviewed implementation snapshot;
  `final_commit` in `report.json` identifies that implementation snapshot.

## Files changed

- `src/workctx/adapters/filesystem/staging.py`
- `src/workctx/adapters/filesystem/__init__.py`
- `tests/filesystem/test_staging_extensions.py`
- `docs/reference/canonical-store.md`
- This report and `.agents/work-orders/WP-201-staging-extensions/report.json`

## Behavior implemented

- Added typed `StagedMove`, `StagedDelete`, and `IntentTargetKind` APIs. The existing
  `StagedReplacement.prepare(..., writes=...)` parameter and all existing replacement APIs keep
  their signatures and behavior.
- Kept intent schema version 1. A five-field target without `kind` remains a replacement and
  serializes byte-for-byte identically; move and delete use strict extended shapes with mandatory
  preimage backups.
- Applied moves and deletes in intent order with same-volume checks, atomic `os.replace`/unlink,
  bounded `PermissionError` retries, per-attempt hashes, plain-parent path revalidation, and a
  final nonce fence. Existing move destinations are refused under the cooperative writer lock.
- Extended recovery inspection, successor completion, reverse-order rollback, resumable inverse
  operations, and all three audit finalizers to classify and preserve move/delete recovery state.
- Added `atomic_append_line_bytes`: it validates one complete LF-terminated byte line, permits a
  structurally valid active intent, rejects malformed/unsafe intent state, creates only safe
  in-boundary parent components, stages and fsyncs the complete postimage, and atomically replaces
  after rechecking the preimage, path, and lock on every retry.
- Documented the schema-v1 vocabulary, state machine, retry and recovery rules, append sequencing,
  runtime artifacts, copy-on-write cost, and the non-cooperating no-clobber race boundary.
- Added failure-injection coverage for per-kind and mixed partial sequences, completion and
  rollback, interrupted inverse operations, finalizer conflicts, retries, takeovers, path-link
  substitution, successor recovery, malformed intents, torn append staging, fsync, and hard links.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run pytest tests/filesystem -q` (baseline) | passed | `133 passed in 21.36s` before worker changes |
| `uv run ruff check .` | passed | `All checks passed!` |
| `uv run ruff format --check .` | passed | `295 files already formatted` |
| `uv run mypy src` | passed | `Success: no issues found in 58 source files` |
| `uv run pytest` | passed | `777 passed in 80.04s` |
| `uv run pytest tests/filesystem -q` | passed | `201 passed in 30.56s` |
| `uv run pytest tests/filesystem/test_staging.py tests/filesystem/test_staging_extensions.py -q` | passed | `100 passed in 19.23s` |
| `uv run pytest tests/filesystem/test_staging_extensions.py -q` | passed | `68 passed in 13.64s` |
| `uv run python -c "... Draft202012Validator(schema).validate(report) ..."` | passed | `WP-201 report.json validates against agent-report.schema.json` |
| `uv run pytest tests/test_plan_contracts.py -q` | passed | `4 passed in 0.07s` after report creation |

Three independent read-only post-fix audits also reproduced the initially identified finalizer,
retry-path, stale-delete, and malformed-intent failures, verified their fixes, and reported no
remaining blocking finding. One audit independently reran the full suite with 777 passing tests.

## Assumptions and decisions

- The lead-owned `0f99073` activation commit is the intended WP-201 baseline despite the
  unresolved placeholder in `contract.json`; no worker change was made to the contract.
- Missing `kind` means replacement. New replacements continue emitting the legacy five-field
  shape, while extended replacement shapes are rejected rather than normalized silently.
- Append is copy-on-write because ADR 0006 requires every canonical write to use a fsynced
  same-volume temporary file and atomic replacement. This is linear in ledger size, which ADR
  0010 accepts until rotation becomes a measured need, and it avoids mutating out-of-bound
  hard-linked inodes.
- Move destination absence is checked at prepare and before every retry under the exclusive
  context lock. ADR 0006 does not eliminate the final check-to-`os.replace` race against a
  non-cooperating process; Phase 1 adds no platform-specific atomic no-clobber API.
- Same-zone symlink or junction parents are unsupported for move, delete, and append mutation
  paths and fail closed, including substitutions made during retry backoff.

## Contract deviations

- The contract records `PENDING-WP201-BASELINE`; the operator-pinned integrated base used here is
  `0f99073e926bcbe5b8405895bac0bc3a80edc409`.
- The assigned worktree and branch were absent at session start, so the worker created the named
  worktree and `agent/WP-201-staging-extensions` branch from the pinned base before editing.
- The required WP-300 blocker report is absent from the pinned tree. It was read without mutation
  from `agent/WP-300-transaction-engine` at its reported worker revision.
- No objective, architecture, dependency, public replacement behavior, or allowed-path deviation
  was made.

## Security and migration considerations

- All new canonical endpoints are resolved inside permitted zones; move/delete retries and append
  retries revalidate plain parent chains before and after hashing, with the nonce fence last.
- Wrong or stale holders cannot append, move, delete, restore, or accept idempotent absence after
  takeover. Unsafe or malformed active intent state blocks append before target-parent creation.
- Move/delete preimages remain fsynced in transaction-owned backups until verified finalization.
  Append replacement prevents a context hard link from mutating an inode reachable outside the
  context.
- This is an additive intent-schema-version-1 change with exact old-record compatibility and no
  canonical data migration, new dependency, SQLite path, CLI, schema, or domain-model change.
- Fixtures are fictional; no credentials, private data, network service, or external write was
  used.

## Unresolved issues

- The lead should replace or explicitly accept the `PENDING-WP201-BASELINE` administrative
  placeholder during integration. There is no implementation blocker.
- Atomic no-clobber against a process that ignores the context lock remains outside the ADR 0006
  Phase 1 guarantee and is documented rather than hidden.

## Recommended next action

Implementation lead: review `abbbb0c007f553f88ce6afa72f378ffb2fc04a7c` plus the report commit,
rerun the gate independently, integrate WP-201, and resume WP-300 against the new public
filesystem primitives.
