# Worker assignment: `WP-120-cli-envelope`

You are the worker assigned to `WP-120-cli-envelope` in the Work Context OS repository.
You are working in the Git worktree `.worktrees/WP-120-cli-envelope` on branch
`agent/WP-120-cli-envelope`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-120-cli-envelope/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract.
4. Work only in the assigned worktree and branch.
5. Modify only `allowed_paths`; never modify `forbidden_paths`. You consume
   `services/contexts.py`, `validation/workspace.py`, and the model enums through their
   existing public interfaces only — if the envelope seems to require changing them, that
   is a blocker, not a refactor.
6. The exit-code mapping in the contract is a lead decision — implement it exactly; do not
   re-map codes per your own judgment.
7. `errors.py` may gain new classes; existing classes must not change (services import
   them).
8. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
9. Do not expand scope: no business commands, no user-level registry, no --strict.
10. Add tests for every behavior with split stdout/stderr assertions; run every validation
    command in the contract.
11. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and results.
    A completion claim without executed command evidence will be rejected.

## Objective

Build the CLI presentation boundary of doc-04: a shared result envelope
({ok, command, context_id, result, warnings, errors, meta{schema_version, duration_ms}},
result always an object), clean stdout in JSON mode with stderr diagnostics, the
lead-decided exit-code table behind a single top-level exception boundary, the
context-resolution shell (--context option, ancestor discovery, clear step-4 failure,
documented step-3 seam), all existing commands rewired through it, an envelope JSON Schema
with ADR 0008 fixtures, and rewritten CLI tests.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
