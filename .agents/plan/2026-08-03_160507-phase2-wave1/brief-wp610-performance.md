# Brief: WP-610 — Mutation-path performance pass (C-208)

Codex worker, worktree `.worktrees/WP-610`, branch `agent/WP-610-performance`.
You cannot commit; leave changes uncommitted. Final message = report. `.agents/`
is read-only for you. Read `.agents/status/phase2-candidates.md` section C-208
first — it contains the measured baseline you are fixing.

## Measured baseline (single small-file registration, Windows/NTFS)

~4s fixed cost: 1,232 file opens, 8,618 `Path.resolve` calls, 30 SQLite
`executescript` schema initializations, 133 fsyncs, ~41 lock heartbeat writes
(~40ms each), YAML re-parsing of the same files multiple times per apply.

## Targets (counts, not wall-clock)

Per single-file registration: file opens < 100; final-path resolutions < 300;
exactly ONE SQLite connection + schema initialization per public operation;
heartbeat writes <= 6 per apply; fsync count unchanged or lower but NEVER
remove an fsync that ADR 0006/0007 durability depends on (justify each removal
individually in the report). Multi-file `inbox add` amortizes lock acquisition
and projection refresh across the batch.

## Approach constraints

- Zero public-API changes. Zero behavior changes observable by the existing
  1456 tests (all must pass unmodified — if a test encodes ceremony counts,
  report it, do not weaken it).
- ADR 0006 semantics frozen: the lease must remain fresh within the
  configured staleness bound at all times. If you switch heartbeats to
  piggyback/age-based writes, include the worst-case freshness argument in
  your report (interval, bound, and the step-duration assumption it rests on).
- Caching is allowed ONLY within one lock-held operation (per-operation
  caches created and discarded inside the public entry point). No
  cross-operation global caches — a second process must never see stale state.
- Windows/macOS/Linux behavior stays equivalent; `sys.platform == "win32"`
  narrowing for platform code.

## Instrumentation and tests

Create `tests/perf/test_ceremony_counts.py`: deterministic counters via
monkeypatched `os.fsync`, `io.open`/`Path.open`, `nt._getfinalpathname`
equivalents (count through a small helper you add to the test, not to src),
asserting the targets above with 2x headroom so legitimate small changes do
not flake. NO wall-clock timing assertions (CI runners vary). Mark the module
`pytest.mark.perf` and register the marker if needed within your allowed
paths; if marker registration requires pyproject, report it as a lead task
instead of editing pyproject.

## Do NOT touch

Anything outside: `src/workctx/adapters/filesystem/lock.py`,
`src/workctx/adapters/sqlite/projection.py`,
`src/workctx/transactions/engine.py`, `src/workctx/ingestion/service.py`,
`tests/perf/**`. If the win requires touching staging.py or another module,
STOP and report the blocker with the measured justification.

## Report must include

Before/after ceremony counts from the same measurement method as the
baseline; the freshness-bound argument; every fsync you removed with its
individual justification; full gate results (declare sandbox pytest
limitations explicitly — the lead reruns outside).
