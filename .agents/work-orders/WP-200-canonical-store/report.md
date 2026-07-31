# Worker report: `WP-200-canonical-store`

## Status

`completed`

## Summary

Implemented the canonical filesystem adapter required by ADRs 0005, 0006, and 0009: a
byte-deterministic serializer, typed zone-aware `CanonicalStore`, nonce-fenced context lock,
durable staged replacement and recovery APIs, and a concurrency-safe user-level context
registry. The private context bootstrap writer now uses the canonical serializer, the owned
`98_state/` layout is documented, and failure-injection and boundary tests cover the contracted
behaviors.

## Base and final commits

- Actual checked-out base: `a066d87b6510cb97932021b25d7635f3c326bef5`
- Contract-declared base: `cf9ebabf10d565bb0eef0d8f686ebada3cdd34ab`
- Reviewed implementation: `d664f73cecc5e331b04b5fa594c5f729a9eb0e46`
- The report artifacts are committed after the reviewed implementation snapshot;
  `final_commit` in `report.json` identifies that implementation snapshot.

## Files changed

- `src/workctx/adapters/filesystem/__init__.py`
- `src/workctx/adapters/filesystem/_paths.py`
- `src/workctx/adapters/filesystem/lock.py`
- `src/workctx/adapters/filesystem/registry.py`
- `src/workctx/adapters/filesystem/serialization.py`
- `src/workctx/adapters/filesystem/staging.py`
- `src/workctx/adapters/filesystem/store.py`
- `src/workctx/services/contexts.py`
- `tests/filesystem/__init__.py`
- `tests/filesystem/fixtures/canonical-entity.md`
- `tests/filesystem/test_lock.py`
- `tests/filesystem/test_registry.py`
- `tests/filesystem/test_serialization.py`
- `tests/filesystem/test_staging.py`
- `tests/filesystem/test_store.py`
- `docs/reference/canonical-store.md`
- This report and `.agents/work-orders/WP-200-canonical-store/report.json`

## Behavior implemented

- Added deterministic UTF-8/LF YAML, JSON, and Markdown serialization with declared-field
  ordering, recursively sorted free-form mappings, ADR 0009 null/omit behavior, non-deterministic
  input rejection, shared frontmatter parsing, golden bytes, and exact hand-edit detection.
- Added a context-bound `CanonicalStore` for typed context configs, entities, tasks, and artifact
  manifests, with explicit zone/suffix/context-ID checks and containment enforcement across
  traversal, symlinks, junctions, nested contexts, and cross-zone links.
- Implemented the ADR 0006 writer lock with exclusive creation, random nonce identity, atomic
  heartbeat, stale detection, preserved takeover evidence, fencing, bounded sharing-violation
  retries, and a crash-recoverable file-only mutation guard for `lock.json` operations.
- Implemented durable transaction-local postimages and preimage backups, a fsynced write-ahead
  intent, ordered atomic replacement, per-attempt source/target/fence verification, explicit
  completion and rollback recovery, audit-gated finalizers, and read-only recovery inspection.
- Added a platformdirs user registry with sorted/idempotent APIs, explicit active selection,
  atomic writes, duplicate-key rejection, crash-released advisory mutation locking, and hard
  rejection of registry or guard paths that resolve inside a context.
- Routed `_write_context_config` through `dump_yaml_bytes` without changing any of the four frozen
  public signatures in `services/contexts.py`.
- Documented the canonical-store APIs, retry and recovery semantics, registry boundary, and the
  complete WP-200-owned runtime layout without claiming WP-210 SQLite paths.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | passed | `All checks passed!` |
| `uv run ruff format --check .` | passed | `226 files already formatted` |
| `uv run mypy src` | passed | `Success: no issues found in 38 source files` |
| `uv run pytest` | passed | `484 passed in 26.25s` |
| `.venv\Scripts\python.exe -m pytest tests/filesystem -q` | passed | `133 passed in 22.30s` |
| `.venv\Scripts\python.exe -m pytest tests/filesystem/test_lock.py -q` | passed | `29 passed in 6.32s` |
| `.venv\Scripts\python.exe -m pytest tests/filesystem/test_staging.py -q` | passed | `32 passed in 7.51s` |
| `.venv\Scripts\python.exe -m pytest tests/filesystem/test_registry.py -q` | passed | `22 passed in 4.15s` |
| `uv run python -c "... Draft202012Validator(schema).validate(report) ..."` | passed | `WP-200 report.json validates against agent-report.schema.json` |

Two independent read-only audits also finished clean after exercising lock contention, real
subprocess crash recovery, the prior cancellation collisions, staged-recovery boundaries, the
registry boundary, scope, frozen signatures, and the full validation gate.

## Assumptions and decisions

- The operator's explicit assignment authorized execution on the supplied ready branch. The
  checked-out base was the lead-owned activation commit `a066d87`, a direct child of the base
  recorded in the contract; no worker change was made to that activation commit.
- The Codex-managed physical worktree was treated as the assigned worktree because it was on the
  required `agent/WP-200-canonical-store` branch.
- A Lamport bakery protocol using unique choosing/ticket files under owned `98_state/staging/`
  serializes every `lock.json` mutation without making SQLite or a third-party lock service a
  correctness dependency. Per-artifact cancellation markers fail closed during persistent NTFS
  sharing violations and are reaped when filesystem availability returns.
- Transaction-local preimage backups support safe rollback, while the intent remains present
  after replacement until WP-300 records the audit outcome and calls the matching finalizer.
- Directory fsync is best effort on POSIX and a deliberate no-op on Windows, as specified by ADR
  0006. SMB and NFS receive no stronger Phase 1 guarantee.

## Contract deviations

- The actual branch base is `a066d87`, not the contract's `cf9ebab`; the intervening commit is a
  lead-owned Wave 2 activation/status commit. The WP-200 implementation is isolated in
  `d664f73` so the lead can accept the activation baseline or cherry-pick only the worker commit.
- The physical Codex-managed worktree path differs from the nominal `.worktrees/WP-200-canonical-store`
  path in the assignment; the required branch was used.
- No objective, architecture, frozen-signature, dependency, or worker-owned path deviation was
  made.

## Security and migration considerations

- Canonical and runtime paths fail closed on traversal, absolute paths, unsafe links, reparse
  points, nested contexts, wrong zones, malformed state, duplicate JSON keys, and registry paths
  inside a workspace. Test fixtures are fictional and no network or secret material was used.
- Lock ownership is nonce-fenced at commit-sensitive points. Retry attempts revalidate the same
  lock and exact preimage/postimage hashes, and stale takeovers preserve the displaced bytes.
- Raw recovery state is inspected without mutation. Invalid or dangling intent leaves report
  `invalid_intent`, and conflicting or missing recovery assets report `recovery_conflict`.
- This is an additive schema-version-1 adapter and runtime layout. It changes no domain model,
  canonical schema, CLI, SQLite projection, or audit representation and requires no data
  migration. `98_state/backups/` remains reserved for the ADR 0007 migration flow.

## Unresolved issues

- The lead should reconcile or explicitly accept the `cf9ebab` versus `a066d87` administrative
  base boundary during integration. There are no implementation blockers.

## Recommended next action

Lead review implementation commit `d664f73cecc5e331b04b5fa594c5f729a9eb0e46` and the report
commit, resolve the administrative base boundary, re-run the full gate, and integrate WP-200 for
WP-210/WP-300 consumers.
