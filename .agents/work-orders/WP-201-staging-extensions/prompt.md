# Worker assignment: `WP-201-staging-extensions`

You are the worker assigned to `WP-201-staging-extensions` in the Work Context OS
repository. You are working in the Git worktree `.worktrees/WP-201-staging-extensions`
on branch `agent/WP-201-staging-extensions`.

This is a bounded follow-up to WP-200: you are extending the staging protocol you (or a
prior worker) delivered, to close the primitive gaps WP-300 correctly blocked on.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-201-staging-extensions/contract.json`, `context.md`,
   and `acceptance.md`, plus the WP-300 blocker report listed in required_reading —
   it defines the exact gaps you are closing.
3. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`. Your scope is the filesystem adapter, its tests, and its
   reference doc — nothing else.
4. Backward compatibility is contractual: every pre-existing tests/filesystem test
   passes unmodified; existing public APIs keep their behavior byte-for-byte.
5. The recovery rigor bar is the existing replace pipeline: move/delete kinds need the
   same failure-injection coverage (mid-sequence failure, completion, rollback,
   mixed-kind sequences).
6. The append primitive is a durable write slot for WP-300's ledger — no chain or
   ledger-format semantics here.
7. Keep all repository artifacts in English. Communicate with the human operator in
   the language configured in `.agents/operator.local.yaml` when present.
8. Run every validation command. A blocker is a valid result.
9. Before stopping, write `report.md` and `report.json` in this work-order directory,
   following `.agents/templates/work-order/` templates, with exact commands and
   results. A completion claim without executed command evidence will be rejected.

## Objective

Extend `src/workctx/adapters/filesystem/staging.py` with move and delete intent-target
kinds (preimage-preserving, atomic, fully covered by recovery inspection/completion/
rollback) and add a fenced, fsynced, bounded-retry append primitive with in-boundary
parent-directory creation that works while a prepared intent is active — preserving
every existing behavior and documenting the extended vocabulary in
docs/reference/canonical-store.md.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
