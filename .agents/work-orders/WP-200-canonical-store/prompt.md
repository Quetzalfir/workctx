# Worker assignment: `WP-200-canonical-store`

You are the worker assigned to `WP-200-canonical-store` in the Work Context OS repository.
You are working in the Git worktree `.worktrees/WP-200-canonical-store` on branch
`agent/WP-200-canonical-store`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-200-canonical-store/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract — ADRs 0005, 0006, and
   0009 are your implementation specification; deviations from them are blockers, not
   judgment calls.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`.
5. The four public signatures in `services/contexts.py` (`initialize_context`,
   `load_context_config`, `resolve_context_root`, `slugify_context_id`) stay frozen; you
   may change that file's internals only.
6. Use `workctx.domain.frontmatter` for parsing — do not write a second parser. Import
   domain models from the integrated tree; do not modify them.
7. You own the `98_state/` runtime names `lock.json`, `staging/**`, `backups/`; WP-210
   (parallel) owns `index.sqlite3*`. Never touch the SQLite adapter or its paths.
8. No CLI or presentation changes; the registry is API-only this wave.
9. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
10. Add tests for every behavior, including the failure-injection cases in acceptance.md;
    run every validation command. A blocker is a valid result.
11. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and results.
    A completion claim without executed command evidence will be rejected.

## Objective

Build the canonical filesystem adapter (`src/workctx/adapters/filesystem/`): the ADR 0005
byte-deterministic serializer with hand-edit detection, a typed zone-aware CanonicalStore
with path-boundary enforcement, the complete ADR 0006 lock (nonce identity, atomic
heartbeat, stale takeover, fencing) and staged atomic replacement (write-ahead intent
journal, Windows PermissionError retry, recovery inspection), plus the user-level context
registry API (doc-04 step 3) — and route `_write_context_config` through the serializer.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
