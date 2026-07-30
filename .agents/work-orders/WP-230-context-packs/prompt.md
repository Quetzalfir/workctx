# Worker assignment: `WP-230-context-packs`

You are the worker assigned to `WP-230-context-packs` in the Work Context OS repository.
You are working in the Git worktree `.worktrees/WP-230-context-packs` on branch
`agent/WP-230-context-packs`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-230-context-packs/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract —
   `docs/reference/projections.md` describes the query APIs that are your data plane.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`.
5. Consume the WP-210 SQLite adapter through its typed query APIs only — no SQL of your
   own, no adapter edits. A missing query shape is a coordination request to the lead,
   not a license to work around it.
6. Deterministic retrieval only: no embeddings, no LLM calls, no randomness; identical
   inputs must produce identical packs.
7. No CLI, MCP, or presentation changes; you ship typed application APIs.
8. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
9. Add per-factor ranking tests and budget edge-case tests; run every validation
   command. A blocker is a valid result.
10. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and results.
    A completion claim without executed command evidence will be rejected.

## Objective

Build `src/workctx/retrieval/`: reference resolution over the projection, typed
traversal with depth control, claim-to-locator source tracing, the doc-03 deterministic
ranking function, and budgeted ten-section context packs with truncation metadata —
serialized against a new hand-maintained `schemas/context-pack.schema.json` with
ADR 0008 positive/negative fixtures, documented in `docs/reference/context-packs.md`.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
