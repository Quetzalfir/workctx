# Worker assignment: `WP-310-inbox-lifecycle`

You are the worker assigned to `WP-310-inbox-lifecycle` in the Work Context OS
repository. You are working in the Git worktree `.worktrees/WP-310-inbox-lifecycle`
on branch `agent/WP-310-inbox-lifecycle`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-310-inbox-lifecycle/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` — doc-08's evidence-safety controls
   and the artifact-manifest schema are your specification.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`.
5. Artifact content is UNTRUSTED DATA: never execute, render, or echo it; quarantine
   fails closed. All mutations compose the WP-200 store and WP-300 engine through
   their public APIs — no direct file writes, no adapter/engine edits.
6. The archive move happens only against a committed WP-300 transaction receipt that
   references the artifact; this is the raw-evidence-retention invariant — no
   exceptions.
7. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
8. Add negative and failure-injection tests per acceptance.md; run every validation
   command. A blocker is a valid result.
9. Before stopping, write `report.md` and `report.json` in this work-order directory,
   following `.agents/templates/work-order/` templates, with exact commands and
   results. A completion claim without executed command evidence will be rejected.

## Objective

Build `src/workctx/ingestion/`: hash-based artifact registration with schema-valid
manifests, content-hash duplicate policy, fail-closed quarantine for suspicious
artifacts, and archive-after-commit that moves originals to 01_processed only with a
committed transaction receipt — idempotent, recoverable, and traceable via artifact://
after archive.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
