# Leader review: `WP-200-canonical-store`

## Decision

`accepted`

## Contract compliance

- Merge base `a066d87` — one commit past the contract's `cf9ebab`; that commit is the
  lead's own pin-metadata commit, so the administrative deviation the worker documented
  is accepted (no code differences in scope). Delivery `d664f73`, report `33648d7`.
- Changed-path audit: 18 files, all inside `allowed_paths`; frozen parent
  `adapters/__init__.py` untouched; no schemas/, cli, presentation, or domain edits.
- Frozen four public signatures in `services/contexts.py` verified unchanged by diff;
  the file now routes `_write_context_config` through the new canonical serializer.

## Diff review

- `adapters/filesystem/`: serialization (ADR 0005 emitter + hand-edit detection), store
  (zone-aware typed read/write, `_paths.py` boundary enforcement), lock (full ADR 0006:
  nonce identity, atomic heartbeat preserving nonce, stale archival, fencing), staging
  (write-ahead intent with fsync ordering, bounded PermissionError retries, recovery
  inspection, verified preimages enabling partial rollback), registry (platformdirs,
  API-only).
- Test depth matches the hardened ADR exactly: single-winner contention, takeover
  archival races, old-holder heartbeat/release rejection after takeover, mutation guard
  during heartbeat temp writes, mid-sequence takeover detectability, intent retained
  until explicit post-audit finalize (correct ADR 0006/0010 sequencing for WP-300),
  rollback recreating consumed postimages.
- Serializer golden tests pin byte format; re-serialization identity verified.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| frozen-signature diff on services/contexts.py | pass | public API unchanged |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 226 files |
| `uv run mypy src` | pass | 38 source files, strict |
| `uv run pytest` | pass | 484 passed on the branch |

## Findings

- The intent-finalize-after-audit API shape anticipates WP-300's sequencing correctly.
- Registry stays API-only; step-3 wiring is lead work at Wave 2 close.

## Required revisions

None.

## Integration notes

- Integrated third (after resequenced WP-220); Wave 2 close pends WP-230 delivery plus
  lead wiring (index rebuild, --strict, registry step 3, SQLite FreshnessProbe).
