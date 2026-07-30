# Worker assignment: `WP-220-validation-engine`

You are the worker assigned to `WP-220-validation-engine` in the Work Context OS
repository. You are working in the Git worktree `.worktrees/WP-220-validation-engine`
on branch `agent/WP-220-validation-engine`.

## Mandatory instructions

1. Read `AGENTS.md` at the repository root.
2. Read `.agents/work-orders/WP-220-validation-engine/contract.json`, `context.md`, and
   `acceptance.md` in this work-order directory.
3. Read every file listed under `required_reading` in the contract; doc-03's Validation
   rules section is your rule catalog.
4. Work only in the assigned worktree and branch; modify only `allowed_paths`, never
   `forbidden_paths`.
5. The consumed interface is frozen: `validate_workspace(root) -> ValidationReport`,
   the report's `.ok/.errors/.warnings`, and issue fields `severity/code/message/path`
   as read by `src/workctx/cli.py` and the presentation layer (read them to confirm;
   never modify them). Extend behind that surface.
6. Runtime validation uses the integrated domain models and
   `workctx.domain.frontmatter`; jsonschema stays dev-only (ADR 0008). Do not import
   the SQLite or filesystem adapters — projection freshness goes through your own
   FreshnessProbe protocol with a null implementation.
7. The engine reads workspaces; it never writes them. Repair actions are reported, not
   executed.
8. No CLI changes; --strict semantics live at API level this wave.
9. Keep all repository artifacts in English. Communicate with the human operator in the
   language configured in `.agents/operator.local.yaml` when present.
10. Add one negative fixture per rule; run every validation command. A blocker is a
    valid result.
11. Before stopping, write `report.md` and `report.json` in this work-order directory,
    following `.agents/templates/work-order/` templates, with exact commands and results.
    A completion claim without executed command evidence will be rejected.

## Objective

Rebuild `src/workctx/validation/` into the doc-03 integrity engine: typed-model document
validation, reference integrity (parse, boundary, D-018 vocabulary, resolution),
task-hierarchy and blocks/depends_on cycle checks, claim temporal rules (current-overlap,
supersession acyclicity), preserved structural/secret/path checks with stable codes, a
FreshnessProbe protocol (null impl), API-level strict mode, and a diagnostics reference
doc kept in sync with the emitted codes by test.

## Validation commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

If safe completion requires missing information, forbidden paths, or an architectural
change, stop and report a blocker instead of improvising.
